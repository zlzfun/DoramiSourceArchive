# 媒体库、OSS 持久存储与自动备份

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Key Design Decisions」**原文迁出**(2026-10-21,文档整理波 issue #210),内容按域归档、未改写;现状以代码为准。本域不变量变化时更新对应节,并在 `docs/version-history.md` 记一行。
> 相关:[`ops/oss-storage.md`](../ops/oss-storage.md) · [`ops/oss-local-fallback.md`](../ops/oss-local-fallback.md) · [`ops/storage-backups.md`](../ops/storage-backups.md)

## 媒体库（图床，media store）

正文外链图片的本地缓存层（2026-07 图床波，推翻早前「外链直连、不代理」决策）。**归档正文里的原始图链从不改写**（档案忠实性、导出契约不变）——显示层统一经 `GET /api/media/proxy?url=` 取图，`src/services/media_store.py` 负责「URL → 本地缓存文件」：`MediaAssetRecord` 一行一 URL（主键 `url_hash=sha256(url)`），落盘按 `content_hash=sha256(字节)` **跨 URL 内容去重**（`data/media/{hash[:2]}/{hash}{ext}`，删除文件前需查引用）。供给三径共用 `get_or_fetch`：代理端点命中即回文件（`Cache-Control: immutable`）、未命中**即时下载**、抓取入库后 `schedule_media_prefetch`（挂在 `run_fetcher_with_tracking`，fire-and-forget）**随文预取**新文章图链。下载一律带**推导的站内 Referer**（`_referer_for`：`i.qbitai.com` → `https://qbitai.com/`，剥 i./img./cdn./mmbiz 等子域前缀）——防盗链 CDN 无 Referer 即 403，2026-07-20 实测 qbitai 403→200、一举消掉当时失败量的大头。防护：仅 http(s) + SSRF 拦截（环回/私网/链路本地拒绝；**豁免 198.18.0.0/15 fake-ip 段**——本机代理 DNS 接管时一切域名解析到该段，实测误杀后修正）+ 流式大小上限 + **魔数嗅探**（防把 CF 挑战页缓存成图）；失败行是负缓存（`status=failed`，退避窗口随 fail_count 放大，封顶一天；`force=True` 绕过冷却供定点重抓）。降级三层：后端失败 302 回源 → 前端 `ReaderMarkdown` onError 回退原链直连 → 裂图占位；`[media] enabled=false` 时 `media_store` 为 None，全链路退回外链直连。

## OSS 持久媒体（issue #92）

：`[oss]` 为图片和生成音频分别选择 local/oss，默认 local；未启用时无关云参数不影响部署。OSS 上传核验后才登记业务状态，冷文件从私有桶恢复并验 SHA-256；Archive Sync 仍走 ECS 鉴权 API，`object_blobs` 是节点私有位置索引。自动缓存回收按容量目标/最短驻留时间执行，跨进程读取租约保护在途响应，完整远端校验后才删本地副本，不删云端对象。生产使用同地域内网 Endpoint 与 ECS IMDSv2；SQLite、TTS 回执留本地。`scripts/migrate_media_oss.py` 默认 dry-run，真实迁移/恢复/手动回收需 `--apply --offline`。见 [`docs/ops/oss-storage.md`](../../docs/ops/oss-storage.md)。

## 自动备份（默认关闭）

：独立 `[backup]` 将 SQLite 一致性快照、付费 TTS 回执与本地媒体打包到私有目录或 OSS `backups/*`；已有远端媒体列为显式恢复依赖。后台维护不依赖 collector 角色，多 worker 文件锁防重；管理面「存储与备份」显示状态。`scripts/storage_backup.py` 提供创建/校验/下载/离线恢复，恢复需已知哈希和空目录；不自动授予备份权限、不自动清理云端。配置与演练见 [`docs/ops/storage-backups.md`](../../docs/ops/storage-backups.md)。

## 存量策略（2026-07-20 拍板）

：生产**只做随文预取，不跑全量回填**——回填对同批域名突发易触发反爬，且死链各吃满超时导致极慢；`POST /api/admin/media/backfill` 端点保留作脚本化应急通道，**前端入口已撤**。存量补录改走**媒体热点图**（运维管理 → 内容 → 媒体库，`MediaHeatmap.jsx`）：GitHub 式 53×7 逐日格阵，格子深浅 = 当日图片缓存覆盖率（`--heat-0…4`，取 accent 靛明度阶，不引第二饱和色系），右上角三角 = 当日有失败（深浅与异常两条正交通道）；点格开抽屉（`ledger-drawer` 语法）看当日逐篇明细 + 失败 URL 与原因（据此区分反爬 / 死链），每篇可**单篇定点重抓**（无突发压力）。数据现算不落表（`GET /api/admin/media/heatmap|days/{date}`：正文提图链 → 按 `url_hash` 批查 → cached/failed/pending 三态），当前归档规模亚秒级，规模涨了再加物化列。Archive Sync v2 已支持媒体 manifest 与鉴权二进制传输；接收节点按哈希验证并落本地副本。
