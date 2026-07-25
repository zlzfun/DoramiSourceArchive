# 出网跳板机方案 · 背景与决策记录

> 本文记录**为什么是这套方案**（约束、被否决的路径、关键技术事实、拍板点），
> 与「怎么部署」的 [`README.md`](README.md) 互补。仅 intranet 分支存在。

## 1. 环境约束

内网部署环境（见 CLAUDE.md 顶部块）：

- **出网被企业网关 MITM**：网关用自签 CA 重签一切 TLS，故后端出网证书校验必失败
  ——这正是本分支 `[network] disable_tls_verify` 开关及 9 处 httpx 接线的由来。
- **出网走个人 proxy 账号 + 域名/IP 白名单**：只有申请了白名单的目标可达，其余
  被拦截页/门户认证页挡下（`scripts/netcheck.py` 的拦截识别即为此写）。
- Docker 过旧不可用、CentOS + 手装 nginx/node、站点纯 HTTP + IP 访问。

## 2. 目标形态（本次拍板）

**单端部署**：内网一套 `role=all`，**自采集 + 自分发**，所有出网请求经跳板机转发。

演进过程：最初设想「公网 collector（dorami.cloud）+ 内网 reader」双端，靠
Archive Sync 同步（已验证可行）。但 dorami.cloud 是自费试验机，终局要撤；公费机器
若只做纯跳板，则内网必须自采集 → 必须能访问任意源站/图片 CDN/LLM/X API。于是收敛为
**单端 + 跳板机通用出网**。

> 曾评估「公费机器做 collector 而非纯跳板」（内网出网目标收敛为单一自家域名，安全
> 评审更好过）。用户选择单端，故本方案服务于单端 + 通用出网。

## 3. 核心机制：把「访问任意域名」改写成「访问白名单跳板机」

白名单按域名判定 + 网关 MITM 解密，这两个约束叠加，决定了出路只有一条：

- **标准正向代理会死**。网关解密后看到内层 `CONNECT api.openai.com:443`，真实
  目标域名暴露在白名单外 → 当场拦截。TLS 封装无用，因为封装本身被解开。
- **URL 重写能活**。网关解密后看到 `GET /relay?t=<...> Host: 跳板机`，Host 是
  白名单域名、结构是再正常不过的 HTTPS 请求 → 放行。真实目标藏在 query 里。

即：**必须让解密后的流量在语义上「看起来正常」**，这是白名单 + MITM 双重约束下
唯一的通路。（前几轮曾往标准/链式代理引，均因未把「网关会解密白名单流量」吃进
条件而作罢。）

### 3.1 为什么内网侧转换器不是 nginx

nginx 只监听**入站**连接，看不到后端进程主动发起的**出站**请求——出站连接的路径
上根本没有 nginx。要改写出站 URL，只能让后端把请求交给一个**本地代理**
（`https_proxy`），且该代理要读到明文 URL 就得**终止 TLS**（本机 MITM）。
mitmproxy 已把 CONNECT 处理 / 本地 CA / 按需签证书全做好，故内网侧 = mitmproxy +
几十行改写 addon，而非 nginx。

### 3.2 为什么跳板机能是纯 nginx

跳板机只需「解码 t → 反代真实目标」。纯 nginx 内核**无法 base64 解码**，但
**njs（官方第一方模块，官方镜像内置）**可以——它不是 OpenResty/Lua、不是旁挂
服务，仍是「一个 nginx」。故满足「纯 nginx」本意。

### 3.3 两级代理是串联的

```
后端 httpx / Playwright
  │  https_proxy=127.0.0.1:8080
  ▼
本地 mitmproxy(+addon)     终止本机 TLS → 读原始 URL → 改写为 https://跳板机/relay?t=<base64url>
  │  upstream → 企业 proxy(个人账号)
  ▼
企业 proxy(MITM 解密)       看到 Host=跳板机 的普通 GET/POST → 命中白名单,放行
  ▼
跳板机 nginx + njs          解码 t → 反代真实目标
  ▼
目标站
```

**个人账号 proxy 的归属**：原本填在项目 `[proxy]`（直连企业 proxy 出网，仅白名单
域名可通）。新方案中它「搬家」到 mitmproxy 的 upstream（`--mode upstream` +
`--upstream-auth`），项目 `[proxy]` 改指本地 mitmproxy——两级各管一段，非并存。

## 4. 关键技术事实（已确认）

| 事实 | 依据 | 影响 |
|---|---|---|
| 网关对白名单域名**也解密**（模式 B MITM） | `disable_tls_verify` 开关在 dorami.cloud 白名单**申请之后**才加入,仍报 `CERTIFICATE_VERIFY_FAILED` | 定死走 URL 重写而非链式代理 |
| 项目全部出网走 httpx 且 `trust_env` 默认开 | `fetchers/base.py`、`llm/client.py` 等 9 处 | 配 `[proxy]` 即全覆盖,**代码零改动** |
| `config.py` 把 `[proxy]` 写进进程环境 | `apply_process_environment`(config.py:206) | httpx 自动读 `https_proxy` |
| 重定向/正文绝对 URL 无需在跳板机改写 | 下一跳请求仍经 mitmproxy 重新编码 | 不改写响应体 → 归档忠实性不受污染 |

## 5. 拍板的实现选型

| 决策点 | 选择 | 理由/代价 |
|---|---|---|
| 跳板机实现 | **纯 nginx + njs** | 官方镜像内置 njs;无旁挂程序。vanilla nginx 无法 base64 解码,故需 njs |
| 原 URL 编码 | **base64url**（`?t=`） | 一次性绕过原 URL 自带 query/编码字符/路径穿越等边界;代价=企业侧日志中目标不可读（可选 `&h=` 明文冗余,默认关） |
| 鉴权 | **不鉴权,靠入方向 IP 白名单** | 跳板机 `allow 企业出口IP; deny all` + 云安全组;`relay.js` 私网/元数据拦截为必要护栏 |

## 6. 交付物

`scripts/relay/`：`mitm_rewrite.py`（内网 addon）、`njs/relay.js`（跳板机解码/
SSRF 拦截）、`nginx-relay.conf`（跳板机反代）、`README.md`（部署）、本文。**均不碰
`src/`**，属部署适配工具。

## 7. 风险与待办（部署前须知）

- **企业 proxy 看得见明文**：它解密到跳板机的 TLS，故抓取内容、X Bearer token、
  LLM api_key、Authorization 头**它都可见**。MITM 既有事实,非本方案引入,但单端
  采集后经此的敏感数据更多。
- **个人 proxy 账号是单点**：白名单绑个人账号,人事/密码轮换即生产中断,合规站不住。
  目标用户在公司内 = 正式服务,**尽早转服务/机器账号**,优先级高于任何技术项。
- **无鉴权 = 开放中继性质**：`relay.js` 的私网/元数据（含 `169.254.169.254`）拦截
  别删,否则可被诱导横移到跳板机自身内网/云元数据。
- **可还的技术债**：拿企业根 CA 装进系统信任库后,`disable_tls_verify` 可设回
  `false`、`disable_ca_bundle` 清掉——从「全局不校验」收敛为「只信任企业网关」。
- **白名单是持续运维项**：单端后内网要跑全量采集 + Playwright/crawl4ai(Chromium
  需离线装),机器负载与 `data/media` 增长先算一遍;新增源时同步跳板机/白名单认知。
- **退路**：跳板机镜像若无 njs,可弃 base64url、改 mitmproxy 传明文 host header +
  vanilla nginx `proxy_pass $http_x_target_host$request_uri`,零解码(代价:目标 host
  企业侧全明文)。
