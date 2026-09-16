# 自动备份与离线恢复

备份独立于图片/音频存储开关。默认关闭，纯本地部署不需要任何 OSS 参数。
开启后后台按 `[backup] interval_hours` 执行，失败会显示在存储状态中，十分钟后重试。
手动命令与后台使用同一实现；文件锁避免多进程同时备份。备份不会调用 ASR、TTS 或其他付费生成接口。

## 一份成功的备份包含什么

| 内容 | 保存方式 |
|---|---|
| SQLite，包括账户、发布状态、对象位置索引和付费任务账本 | `sqlite3.Connection.backup` 在线一致性快照；包含 WAL 中已提交的记录 |
| 百炼 TTS 原始 JSON 回执与已经付费下载的 WAV | 与 TTS 共用 `receipt_root/.lock`；快照与复制期间不会有该目录的新付费任务写入 |
| 快照引用、尚无 OSS 索引的图片和生成音频 | 完整打包，逐文件 SHA-256 和大小校验；缺失或损坏即整次备份失败 |
| 快照引用、已有 OSS 索引的图片和生成音频 | 写入明确的 `external_objects` 依赖清单；文件继续保留在媒体桶 |
| 配置文件、环境变量、程序版本 | 不打包；按原部署方式保管并记录对应发布 tag |

每个归档以 `manifest.json` 开头，记录其余文件的 SHA-256 和字节数。数据库读取结构完整性检查必须通过。
回执目录取当前生效的运行时覆盖；快照再次核对目录一致。`generated` / `succeeded` 任务缺少回执，
或 `succeeded` WAV 哈希不符，备份会失败。TTS 已在执行时，备份跳过这次尝试并按失败重试间隔再试；
备份已经持锁时，新 TTS 调用会短暂等待快照和回执复制完成。普通媒体复制使用读取租约，避免与缓存回收冲突。

**`external_objects > 0` 表示恢复仍需要清单中的 OSS 对象和读取权限。**
不得按“当前业务无引用”删除仍被保留备份引用的远端对象；媒体 GC 必须核对所有保留备份。
备份不会访问图片原站来补缺，也不会把缺少图片/音频的本地备份当作成功。

归档包含私有正文、账户信息和数据库中保存的凭据，以及供应商回执。目录按 `0700`、文件按 `0600` 创建，
不能放入静态资源目录、公开桶或 Git。哈希校验用于发现损坏；已知 SHA-256 要从受保护的备份报告或 sidecar 获取，
不能把攻击者同时提供的归档与哈希当作可信来源。

## 配置

```ini
[backup]
enabled = false
destination = local
interval_hours = 24
local_dir = data/backups
retain_local = 7
minimum_free_mb = 1024
timeout_seconds = 60

# 仅 destination=oss 时需要；与媒体 OSS 配置独立。
bucket = your-private-backup-bucket
region = ap-southeast-1
endpoint = https://oss-ap-southeast-1-internal.aliyuncs.com
prefix = backups/production
credential_provider = ecs_role
ecs_role_name = YourBackupRole
```

所有字段支持 `DORAMI_BACKUP_<大写字段名>`。`enabled=false` 时其余备份参数不会影响启动；
`destination=local` 时不校验无关的 OSS 地址。独立静态凭据模式为 `credential_provider=static`，仅读取
`DORAMI_BACKUP_ACCESS_KEY_ID`、`DORAMI_BACKUP_ACCESS_KEY_SECRET`、`DORAMI_BACKUP_SECURITY_TOKEN`，不从 INI 读取秘密。
生产优先使用 ECS IMDSv2 临时凭据。不会自动继承媒体存储的角色名、桶、前缀或静态密钥。

`prefix` 必须位于独立的 `backups` 顶层前缀。备份身份仅需对应前缀的 HTTPS `oss:PutObject` 和 `oss:GetObject`
（包含 HEAD），无需列桶、删对象、修改桶权限。已有媒体运行角色只有媒体前缀权限时，不能写备份；
策略模板见 [oss-backup-policy.example.json](./oss-backup-policy.example.json)，按实际桶名和备份前缀替换。
需要单独审查并授权备份前缀，或在独立备份进程/实例提供备份身份。本次开发不修改生产 RAM 权限。
单台 ECS 的元数据角色绑定由平台管理；仅在配置中填不同角色名并不会创建第二个可用身份。

