<!-- r2:en -->
## Highlights

- Speculative decoding can now be enabled per request with `speculative: true`.
- A new `/v1/health` endpoint reports queue depth and GPU memory.
- Prefix caching is on by default for chat templates.
<!-- r2:seg -->
## Fixes

- Fixed a crash when a client disconnected mid-stream.
- `max_tokens` is now validated against the model's context window.
<!-- r2:zh -->
## 亮点

- 现在可以按请求启用推测解码：`speculative: true`。
- 新增 `/v1/health` 接口，报告队列深度与 GPU 显存。
- 聊天模板默认开启前缀缓存。
<!-- r2:seg -->
## 修复

- 修复客户端在流式输出中途断开时的崩溃。
- `max_tokens` 现在会按模型的上下文窗口校验。
