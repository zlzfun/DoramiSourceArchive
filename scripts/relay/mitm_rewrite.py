"""哆啦美内网出网改写 addon（mitmproxy）—— 内网侧转换器。

只在 intranet 分支存在；配合公网跳板机（nginx + njs）把「内网访问任意域名」
统一改写成「访问白名单跳板机」。

链路::

    后端 httpx / Playwright
      → 本机 mitmdump(本 addon, upstream 模式指向企业 proxy)
      → 企业 proxy(MITM 解密后看到 Host=跳板机 的普通 HTTPS 请求 → 命中白名单放行)
      → 跳板机 nginx(njs 解码 t 参数 → 反代真实目标)
      → 目标站

为什么内网侧不是 nginx:
    nginx 只监听「入站」连接，看不到后端进程主动发起的「出站」请求；要改写出站
    URL 必须由后端把请求交给一个本地代理(https_proxy)，而该代理还得终止 TLS 才能
    读到明文 URL——即在本机做一次 MITM。mitmproxy 已把 CONNECT 处理、本地 CA、
    按需签证书全做好，本 addon 只负责「读到原始 URL → 改写目标」。

启动(把占位换成真实值)::

    mitmdump \
      --mode upstream:http://企业PROXY主机:端口 \
      --upstream-auth 账号:密码 \
      --ssl-insecure \
      --listen-host 127.0.0.1 --listen-port 8080 \
      -s scripts/relay/mitm_rewrite.py \
      --set relay_host=跳板机域名

  - upstream 模式: mitmproxy 自身出网也要过企业 proxy，故指向它。
  - --ssl-insecure: 企业 proxy 用私有 CA 重签 upstream(跳板机)证书,与项目
    [network] disable_tls_verify=true 同因; mitmproxy 校验 upstream 证书会失败,
    这里跳过。
  - relay_host: 跳板机域名(改写后的目标 Host,必须在企业出网白名单内)。

内网项目侧只需在 config/production.ini 配::

    [proxy]
    https_proxy = http://127.0.0.1:8080
    http_proxy  = http://127.0.0.1:8080
    no_proxy    = 127.0.0.1,localhost

即所有 httpx 出网(采集/LLM/媒体/远程同步/X API)自动经此。Playwright 见 README。
"""
import base64

from mitmproxy import ctx, http

# 附带明文 &h=<目标域名> 便于企业侧审计日志辨识目标;代价是把目标域名暴露在
# query 中(企业 proxy 解密后可读)。默认关——保持目标不易被 URL 过滤器分类。
AUDIT_HOST_PARAM = False

_RELAY_HOST = ""


def load(loader):
    loader.add_option(
        "relay_host", str, "", "跳板机域名(改写后的目标 Host,须在企业白名单内)"
    )


def running():
    global _RELAY_HOST
    _RELAY_HOST = (ctx.options.relay_host or "").strip()
    if not _RELAY_HOST:
        raise ValueError("必须 --set relay_host=跳板机域名")
    ctx.log.info(f"[relay] 出网改写已启用 → 跳板机 {_RELAY_HOST}")


def request(flow: http.HTTPFlow) -> None:
    req = flow.request
    # 防重入:已是发往跳板机的请求不再改写
    if req.pretty_host == _RELAY_HOST:
        return

    original = req.url  # 完整原始 URL: scheme://host[:port]/path?query
    t = base64.urlsafe_b64encode(original.encode("utf-8")).decode("ascii").rstrip("=")
    path = "/relay?t=" + t
    if AUDIT_HOST_PARAM:
        path += "&h=" + req.pretty_host

    # 一律改写为 https 到跳板机(原 scheme 已封进 t,跳板机据此还原)
    req.scheme = "https"
    req.host = _RELAY_HOST
    req.port = 443
    req.path = path
    req.headers["Host"] = _RELAY_HOST
