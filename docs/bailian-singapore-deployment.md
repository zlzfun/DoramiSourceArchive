# 新加坡百炼播客语音接入

本方案在新加坡 ECS 调用百炼新加坡业务空间，ASR 使用 `fun-asr-2025-11-07`，
TTS 使用 `qwen3-tts-flash-2025-11-27`。它是 PR64 传统 ISI 方案之外的明确选择，
不需要上海 NLS AppKey，也不需要为本次样本另购 OSS Bucket。

## 地域与接口

- ASR：`POST /api/v1/services/audio/asr/transcription`，异步提交后只轮询原 TaskId。
  显式发送 `parameters.channel_id=[0]`，每次一个文件；最大 12 小时。
  [官方 HTTP 接口及新加坡域名](https://help.aliyun.com/zh/model-studio/fun-asr-recorded-speech-recognition-http-api)。
- TTS：`POST /api/v1/services/aigc/multimodal-generation/generation`，每段至多 600 字符。
  默认按句切到 500 字符以内，下载每段 24 kHz 单声道 PCM WAV 后合并。
  [官方 TTS 接口](https://help.aliyun.com/zh/model-studio/qwen-tts-api)。
- 新加坡地域表示接入与数据静态存储位于新加坡；国际部署的推理资源可调度至中国内地以外地区，
  **不等于推理严格限制在新加坡单一机房**。
  [官方地域说明](https://help.aliyun.com/zh/model-studio/singapore-regional-access-information)。

优先使用控制台展示的业务空间专属域名：
`https://<workspace-id>.ap-southeast-1.maas.aliyuncs.com/api/v1`。
配置只接受该域名形式或旧版 `https://dashscope-intl.aliyuncs.com/api/v1`，拒绝北京/上海地址。

## 开通与配置

在拥有生产 ECS 的阿里云账号下进入百炼新加坡业务空间，开通模型调用并创建专用 API Key。
将权限限定为上述两个模型，并把访问 IP 限定为实际 ECS 出口公网 IP。
不要在截图、日志、工单或 Git 中保存完整 Key。代码只在 API 请求中携带 Bearer，下载结果不携带 Key。

`config/production.example.ini` 与 `config/backend.example.ini` 提供完整 `[bailian_speech]` 默认项。
部署时复制并修改受保护的 `config/production.ini`：

```ini
[bailian_speech]
enabled = true
base_url = https://<workspace-id>.ap-southeast-1.maas.aliyuncs.com/api/v1
account_scope = <account-id>:<workspace-id>
asr_daily_audio_seconds_limit = 1800
asr_entitlement_ends_at = <带时区的未来截止时间>
tts_monthly_budget_minor = 100
tts_per_run_budget_minor = 100
```

这些限制是初次样本的保守额度：ASR 每日 30 分钟，TTS 每月及单次最多 1 元。
正式启用前按实际需求设置；`asr_entitlement_ends_at` 是本地付费授权截止时间，
不是免费资源包到期时间。未填额度、截止时间或凭据时，ASR 不提交新任务。
TTS 预算为零时不允许调用。选择器 `enabled` 需重启生效；默认关闭，保留原 ISI 行为。
已提交 ISI 任务应在切换前处理完，不会被百炼适配器接管。

密钥写入宿主权限为 `600` 的 `.env`，由 Compose 转发 `DORAMI_BAILIAN_API_KEY`。
其余配置读取挂载的 INI。裸机支持对应 `DORAMI_BAILIAN_<字段大写>` 环境变量。
账号范围必须稳定，轮换同一账号 Key 时保留账号范围、地域及模型，可继续轮询和复用回执。

ASR 还需要现有 `[podcast]` 的 `processing_enabled`、`allowed_stages`、转写目标、
月度/单次人民币预算全部就绪。精品导读还需要既有 LLM 配置，逻辑 voice alias 必须与
`[podcast] default_voice_profile` 一致（默认 `narrator_zh`，映射为 `Cherry`）。

## 计费与恢复

2026-09-15 核验的新加坡价格快照：Fun-ASR 每秒 ¥0.00026（每小时 ¥0.936）；
Qwen3-TTS Flash 每万字符 ¥0.733924。这两个模型的新加坡部署无免费额度。
[官方模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)。
价格以云端账单为准；本地按整数分向上取整，属于保守预算记录，不是已出账发票金额。

- ASR 复用 `podcast_budget_reservations` / `podcast_cost_ledger`，输入时长向上取整到秒预占，
  终态按返回用量核对冻结价格。百炼只对判定为语音内容的部分计费，故文件时长与计费时长
  分别记录；`minimum_usage_units=0` 在提交前冻结，旧 ISI 记录默认仍以完整输入为下限。额度或付费授权过期后可排空已提交任务，不能新增付费请求。
- 精品导读 TTS 走当前同步 `PremiumGuideTtsProvider`，使用独立的 `bailian_tts_calls` 账本，
  **与 `[podcast]` 预算分别累计**。总成本评估应包含两者及原有 LLM 成本。
- TTS 按 UTF-8 字节数保守预占字符费用，收到响应后用 `usage.characters` 结算。
  文本、声音、模型、账号及单集均相同的调用复用已下载音频；不重新付费合成。
- 每段 POST 前提交 `authorized` 状态。超时、崩溃或无法确认的响应保持该状态，阻止同集再次提交；
  不得通过删表、换输出目录或更换请求键来“重试”。必须先根据回执及云端用量核实。
- 已保存完整响应的 `generated` 状态只重试音频下载；官方临时 URL 过期后需人工处理，
  不会为了下载失败自动重新合成。`rejected` 已知拒绝不收费，同段也不会自动重提。
- API Key、原文和签名 URL 不进入付费账本。原始响应含临时签名 URL，仅存本地受保护回执目录。
  Docker 强制该目录位于 `/app/data/bailian-speech` 持久卷；备份时与数据库一起保存。
  默认缓存上限 512 MiB，至少保留 1 GiB 磁盘余量；达到限制后停止新增合成。
  暂无自动清理：音频缓存用于避免重复收费，不要单独删除。

结果下载限定为 HTTPS 与新加坡 OSS 域名，逐跳校验重定向并限制字节数。
百炼有时给出 HTTP OSS 链接，适配器只尝试同地址的 HTTPS，不降级明文。
Qwen3 实测 WAV 使用占位长度；仅兼容已验证的标准 PCM 头模式并重写实际帧数，
普通截断 WAV 仍被拒绝。最终音频继续经过现有精品导读的 ffprobe、时长限制和 CAS 流程。

ASR 使用 RSS 发布者音频直链，提交前复核源权限与媒体快照。当前百炼实现没有 ISI 的 OSS 回退：
源站无法被百炼下载时会失败并保留记录，不能偷偷改走上海服务。只有后续样本证明需要中转时，
再增加新加坡私有 Bucket 和明确的下载失败恢复流程。

## 独立样本验证

只在获准的小额预算和专用目录内运行；不要指向正式内容库。脚本会调用真实付费接口。
环境文件应把 `tts_receipt_root` 指向该目录中的 `receipts`，并与样本数据库一并保留。

```sh
DORAMI_CONFIG_FILE=/secure/validation.ini PYTHONPATH=src \
  python scripts/bailian_speech_smoke.py --output-dir /secure/bailian-validation
```

样本包含中英文、技术名词与句间停顿，验证 TTS 分段、合并、原样重放以及 ASR
提交、轮询、标准化转写和费用入账。ASR 使用第一段已生成音频的临时 URL。
输出 `sample.wav`、`sample-text.txt`、`transcript.json`、`report.json` 和本地账本。
超时重跑必须使用同一输出目录；已提交任务保持原 TaskId。

这验证接口和持久化链路，不代表已验证数小时多人播客识别质量，也不代表已部署到正式服务。
正式上线仍按 `docs/release-process.md` 的发布 tag 流程进行，升级前备份数据库及回执目录。
新迁移位于既有单向迁移边界之后，回滚使用升级前数据库备份。

## 2026-09-15 实测记录

已从新加坡 ECS 的独立样本目录调用两个固定版本模型，真实账号和模型权限均可用：

| 样本 | 结果 | 供应商用量 | 本地保守记账 |
| --- | --- | --- | --- |
| 中英文两段 TTS | 合并为 45.52 秒、24 kHz 单声道 PCM WAV | 429 字符，两次调用 | ¥0.05 |
| 第一段音频 ASR | 30.24 秒文件，产生含时间戳的标准化转写 | 28 秒，一次任务 | ¥0.01 |
| 原样重跑 | TTS 音频一致、ASR worker 返回 idle | 没有新增任务或回执 | 没有新增记账 |

WAV SHA-256：`3f4a09bf3628994cae988735caa94c42facfe5e7b3eb56b161baeef29ac9c860`。
按价格快照和供应商用量计算约 ¥0.0388，本地按单次请求向上取整共 ¥0.06，最终账单可能不同。
ASR 中文主体及英文句可用；原稿 `Qwen` 被识别为 `queen`，专有词质量尚需更多真人样本验证。

实验过程中旧账本因“计费秒数少于文件秒数”暂停过一次结算。先备份独立样本库，再迁移并核对原任务，
为该实验记录应用新的有效语音计费规则，审计后恢复原 TaskId；没有第二次 ASR 提交。
应用代码版本、生产业务数据库和正式处理开关均未切换。

本地验证：`pytest tests/ -q` 共 1,869 项通过，其中百炼专项 30 项、数据库迁移 54 项；
前端 `npm run lint`、`npm run build` 通过。没有新增运行时依赖或修改发布版本号。
