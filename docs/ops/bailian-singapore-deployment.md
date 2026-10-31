# 新加坡百炼播客语音接入

本方案在新加坡 ECS 调用百炼新加坡业务空间，ASR 默认使用 `fun-asr`，
TTS 默认使用 `qwen3-tts-flash`（2026-10-30 更新，见下方迁移节）。它是 PR64 传统 ISI 方案之外的明确选择，
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
asr_model = fun-asr
tts_model = qwen3-tts-flash
asr_daily_audio_seconds_limit = 1800
asr_entitlement_ends_at = <带时区的未来截止时间>
tts_monthly_budget_minor = 100
tts_per_run_budget_minor = 100
tts_cache_max_bytes = 1073741824
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

## 旧快照迁移与诊断（2026-10-30，issue #244）

`fun-asr-2025-11-07` 与 `qwen3-tts-flash-2025-11-27` 已列入 2026-10-10 退役名单。
[官方退役公告](https://www.alibabacloud.com/en/notice/model_studio_notice_of_retirement_for_selected_legacy_models_7d9)
给 TTS 推荐的 CosyVoice 涉及不同适配、音色和价格，本次不直接替换成该模型。
现行 [Qwen-TTS HTTP 文档](https://help.aliyun.com/zh/model-studio/qwen-tts-api) 仍提供 `qwen3-tts-flash`，
[Fun-ASR HTTP 文档](https://www.alibabacloud.com/help/en/model-studio/fun-asr-recorded-speech-recognition-http-api)
使用 `fun-asr`，与本项目既有请求体和返回处理兼容。默认改用这两个名称，不改端点、Cherry 音色、voice alias 或价格/预算。
可用性和价格仍需在实际业务空间核验；这不是生产升级声明。

1. 管理员在运维管理 → 播客 → ASR 配额策略查看「当前语音模型」，或读取 `GET /api/admin/podcast-asr-quota` 的 `speech_models`。它列出 ASR/TTS 的有效名称、来源及旧快照退役提示；纯本地读取，不调用供应商。旧显式值继续保留并提示，不在启动时偷偷改写。
2. 配置优先级是 **runtime_kv > 环境变量 > INI > 默认值**。来源为 INI/env 时分别改 `asr_model`、`tts_model` 或 `DORAMI_BAILIAN_ASR_MODEL`、`DORAMI_BAILIAN_TTS_MODEL` 后重启。来源为 runtime_kv 时，由运维按现有 `services.credentials.save_updates(session, NAMESPACE, updates)` 入口更新，`updates` 仅传 `asr_model` / `tts_model` 字段（namespace 在 `services.bailian_speech_config`，对应存储键为 `bailian_speech_asr_model` / `bailian_speech_tts_model`）；空串清覆盖后才回落基线。仅改默认值或 INI 不会覆盖 KV。回读上述端点确认；不要导出完整配置或密钥。
3. 先停止接收新生成，排空原 ASR TaskId，并备份数据库和回执。**模型参与任务身份与 TTS 幂等键**，换模型不等于原请求重试；先核实原 `authorized`/未知状态，不能用改模型、改 scope、换回执目录、删账本规避冻结。旧 rejected 行保留；确认未计费后，新模型调用是新的付费授权。已发布音频继续可播。
4. `http_404:ModelNotFound` 等错误保留 HTTP 状态及允许名单中的供应商错误码；未知/非 JSON 错误仅保留 HTTP 状态。原始 message、密钥和签名链接不进入异常。单独 404 不能唯一判定模型退役，应同时检查地域、路径、模型权限与有效模型来源。POST 5xx/408/409 仍按结果未知处理，保持原预占和禁止重复提交语义；不增加自动重试。
5. 获准后以原样小样本核验 ASR/TTS 和同账本重放，再逐步恢复。官方模型文档与预检的集中小样本支持本次兼容选择。2026-10-30 已在隔离环境验证真实文章及指定播客生成、播放；指定播客使用发布方逐字稿，不能据此宣称执行了 ASR。候选自身请求桩/预算/幂等测试与评估器真实调用分别留证；2026-10-31 集成回归不新增付费请求，不代表生产已升级。

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
  容量检查还会为下一次最大音频预留空间；若沿用 Podcast 的 512 MiB 单文件上限，
  生产建议至少配置 1 GiB 回执缓存，避免首篇音频缓存后无法继续合成。

结果下载限定为 HTTPS 与新加坡 OSS 域名，逐跳校验重定向并限制字节数。
百炼有时给出 HTTP OSS 链接，适配器只尝试同地址的 HTTPS，不降级明文。
Qwen3 实测 WAV 使用占位长度；仅兼容已验证的标准 PCM 头模式并重写实际帧数，
普通截断 WAV 仍被拒绝。最终音频继续经过现有精品导读的 ffprobe、时长限制和 CAS 流程。

ASR 使用 RSS 发布者音频直链，提交前复核源权限与媒体快照。当前百炼实现没有 ISI 的 OSS 回退：
源站无法被百炼下载时会失败并保留记录，不能偷偷改走上海服务。只有后续样本证明需要中转时，
再增加新加坡私有 Bucket 和明确的下载失败恢复流程。

长音频 ASR 结果可能带有仅包含空白的带时间戳词条。规范化时忽略这些无词义占位符，
保留完整句子及有效词条，时间顺序检查保持严格。若旧版本已收到云端成功任务但本地仍在轮询，
升级后继续处理原 TaskId，不重新提交音频。

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
正式上线仍按 `docs/ops/release-process.md` 的发布 tag 流程进行，升级前备份数据库及回执目录。
新迁移位于既有单向迁移边界之后，回滚使用升级前数据库备份。

## 2026-09-15 实测记录（历史快照，不代表退役后的可用性）

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
