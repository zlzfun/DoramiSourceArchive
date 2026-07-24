# v3.18 互通波:读者反馈 + 管理员公告 + 远程内容同步(设计方案)

> 状态:设计定稿 → 实现中(2026-07-23)。三个独立需求合为一波,因为前两者同属
> 「管理员 ↔ 读者的双向沟通面」,第三者复用归档同步契约、独立成块。

## 需求 1:读者反馈(用户面提交 → 管理面收件)

**定位**:读者有一个轻量入口表达诉求(「希望新增 xxx 源」、bug、建议),管理员在
运维管理里集中查看、标记处理状态并可回复;读者能看到自己反馈的处理进展与回复。

### 数据模型

`FeedbackRecord`(表 `feedbacks`):

| 字段 | 说明 |
|---|---|
| `id` (int PK) | 自增 |
| `owner_username` (idx) | 提交者 |
| `category` (idx) | `source_request` / `bug` / `suggestion` / `other` |
| `content` | 正文,≤ 2000 字 |
| `status` (idx) | `open` / `in_progress` / `resolved` / `dismissed` |
| `admin_note` | 管理员回复/处理备注(读者可见) |
| `created_at` / `updated_at` | ISO 串 |

### API

- 读者面(reader 门控,`/api/reader/feedback`):
  - `POST /api/reader/feedback` — `{category, content}`;内容长度校验;
    防滥用:同一用户单日提交上限 10 条(429)。
  - `GET /api/reader/feedback` — 自己的反馈列表(含 status + admin_note)。
  - `DELETE /api/reader/feedback/{id}` — 撤回自己的、仍为 `open` 的反馈。
- 管理面(admin 门控,`/api/admin/feedback`):
  - `GET /api/admin/feedback?status=&limit=` — 全量列表(含 username),
    响应带 `counts`(按 status 聚合,供角标)。
  - `POST /api/admin/feedback/{id}/status` — `{status, admin_note?}`。

### 前端

- 读者:设置柜新增「反馈与建议」section(通用组;文案不得泄漏内部架构词——
  分类词用「想要新的内容来源 / 问题反馈 / 功能建议 / 其他」)。表单(分类
  segmented + textarea)+ 我的反馈列表(状态 chip + 管理员回复气泡)。
- 管理:运维管理新增第四子页「消息」(`sub: 'engage'`),左右两个 panel 之一为
  **反馈收件箱**:status 过滤 seg + 列表行(用户/分类/时间/正文)+ 行内状态
  流转与回复输入。

## 需求 2:管理员公告(管理面发布 → 读者面横幅)

**定位**:管理员向所有读者发布公告,读者面以顶部横幅呈现,逐用户一次性
(读者点 × 后不再出现,跨设备一致);支持轻量格式:链接、加粗、强调色。

### 数据模型

`AnnouncementRecord`(表 `announcements`):

| 字段 | 说明 |
|---|---|
| `id` (int PK) | 自增 |
| `title` | 可空短标题 |
| `content` | 正文,受限 markdown 子集(`**加粗**`、`[文字](url)`) |
| `level` (idx) | `info` / `accent` / `warning` — 决定横幅配色(取 design token,不允许任意色值,保设计系统一致) |
| `is_active` (idx) | 管理员可下线 |
| `created_by` | 发布者 |
| `created_at` / `updated_at` | ISO 串 |

`AnnouncementDismissRecord`(表 `announcement_dismissals`,复合主键
`(owner_username, announcement_id)`):读者逐条关闭的记录,决定「一次性」语义。

### API

- 管理面:`GET/POST/PUT/DELETE /api/admin/announcements`(+ `POST
  /api/admin/announcements/{id}/toggle`);GET 带每条的 `dismiss_count`
  (多少读者已读/关闭)。
- 读者面:`GET /api/reader/announcements` — 仅 active 且本人未 dismiss;
  `POST /api/reader/announcements/{id}/dismiss`。

### 前端

- 读者:`ReaderTab` 最外层容器顶部横幅带(多条时纵向堆叠,通常仅 1-2 条),
  level → token 配色(info=墨/纸、accent=品牌靛 wash、warning=琥珀 wash),
  右缘 × 关闭。**渲染绝不用 dangerouslySetInnerHTML**:自写 ~40 行受限
  markdown 解析器(`utils/miniMarkdown.jsx`),只识别 `**bold**` 与
  `[text](http(s)://…)`,链接一律 `target="_blank" rel="noopener noreferrer"`,
  其余按纯文本;该解析器公告编辑预览与横幅共用。
