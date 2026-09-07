# Podcast ASR/TTS Provider 接入契约

## 边界

处理状态机、claim/lease/fencing、request-unknown、预算与额度预占、本地 CAS、音频 QA、
发布和管理端不得导入任何供应商 SDK 或供应商配置类型。它们只依赖
`services.podcast_provider_ports` 与 `services.podcast_worker_contracts`。

供应商 adapter 负责：

- 将逻辑 voice/model 与供应商参数转换为不含秘密的 `ExecutionIdentity`；
- 生成确定性的 `AsrProviderPlan` / `TtsProviderPlan` 和 admission fingerprint；
- 将供应商 submit/poll 响应映射为 `Accepted`、`Unknown`、`Rejected`、`Pending`、
  `Succeeded`、`TaskFailed` 或 `Indeterminate`；
- 将供应商用量和价格规范化为 `ProviderUsagePlan` / `NormalizedUsage`；
- 对远程 TTS 结果只提供 `RemoteAudioDeliveryPolicy`，不得把供应商配置对象传给下载、
  CAS 或发布层。

`provider_request_key` 只有在供应商文档明确承诺幂等时才可发送。未取得承诺时 adapter
必须忽略它；授权后的超时或不一致响应必须返回 `Unknown`，已知 TaskId 时只轮询该 ID，
不得自动再次提交。

## 新增供应商

1. 在独立模块实现 `AsrProviderAdapter` 或 `TtsProviderAdapter`，供应商 SDK、签名、token、
   endpoint 和 wire error 只能留在该模块及其 client 内。
2. 用逻辑 voice alias 映射供应商 voice；业务处理记录不能保存供应商密钥或临时签名 URL。
3. 实现 admission estimator，把账号范围、额度窗口、整数人民币价格、deadline 和配置修订
   纳入 fingerprint。缺少任何付费安全配置时 fail-closed。
4. TTS adapter 返回通用远程音频 host 策略；共享交付层继续执行 HTTPS-only、DNS 固定、
   重定向逐跳校验、大小上限、MIME/magic/ffprobe 和 lease heartbeat。
5. 注册前必须通过 provider-neutral contract tests、供应商 mock transport E2E、重启/轮换/
   request-unknown/重复调度测试，以及完整测试套件。

## 当前实现

- Aliyun ISI ASR/TTS 是上述 ports 的首个 adapter，不是状态机的默认类型。
- TTS 通用 Plan 只携带 `ProviderUsagePlan`；字符计数属于 Aliyun adapter 内部实现。
- 当前持久化额度支持 ASR 秒数和 TTS 字符数。若新供应商按其他单位计费，应先扩展
  `ProviderUsageUnit`、数据库约束/迁移和账本测试，不能把 token 假装成字符。
