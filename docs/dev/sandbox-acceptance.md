# 隔离栈验收(沙箱起服 + Playwright 自查)

> 状态:耐久参考(2026-10-21 自 Claude Code 自动记忆沉淀,文档整理波 issue #210)。目的:不碰真实 dev 库与 8088 / 5173,跑一遍就有截图与数字,再交用户目检。配套技能 `.claude/skills/dorami-sandbox-acceptance`。

## 1. 起栈

- **临时 ini**(scratchpad,用 sed 从主仓 `config/backend.ini` 派生):`[storage] database_url` 指沙箱 sqlite(绝对路径)、`[server] port=8089`(被占顺延)、`[auth] cookie_secure=false`、`[taxonomy] deployment=manual`、`[media] enabled=false`(**验收依赖封面像素 / 图片时要开**,`media_dir` 指 scratchpad——关着时 `/api/media/proxy` 302 回外站,画布被污染、取色静默回落)、`[podcast_artifacts] root_dir` 指 scratchpad。
- **真实库副本**而非空库(看视觉效果必须):`sqlite3 data/cms_data.db ".backup '<scratch>/cms_data.db'"`,再 `DORAMI_CONFIG_FILE=<ini> .venv/bin/alembic upgrade heads`(直起 `api.app:app` 不跑 `ensure_migrated`,带新迁移时不升级会列表 500、登录后白屏)。
- 后端:`DORAMI_CONFIG_FILE=<ini> .venv/bin/uvicorn --app-dir <worktree>/src api.app:app --port 8089`(主仓 `.venv` 可直接跑 worktree 的 src)。前端:`VITE_PROXY_TARGET=http://127.0.0.1:8089 npx vite --port 5174 --strictPort`(不用改 `app.config.json`)。
- 账号:空库首启自动种 `admin/admin`;真实库副本用 `services.accounts.create_user(session,'e2e-reader','…','user')` 建读者并 `complete_interest_onboarding`,否则读者首屏是引导页。
- 分析 / 速读 / 评分要真模型时,库副本里的 LLM 配置是死地址;**把主库密钥拷进副本属凭据搬运,先问用户授权**,授权后在单进程里换配置、`finally` 复原。死地址期间分析会按 1 / 5 / 30 min 重试、第 4 次终止;重排只改 `status='pending', next_attempt_at=NULL`,不能清零 `attempt_count`。

## 2. 播种时会撞的约束

- `PodcastProcessingRecord` running 须带 `lease_owner` / `lease_token` / `lease_expires_at`,retry_wait 须 `next_retry_at`。
- `PodcastTextArtifactRecord` kind=`normalized_transcript` 须 `processing_id` 或非空 `authority_id`;`PodcastTextPublicationRecord.authority_id` 必须等于 artifact 的。
- `PodcastArtifactRecord` 需 `ext`;非 withdrawn 的 digest 音频必须绑定本集当前 `narration_script_zh` 的 artifact id + content_hash(触发器「narration dependency is not current」);provenance≠`tts` 时 `producing_attempt_id` 必须 None。
- `TagRetagJobRecord.taxonomy_version` 外键到 `taxonomy_versions`。
- 本机 dev 库通常没有播客单集:走接口最省事——各账号 `POST /api/reader/sources/{id}/subscribe`(**先订阅后抓** = 新单集全部未读)→ 管理员 `POST /api/source-configs/{id}/fetch` body `{"params":{"limit":5}}`(同步返回,公开 RSS 不花钱)。默认 `processing_enabled=false`,landing 整轮跳过。
- AI 导读类状态沙箱不接模型:用 Playwright `ctx.route` 改响应造出来。

### 本地真实付费调试（2026-11-01 补充）

用户控制每批付费调试：执行前明确是本地沙箱还是现网，并说明对象、模型、最多新提交次数、估算费用、预算上限与授权期限；获准后只执行该范围，完成后报告实际调用、用量、预占和本地记账。已有明确授权不重复询问，追加单集、重提未知任务、提高预算或延长期限须另行确认。模拟回归、读取已有产物与本地检索不产生供应商费用，和真实模型调用分别记录。

三个费用口径不能合并理解：ASR 受本地授权期限、音频额度及 Podcast 人民币预算控制；百炼精品导读 TTS 另有月度/单次人民币预算；普通读者问答的次数与 token 限额不是人民币硬上限。后台全文分析、自动标题翻译也可能调用 LLM，不能由 ASR/TTS 预算推导出总费用上限。云端最终账单与本地 token/费用记录分开对账。

保留本地预览前必须关闭**全部自动付费入口**，不能只写 `article_analysis_enabled=false`：

- 默认令 `[podcast] processing_enabled=false`。播客全文分析由独立 worker 执行，不受普通文章分析开关控制；同一 tick 还会检查精品导读候选。
- 显式写入 `auto_title_translation_enabled=false`、`auto_title_translation_daily_tokens=0`。标题 worker 在 reader 角色也会启动，缺失开关默认开启。
- 停用沙箱采集任务、用户源自动刷新（`user_sources_enabled=false`）和远程同步，保留数据与台账。继续关闭普通文章分析、日报、个人早报和论文自动任务；仅改运行角色不足以阻止全部 LLM 调用。
- 获准测试单次 ASR 时才临时启用处理，阶段限于 `fetch,asr,local_publish`、目标限于 `transcript`，并确保无其它可运行任务。严格限制新供应商提交次数；旧 TaskId 的轮询与下载不等于授权新任务。
- 完成后恢复原 ASR 授权期限，关闭处理与自动任务，再保留阅读预览。对照前后台账并跨至少两个 worker tick 验证无新增自动 LLM 调用；不得通过删除台账或更换回执目录来归零用量。

本轮发现的实际遗漏与处理记录见 [issue #248 验证记录](../archive/issue-248-podcast-qa.md)。

## 3. Playwright 自查

- 登录选择器 `input[autocomplete="username"]` / `input[autocomplete="current-password"]` / `.auth-submit`;登录动画后 `.reader-vrail-btn:has-text("运维")` 进管理台;管理员进阅读器 `button[aria-label="进入阅读器"]`;发现页 `button[aria-label^="发现"]`;添加自定源 `button.reader-disc-add`。
- 管理面像素级断言:表头 `.acct-table.is-fixed thead tr` 36px、`tr.acct-row` 46px、中线差 ≤ 1–2px、行高 diff = 0;`pageerror` / console error 为零(gstatic favicon 404 来自发现页源图标,与业务无关)。
- 拖选 / 遮罩场景用 `page.mouse.move → down → move(steps) → up`,遮罩左下角 `(20, H-20)` 任何面板都盖不到;运维用户抽屉**常驻 DOM**(看 `aside[aria-label$="活动详情"]` 的 `aria-hidden`);hover 浮现的 `.rowact-btn` 物理点击报「outside of the viewport」,入口机制不是被测对象时用 `locator.dispatch_event('click')`。
- 亮 / 暗两主题 + 移动壳各截一组;截图先自己目检一遍再交用户。

## 4. 交付

验收步骤写成可直接复制执行:沙箱地址、账号、要看的页面与预期;提示迁移会升级副本库。用户在当前会话明确放行后进入检视 / PR。
