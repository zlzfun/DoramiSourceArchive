# 飞书新概念审核清单

> 状态:耐久参考
> #298：投递适配器、持久审核/outbox、路由与调度已集成到 PR #333；尚未合入部署。本页不表示目标节点已启用正式每日清单。完整启用流程见[标签自动治理手册](taxonomy-governance.md)。

## 接收方式与配置

使用目标群的**自定义机器人**，而非手机号寻址。用户已选择每天北京时间 18:00 发送新增或有重要变化的审核清单，没有变化不发送。机器人消息链接回 Dorami 的登录审核页；点击链接不直接批准公共标签。

按[飞书官方指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)在群设置添加自定义机器人，并开启签名校验。Webhook 和签名密钥均视为凭据，只放本机 private 配置或凭据服务，不放 Git、报告、截图或日志。配置文件权限设为 `600`：

```ini
[feishu_notifications]
enabled = false
webhook_url =
signing_secret =
review_base_url =
timezone = Asia/Shanghai
digest_hour = 18
timeout_seconds = 15
allow_loopback_review_url = false
```

`review_base_url` 必须是接收人能访问的 HTTPS Dorami 地址，不含 query、fragment 或授权 token。未接通实际审核路由前保持 `enabled=false`。`load_feishu_config(path)` 仅载入明确指定的私密文件，不修改全局配置；解析失败不会打印原始配置行。

本机隔离观察可显式设置 `allow_loopback_review_url=true`，使用带端口的
`http://127.0.0.1:<port>`（也接受 localhost / IPv6 回环）。这种审核链接仅能在运行
验收服务的电脑上打开，手机仍能接收清单但不能打开本机页面。默认继续只允许公网 HTTPS；
此例外不适用于局域网地址、任意 HTTP 主机或公开证据链接，也不改变登录/管理员鉴权。

主配置通过 `[taxonomy] feishu_config_path` 引用上述私密文件，`review_enabled=true`
启用提案维护和每天 18:00 的发送调度；私密文件中的 `enabled` 是独立投递总闸。
审核路径为 `/review/taxonomy` 及 `/review/taxonomy/<id>`，需要管理员登录，GET 不改变状态。
`governance_enabled` 与 `exact_merge_enabled` 分别控制统计/队列维护和精确自动归并；
精确归并还需在管理面打开同名开关（兼容存储键 `taxonomy_auto_activation_enabled`）。
配置是能力闸、管理开关允许热启停；两者默认关闭。维护和精确身份归并不依赖文章分析 worker 或 LLM，
但有模型时会消费相关性补标任务，语义建议也会调用模型；不允许自动发布新公共概念。

## 适配器约定

`services.feishu_notifications` 提供：

- `FeishuNotificationConfig`：Webhook 与签名密钥不参与 repr；校验飞书官方 HTTPS hook endpoint。
- `build_daily_digest(config, items, digest_date=..., inbox_path=...)`：一条富文本清单，包含概念、类型、建议、理由和公开证据；空集合返回 `None`。最多展示 20 组，UTF-8 请求留在 20,000 字节以内，超出只保留摘要与完整清单入口。
- `ReviewDigestItem.review_path` 与 `inbox_path`：由集成方传真实只读页面路径，不允许 query/fragment、外站跳转或路径穿越。
- `PublicEvidenceLink.is_public` 默认 false；调用方必须先根据权限判定，适配器只接受显式公共且不带 query 的 HTTPS 链接。链接过滤不能替代源 ACL，标题/提案正文也必须由调用方做可见性过滤。
- `send_digest(...)`：异步单次 POST，禁用重定向及环境代理。签名采用官方 `HMAC-SHA256(timestamp + LF + secret, empty message)` 后 Base64；不使用消息正文作签名输入。

结果 `accepted` 只表示飞书返回 HTTP 200 且 `code=0`，不等于已送达终端、已读或已审核。`failed` 为明确拒绝/本地校验失败；`unknown` 为超时、5xx、协议异常等无法确定是否接收；`skipped` 为关闭或空清单。错误只保留脱敏分类、HTTP 状态和整数业务码，不保留供应商原始错误文本。

## 可靠性边界

适配器本身不维护 outbox，也不重试 POST；上层 `services.taxonomy_review` 已实现持久 outbox、批次领取、调度／lease 恢复、已审核组取消待发消息和旧 revision 过期处理。明确失败最多尝试 3 次，一个日期最多一个逻辑批次；确认 `accepted` 后记录终态。飞书自定义 Webhook 没有本适配器可用的幂等键或查询回执接口；超时或发送中断的 `unknown` 保留待核对，不自动盲重发。

HTTP 库日志在本次发送的执行上下文内关闭，防止 hook 路径和响应诊断带出凭据；其它请求日志不受影响。发送前仍需确认配置属于正确目标群，并使用已明确授权的消息范围。

## 无网络验证

`tests/test_feishu_notifications.py` 使用 `httpx.MockTransport`，覆盖签名固定向量、地址/审核路径限制、配置脱敏、公开证据过滤、消息大小、空清单、业务错误、重定向、超时不重发和日志隔离。运行这些测试不会向飞书发送消息。
