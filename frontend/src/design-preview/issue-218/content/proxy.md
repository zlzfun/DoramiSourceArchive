<!-- r2:en -->
I wanted the local models on my laptop to be reachable from my phone without opening a port to the whole internet. The setup I ended up with is boring, which is the point: a small reverse proxy in front of the model server, with a single shared token.

The model server listens on `127.0.0.1:8080` only. The proxy terminates TLS and checks an `Authorization` header before it forwards anything.
<!-- r2:seg -->
A few things surprised me:

1. Streaming responses need buffering turned off in the proxy, otherwise tokens arrive in bursts.
2. Long generations hit the default 60-second read timeout.
3. Most clients will happily send the token in a query string if you let them. Don't.

None of this is new, but writing it down saves me an evening next time.
<!-- r2:zh -->
我想让手机能访问笔记本上的本地模型，又不想把端口暴露给整个互联网。最后的方案很无聊，而这正是重点：在模型服务前面放一个小小的反向代理，再配一个共享令牌。

模型服务只监听 `127.0.0.1:8080`。代理负责终止 TLS，并在转发前检查 `Authorization` 请求头。
<!-- r2:seg -->
有几件事出乎我的意料：

1. 流式响应需要在代理里关闭缓冲，否则 token 会一阵一阵地涌来。
2. 长时间的生成会撞上默认 60 秒的读取超时。
3. 只要你允许，多数客户端都乐于把令牌放进查询字符串。别这么做。

这些都不新鲜，但写下来，下次能省我一个晚上。
