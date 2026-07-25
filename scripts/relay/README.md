# 内网出网跳板机方案（intranet 分支专属）

内网机被企业网关 MITM，出网需命中「个人 proxy 账号 + 目标域名白名单」。本方案把
**内网访问任意域名**统一改写成**访问白名单跳板机**，从而单端部署（`role=all`，
内网自采集自分发）也能出网。

## 链路

```
后端 httpx / Playwright
  │  https_proxy=http://127.0.0.1:8080
  ▼
mitmproxy + mitm_rewrite.py   （内网机本地；upstream→企业proxy，ssl-insecure）
  │  终止本机 TLS → 读到原始 URL → 改写为 https://跳板机/relay?t=<base64url>
  ▼
企业 proxy（MITM 解密后看到 Host=跳板机 的普通 GET/POST → 命中白名单，放行）
  ▼
跳板机 nginx + njs         （公网 ECS/容器）
  │  解码 t → 反代真实目标
  ▼
目标站
```

**为什么内网侧不是 nginx**：nginx 只监听入站连接，看不到后端进程的出站请求；要
改写出站 URL 必须由后端把请求交给一个本地代理（`https_proxy`），而该代理要读到
明文 URL 就得终止 TLS——即本机 MITM。mitmproxy 现成兜底，`mitm_rewrite.py` 只写
改写逻辑。**为什么跳板机能是纯 nginx**：njs 是官方第一方模块（官方镜像内置），
承担 base64url 解码；无旁挂程序。

## 文件

| 文件 | 位置 | 作用 |
|---|---|---|
| `mitm_rewrite.py` | 内网机 | mitmproxy addon：出站 URL 改写 |
| `njs/relay.js` | 跳板机 `/etc/nginx/njs/relay.js` | 解码 t、解析目标、SSRF 拦截 |
| `nginx-relay.conf` | 跳板机 nginx | 反代真实目标 |

## 跳板机部署（公网）

1. 官方 nginx 镜像（含 njs）。挂载 `njs/relay.js`、`nginx-relay.conf`、域名证书。
2. 改 `nginx-relay.conf` 三处：`server_name`、`allow 企业出口IP`、证书路径。
3. 证书：白名单按域名批的话，给跳板机域名配真证书（Let's Encrypt）；企业 proxy
   反正会重签，mitmproxy 侧 `--ssl-insecure` 不校验，所以自签也能跑通，但真证书
   便于将来收紧。
4. 入方向：`allow/deny` + 云安全组双保险，只放行企业 proxy 出口 IP。

## 内网机部署

> ⚠️ **两级代理是串联的,个人账号 proxy 要「搬家」不是「并存」**。内网机原本
> 已在项目 `[proxy]` 填了企业 proxy(个人账号)直连出网——现在那段凭据搬到
> mitmproxy 的 upstream 上,项目 `[proxy]` 改指本地 mitmproxy:
>
> ```
> httpx → 本地 mitmproxy(127.0.0.1:8080) → 企业 proxy(个人账号) → 跳板机 → 目标
>         └─ 改写 URL ─┘                   └─ 真正出网 ─┘
> ```
>
> 企业 proxy 不做 URL 改写,故 httpx 必须先经会改写的 mitmproxy;个人账号是
> 「真正出网」那段的凭据,归 mitmproxy 而非项目。

**1) 起 mitmproxy**（`pip install mitmproxy`；占位换真实值）：

```bash
mitmdump \
  --mode upstream:http://企业PROXY主机:端口 \   # ← 原项目 [proxy] 的地址搬这儿
  --upstream-auth 账号:密码 \                    # ← 原项目 [proxy] 的账号密码搬这儿
  --ssl-insecure \
  --listen-host 127.0.0.1 --listen-port 8080 \
  -s scripts/relay/mitm_rewrite.py \
  --set relay_host=跳板机域名
```

建议用 PM2/systemd 常驻（与项目 `deploy.sh` 的 PM2 路径同机）。**账号密码别裸写
命令行**（`ps` 可见）——用 systemd/PM2 的 env 注入,或写进 `~/.mitmproxy/config.yaml`
的 `upstream_auth`。

**2) 项目配置** `config/production.ini`——把企业 proxy 换成本地 mitmproxy：

```ini
[proxy]
# 改前(现状): 直连企业 proxy,只有白名单域名能通
# https_proxy = http://账号:密码@企业proxy:端口
# 改后: 指向本地 mitmproxy
https_proxy = http://127.0.0.1:8080
http_proxy  = http://127.0.0.1:8080
no_proxy    = 127.0.0.1,localhost   # 内网机对自身(8088)调用绕开 mitmproxy
```

`config.py` 会把它写进进程环境（`apply_process_environment`），所有 httpx 出网
（采集/LLM/媒体预取/远程同步/X API 共 9 处）自动经此，**项目代码零改动**。

**3) Playwright**（OpenAI 源 CF 绕过 + crawl4ai）指向同一代理：

```
--proxy-server=http://127.0.0.1:8080  --ignore-certificate-errors
```

（Playwright/crawl4ai 的 launch args 里加；`--ignore-certificate-errors` 因
mitmproxy 用自签 CA，与 `disable_tls_verify` 同理。将来把 mitmproxy 的 CA 装进
系统信任库后可去掉。）

## 验证

```bash
# 经代理测目标是否通（应看到目标站真实响应，而非拦截页）
https_proxy=http://127.0.0.1:8080 curl -sI https://api.openai.com/ | head

# 用项目脚本批量测采集域名（读 https_proxy）
https_proxy=http://127.0.0.1:8080 python scripts/netcheck.py all
```

`netcheck.py` 的 `COLLECTOR_DOMAINS` 就是从代码库实扫的出网域名清单——它也可当
跳板机白名单的来源（虽然本方案跳板机不按目标域名限制，但企业侧白名单只需一个
跳板机域名，运维更简单）。

## 安全账（务必知情）

- **无鉴权 = 依赖入方向 IP 白名单**：跳板机只靠 `allow` 企业出口 IP 挡外部。凡能
  经企业 proxy 出网的机器都能用它转发——内部工具可接受，但它是**开放中继**性质，
  `relay.js` 的私网/元数据拦截（含 `169.254.169.254`）是必要护栏，别删。
- **企业 proxy 看得见明文**：它解密到跳板机的这条 TLS，因此**你抓取的全部内容、
  X Bearer token、LLM api_key、Authorization 头它都可见**。这是 MITM 的既有事实
  （非本方案引入），但单端采集后经此链路的敏感数据更多，需知情。
- **个人 proxy 账号是单点**：白名单绑个人账号，人事/密码轮换即生产中断，合规上也
  站不住。目标用户在公司内 = 正式服务，**尽早转服务/机器账号**，优先级高于本方案
  任何技术项。
- **可还的技术债**：拿企业根 CA 装进系统信任库（CentOS：`/etc/pki/ca-trust/
  source/anchors/` + `update-ca-trust extract`）后，`[network] disable_tls_verify`
  可设回 `false`、`disable_ca_bundle` 清掉——从「全局不校验」收敛为「只信任企业
  网关」，安全姿态更好。

## 退路：跳板机镜像不带 njs

若跳板机确实无 njs，可弃用 base64url、改让 mitmproxy 把目标 host 放进明文 header、
路径保持原样，则纯 vanilla nginx 用 `proxy_pass https://$http_x_target_host` +
`$request_uri` 即可、无需解码。代价是目标 host 在企业侧完全明文可见。需要时告诉我，
addon 与 conf 各改几行即可切换。