上传使用唯一名称与禁止覆盖头，随后检查 HEAD 大小和 SHA-256 元数据；失败保留已完成的本地快照并报告失败。
重试会校验并重传同一份完整归档，不重新生成每十分钟一份的快照，也不轮换已有成功恢复点；
因此云端故障期间最多额外保留一份待上传归档。进程重启后仍从持久化状态继续重试。
如果之前的 PUT 已成功而响应丢失，重试通过 HEAD 校验认领同一个对象。
最近成功时间采用快照实际时点，而非延迟上传的完成时刻；多日故障恢复后，下一次调度检查会立即补拍已到期的新快照。
备份目录或状态文件不可写时，当前进程仍保留安全的失败状态和十分钟退避；状态文件无法落盘时，重启后该内存退避不会保留。
当前使用单文件 PutObject；压缩归档超过 5,000,000,000 字节会明确报告 `backup_requires_multipart_upload`，
保留本地快照，不宣称云端成功。超大本地媒体库应先完成 OSS 迁移，或单独实现经过恢复验证的分卷/分片备份方案。
该限制来自 [OSS PutObject 最大 5 GB](https://help.aliyun.com/zh/oss/developer-reference/putobject)。
完整备份成功后，本地归档按 `retain_local` 保留最新份数，只清理本工具命名的归档与对应哈希文件。云端不会自动删除，
远端保留期/费用需独立配置，不能照搬媒体目录的策略。本地同盘备份能应对误操作，不能应对整盘或整机丢失。
备份需要同时容纳暂存的数据库/本地文件、压缩归档与保留备份；空间不足保留既有备份并失败，不抢先删旧备份。

## 手工创建与恢复演练

```bash
export DORAMI_CONFIG_FILE=/path/to/config/production.ini

# 已显式 enabled=true 时，立即执行一份在线备份；输出只包含状态/哈希/统计。
python scripts/storage_backup.py create
python scripts/storage_backup.py status

# 先从 create 输出或受保护的 .sha256 文件取得 SHA256。
# verify 解包到新的临时目录并校验全部内容、SQLite 完整性，随后删除临时目录。
python scripts/storage_backup.py verify --archive /path/to/dorami-....tar.gz --sha256 SHA256

# 从已知私有 key 下载，需备份读取身份；不列桶、不覆盖已有目标。
python scripts/storage_backup.py download \
  --object-key backups/production/dorami-....tar.gz \
  --target /safe/location/downloaded.tar.gz --sha256 SHA256

# 停止恢复目标的全部 API、worker、调度器；必须是新目录或空目录。
python scripts/storage_backup.py restore --offline \
  --archive /safe/location/downloaded.tar.gz --sha256 SHA256 \
  --target /safe/location/restored
```

`--offline` 是操作者确认，不会替操作者停止服务。工具拒绝覆盖非空目录；校验失败不会留下半成品目标目录。
恢复路径仅允许数据库、按内容哈希命名的媒体及回执；拒绝路径穿越、绝对路径、符号链接、硬链接、重复条目、
未列入清单的文件、缺失条目、大小/哈希不符。默认最多解包 100 GiB，必要时显式设置 `--max-unpacked-mb`。

成功恢复目录形状：

```text
restored/
  database.sqlite3
  receipts/<paid-call-id>.json|wav
  media/<hash-prefix>/<hash>.<ext>
  podcast/<hash-prefix>/<hash>.<ext>
  manifest.json
```

这一步验证并恢复字节，不会覆盖原生产路径、修改线上数据库或自动启动业务。正式启用恢复节点前：

1. 使用与备份匹配的应用版本。将恢复的数据库、回执、媒体文件放到该节点实际配置的目录，或调整该节点配置。
   注意数据库 `app_settings` 中可能保存 `bailian_speech_tts_receipt_root` 运行时覆盖，其优先级高于 INI；
   目录变更须同时处理该覆盖，不能只改 INI。保持回执与数据库成套，禁止将回执目录作为普通缓存清理。
2. 如果 `external_objects > 0`，先保留原桶/地域/索引与读取权限，按 [OSS 维护流程](./oss-storage.md#维护命令)
   执行 `migrate_media_oss.py restore --apply --offline`，验证所有外部对象完整可读。
   需要完全脱离 OSS 时，按 [本地回退手册](./oss-local-fallback.md) 执行
   `restore → check-local → finalize-local → local`，离线期间验证文件并清理对象索引；仅移除 OSS 配置不能恢复缺失对象。
3. 先以停止采集/付费 worker 的状态检查账户、文章、图片、生成音频与发布/撤回结果。
   恢复到历史时点会丢失备份之后的状态；必须核对备份之后的 ASR/TTS 供应商账单和任务，
   防止把历史数据库中不存在的已付费请求再次提交。备份保留时点内的账本/回执，但不能消除时点之后的数据损失。
4. 完成业务验收后再启动调度器和付费任务。记录恢复时间、所用归档哈希、应用 tag 与外部对象核验结果。

## 验证记录

`tests/test_storage_backup.py` 使用真实 SQLite WAL、实际压缩与解包，在隔离临时目录验证：
数据库最新已提交记录、已付费 WAV/回执、未明确完成的付费授权状态、本地图片/音频均能恢复；
远端媒体依赖可见；受保护的空目录恢复与恶意归档拒绝；模拟 OSS 上传/下载/恢复、故障脱敏与本地保留。
这些是实现测试，不代表生产自动备份已启用，也不代表生产备份前缀已授权或已完成真实灾备演练。
