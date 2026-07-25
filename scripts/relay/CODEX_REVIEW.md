结论：方案方向有价值，但当前只能算“设计原型”，不建议按可部署的一期验收。至少有 3 个阻断问题。

## 主要问题

1. **[P1] 默认启动参数会在改写前暴露原目标**

   [README.md](/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/scripts/relay/README.md:64) 没有设置 `connection_strategy=lazy`。mitmproxy 默认是 `eager`，并默认探测上游证书；HTTPS 的原始 `CONNECT target:443` 阶段就可能先连企业代理，`request()` 钩子此时尚未拿到内层 HTTP 请求，白名单会先看到并拦截真实域名。

   官方文档也明确描述了这一步，并确认默认策略是 eager：[工作机制](https://docs.mitmproxy.org/stable/concepts/how-mitmproxy-works/)、[配置选项](https://docs.mitmproxy.org/stable/concepts/options/)。

   至少应显式配置 `connection_strategy=lazy`，并考虑同时关闭 `upstream_cert`，然后以企业代理日志证明整个链路只出现跳板机域名。

2. **[P1] SSRF 护栏可以直接绕过**

   [relay.js](/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/scripts/relay/njs/relay.js:29) 只检查字符串形式的 IPv4，且用 `host.split(':')[0]` 处理地址。我实际烟测结果：

   - `127.0.0.1`、`10.0.0.1`：被拦截；
   - `[::1]`、`localhost.`：通过；
   - 任意攻击者域名解析到 `127.0.0.1`、私网或 `169.254.169.254`：也会通过。

   这与文档声称的“私网/元数据必要护栏”不符。必须在 DNS 解析后校验最终 IP，并覆盖 IPv4、IPv6、重定向每一跳、非标准 IP 表示、跳板机自身域名和端口限制。纯 nginx+njs 很难把这一点做扎实。

3. **[P1] Playwright/crawl4ai 链路尚未实现**

   [README.md](/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/scripts/relay/README.md:92) 只是说“launch args 里加”，实际 [playwright_renderer.py](/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/src/fetchers/impl/playwright_renderer.py:86) 和 [crawl4ai_backend.py](/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/src/fetchers/web_content/crawl4ai_backend.py:61) 都没有代理配置。

   Playwright提供的是显式 `proxy={server: ...}` 启动参数，[官方文档](https://playwright.dev/python/docs/network/)也如此要求。因此“项目代码零改动、覆盖 Playwright/crawl4ai”的结论目前不成立。

4. **[P2] 跳板机不校验目标站 TLS**

   [nginx-relay.conf](/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/scripts/relay/nginx-relay.conf:46) 开启了 SNI，却没有开启证书校验。nginx 的 `proxy_ssl_verify` 默认是 `off`，[官方文档](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)对此有明确说明。公网跳板机应配置系统 CA、`proxy_ssl_verify on` 和合理验证深度。

5. **[P2] 尚缺可重复的部署验证**

   目前没有 relay 单测或端到端测试；README 的 curl 示例也缺少信任 mitmproxy CA 或 `-k`，很可能无输出却因管道返回成功而造成误判。还缺少版本锁定、systemd/PM2 成品配置、健康检查及敏感信息脱敏日志。

## 做得好的部分

问题背景和取舍记录得很清楚；两级代理职责划分、base64url 普通 URL 往返、Host/SNI 恢复和“不改写响应体”的判断基本合理。Python 与 JS 语法检查通过，普通路径、查询参数和端口的解码烟测也通过。

综合评价：**设计分析 8/10，实现完成度 4/10，暂不具备生产可用性。** 此外，`scripts/relay/` 当前仍是未跟踪目录，尚未形成提交。

## 更优解法

如果允许重新考虑架构，最优仍是：**让公网公费机承担 collector，内网只做 reader，通过 Archive Sync 单向同步。** 这样企业白名单只需一个域名，也不需要通用开放中继、浏览器代理注入和复杂 SSRF 防护。

若“内网单端自采集”不可变，我更推荐：

- nginx 只负责 TLS、IP ACL 和限流；
- 后面放一个很小的 Go relay；
- 标准 URL 解析、HMAC 时效签名；
- DNS 解析后拒绝所有非公网地址，重定向逐跳复检；
- 仅允许 80/443；
- 正常校验目标 TLS；
- 提供请求大小限制、审计与指标。

这比坚持纯 nginx+njs 多一个小二进制，但安全边界和可测试性明显更好。

::code-comment{title="[P1] 改写发生得太晚" body="mitmproxy 默认使用 eager 连接并探测上游证书，HTTPS 原始 CONNECT 可能在 request 钩子之前就发给企业代理。启动参数需要显式使用 lazy 策略并做真实企业代理日志验证。" file="/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/scripts/relay/README.md" start=63 end=70 priority=1}
::code-comment{title="[P1] SSRF 防护可绕过" body="这里只检查 IP 字面量，且 IPv6 会被 split(':') 错误截断；IPv6 loopback、localhost. 和解析到私网或云元数据的域名都会通过。需要在 DNS 解析后验证最终地址，并对重定向逐跳复检。" file="/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/scripts/relay/njs/relay.js" start=29 end=45 priority=1}
::code-comment{title="[P1] 浏览器代理没有落地" body="这里仅给出待添加的 Chromium 参数，当前 PlaywrightRenderer 和 Crawl4AI BrowserConfig 都没有配置本地代理，因此浏览器抓取不属于已完成链路，也与项目代码零改动的结论冲突。" file="/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/scripts/relay/README.md" start=89 end=100 priority=1}
::code-comment{title="[P2] 应校验目标站证书" body="proxy_ssl_server_name 只设置 SNI；nginx 默认 proxy_ssl_verify=off。跳板机直接访问公网目标时应配置可信 CA 并开启证书校验。" file="/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/scripts/relay/nginx-relay.conf" start=46 end=48 priority=2}
