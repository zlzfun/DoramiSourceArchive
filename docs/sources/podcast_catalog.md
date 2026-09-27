# 精选播客目录（欧研观澜样本）

## 结论

目录中的 37 个节目可以复用 Dorami 已有的 `SourceConfigRecord(source_type=podcast)`
与 `generic_podcast_rss` 接入，不需要为 Spotify、Simplecast、Libsyn、Fireside、Transistor、
Megaphone、小宇宙和 Buzzsprout 等平台各写一个抓取器。原 36 源于 2026-09-03 验证；
验证日期现为逐源字段，新增/复验单个节目不会虚假刷新其它节目：

- 35 个 feed 返回可解析的 RSS/Atom，且至少一个单集含音频 enclosure；
- `Voices from DARPA` 的 Apple 目录仍指向 Blubrry feed，但当前网络 TLS 握手 EOF，标为
  `blocked`，节点仍保留供后续复验；
- `Inside AI` 的 feed 可用，但最新单集停在 2025-01，保留在扩展档观察；
- `Latent Space`、`20VC` 等历史 feed 接近或超过 10 MiB，目录参数统一设置 20 MiB
  响应上限和每轮 20 集，避免无界响应与首次全量处理。
- `The AI Native Dev` 于 2026-09-24 验证，当前 RSS 同时提供音频 enclosure 和
  HTML/JSON/SRT/VTT 发布方逐字稿候选；以 `incubating` 身份进入独立启用的观察任务。

目录单一事实来源为 `src/services/podcast_catalog.py`。每个条目记录稳定 source ID、feed、
发布方、语言、主题、首发档位、验证状态和最近单集日期。这里仅登记公开分发元数据，不复制
内部平台的 AI 摘要或逐字稿。

## 导入与上线

应用启动时会幂等安装全部 37 个条目，使全新部署的节点管理不再缺少 Podcast。公共播客与
内置文章节点一样始终可供采集任务选择，不再设源级“启用采集”开关；只有加入启用的采集任务后
才按任务 Cron 运行。安装不会自动订阅给任何用户，也不会直接触发 ASR 或 TTS。重复启动只补充
目录新增项，不覆盖管理员对已有节点的名称和配置；Feed 健康状态只用于源审查。

命令行工具用于预览目录或执行选择性运维。默认命令只预览，不写库：

```bash
PYTHONPATH=src uv run python scripts/import_podcast_catalog.py
```

小批导入做内容质量观察：

```bash
PYTHONPATH=src uv run python scripts/import_podcast_catalog.py \
  --apply \
  --source podcast_latent_space \
  --source podcast_semianalysis_weekly
```

手动补齐全部目录条目：

```bash
PYTHONPATH=src uv run python scripts/import_podcast_catalog.py --apply
```

管理员 API 提供相同能力：

- `GET /api/source-configs/podcast-catalog`：目录、验证与当前安装状态；
- `POST /api/source-configs/podcast-catalog/import`：按 ID 幂等导入。

自动安装和手动导入默认都是 `update_existing=false`。目录中的所有源都会进入节点管理；
`ingest_status` 只报告 Feed 健康，不作为准入门槛。所有新源标记为 `incubating`，可先立即抓取
检查标题、日期、show notes、封面、时长与重复率，再扩大采集。可在「采集任务」中像博客节点
一样选择多个节目、分别设置单次上限，并由任务的统一 Cron 定时运行。任务保存逻辑播客源 ID，
执行时读取最新 SourceConfig 并解析为共用的 `generic_podcast_rss` 执行器；Feed URL 和源身份
始终以 SourceConfig 为准，运行开关只存在于采集任务。节点运行史直接使用逻辑播客源 ID。
采集任务只更新 feed/单集元数据，
不会直接调用 ASR 或 TTS。`The AI Native Dev` 是显式例外：目录安装同时幂等维护一个首次创建
即启用的观察期采集任务，每 6 小时只增量抓 RSS 元数据；后续保留管理员对任务启停的选择。
它仍不会自动订阅用户，也不会因目录安装自动触发历史 ASR/TTS 回填。

### 自定 Podcast 升格

`The AI Native Dev` 支持一次性的 fail-closed 收养：导入器按 canonical feed URL 查找唯一
存量自定 Podcast；多条同 URL、类型/权威冲突或目标公共源已存在时整笔回滚并要求人工处理。
收养在单事务内建立公共源、迁移文章归属和订阅/未读水位/阅读计量/隐藏配置/采集任务等
source-id 状态，保持 Article 主键及其分析、全文处理、文本/音频产物、收藏、已读与分享引用
不变，最后才删除旧配置。公共源参数保存旧 `entry_id_namespace`，后续同一 GUID 仍命中旧单集
主键。收养时给历史单集加“仅自动候选抑制”标记，防止公共化本身触发历史精品导读 TTS；
管理员手动/读者点播路径不受影响。再次导入幂等，`update_existing=true` 也不会清掉兼容
namespace。

Archive Sync 只在收养完成后把这些单集作为公共内容导出；source payload 不含旧
`owner_username`，私有状态不跨线，保留的 Article 主键保证接收端不会得到第二份单集。

## 与内部博客 RSS 的边界

内部导入的优质博客继续使用 `source_type=rss` / `generic_rss`，播客固定使用
`source_type=podcast` / `generic_podcast_rss`。即使两者来自同一域名，也必须使用不同
source ID；前者产生 `rss_article`，后者产生 `podcast_episode`，从而保持阅读器容器、
播放器、长音频处理资格和后续成本策略相互隔离。

## 权利与衍生内容

公开 RSS 可以支撑节目发现、原音频链接播放与 show notes 聚合，但不自动等于允许重新托管、
全文转录、翻译、改写或发布合成音频。长播客精华仍按 `docs/podcast-wave-plan.md` 的 rights
gate 执行：优先发布方 transcript；没有明确衍生授权时，只做登录态内部辅助或链接回原节目，
不发布新的公开音频/RSS。

发布者逐字稿正文不会在 RSS 采集或 Reader 页面访问时自动下载。外网管理员需显式调用
`POST /api/admin/podcast-transcripts/{episode_id}/ingest-publisher`；该动作只接受 VTT、SRT、
plain UTF-8 text 和 Podcasting 2.0 JSON，并受 `[podcast]` 的 `transcript_max_bytes`、
`transcript_timeout_seconds`、`transcript_max_segments`、`transcript_max_text_chars` 限制。
声明的受支持 MIME 优先；缺失 MIME 时才按扩展名识别，声明了不支持 MIME 或 MIME/扩展名
冲突的候选不会被下载。由于结果会作为公开文本通过 Archive Sync 发布，该入口采用更强的
`derivative_text_allowed + public_distribution_allowed` 权利门禁（前者同时蕴含
`transcript_allowed`），而不是把公开 RSS 当作授权。相同正文重试复用当前不可变版本，正文
变化才新增版本并原子移动发布指针；发布者 URL 只以 SHA-256 记录在 provenance 中。