- 管理:运维管理「消息」子页第二个 panel 为**公告管理**:发布表单(标题/正文/
  level 三选)+ 实时预览(同一 mini-markdown 渲染)+ 已发列表(active 开关、
  触达计数、删除)。

## 需求 3:远程内容同步(接收方图形化拉取存量后端)

**定位**:多套部署场景,新部署/内网部署的后端,由管理员填另一个存量后端的
地址 + 管理员凭据,把对方的归档内容整体拉过来。**方向为接收方主动拉取**
(只需打通接收方 → 发送方单向网络)。

### 机制(复用归档同步契约 articles-jsonl-v1,发送方零改动)

`src/services/remote_sync.py`:

1. **登录**:`POST {base}/api/auth/login`(admin 凭据)。凭据只在任务内存中
   使用,**绝不落库、不写日志**;KV 里只记 base_url + username。
   Cookie 处理:手工从 `Set-Cookie` 抽会话值、显式带 `Cookie` 头回传——
   远端若开 `cookie_secure` 而我们经 http 访问,httpx 的 cookiejar 会因
   Secure 属性拒发,显式头绕开这一坑(生产实操已验证过该行为)。
2. **测连**(`test_connection`):登录 → `GET /api/runtime`(版本)→
   `GET /api/archive/export/articles.jsonl?limit=1`(契约可用性 + manifest);
   返回远端版本、可达性、样本信息,供界面「测试连接」按钮。
3. **拉取**(`run_sync`,后台 job `remote_archive_sync`):按
   `skip/limit=1000` 翻页拉 export(导出按 `fetched_date asc, id asc` 稳定
   排序,翻页安全),每页原文直接喂本地 `import_archive_sync_jsonl()`
   (校验 checksum/幂等 by id/正文回填全部现成),累计 imported/updated/
   skipped/errors 进 job 进度;页不满即止。支持 `fetched_date_start` 等
   过滤透传 → **增量同步**。
4. **游标**:成功后把本次所见最大 `fetched_date` 与远端地址写入 KV
   `remote_sync:last:{host}`;下次界面默认提供「增量自上次」选项。

### API(admin 门控,与归档导入同理——改写整库)

- `POST /api/admin/remote-sync/test` — `{base_url, username, password}` →
  远端可达性/版本/契约检查结果。
- `POST /api/admin/remote-sync/start` — 同上 + `{mode: full|incremental,
  fetched_date_start?, source_ids?}` → `{status:"accepted", job_id}`,
  前端 pollJob。
- `GET /api/admin/remote-sync/status` — 上次同步 KV + 最近 `remote_archive_sync`
  jobs 列表。

### 前端

设置柜 → 管理组「数据同步」section 内新增**「远程同步」**块(与既有导出/导入
并列):地址/账号/密码表单 → 测试连接(展示远端版本与状态)→ 范围选择
(全量 / 增量自上次 / 自定起始日期)→ 开始同步 → 进度条(pollJob,展示
已拉页数/新增/回填/跳过)→ 结果摘要。密码框绝不回显存储。

## 横切事项

- **迁移**:一条 Alembic revision 新增 `feedbacks` / `announcements` /
  `announcement_dismissals` 三表(remote sync 无新表,走 jobs + KV)。
  `test_migrations` 的零漂移守卫自动覆盖。
- **门控登记**:新端点全部落在既有前缀 `/api/reader/*`、`/api/admin/*` 下,
  中间件前缀表无需扩(需实测确认)。
- **版本**:`3.18.0`(功能波,MINOR),`src/version.py` + `pyproject.toml` 同步。
- **测试**:`test_feedback.py` / `test_announcements.py`(门控、越权、限额、
  dismiss 语义)、`test_remote_sync.py`(httpx.MockTransport 假远端:翻页、
  checksum 错误行、增量游标、Secure cookie 显式头;不打真网)。
- **分工**:设计/schema/迁移/远程同步核心 = 本人;反馈全栈 = Codex;
  公告全栈 = Opus;AdminOpsTab 第四子页骨架与 api.js 端点函数由本人预铺,
  保证两个代理的改动文件不相交。
