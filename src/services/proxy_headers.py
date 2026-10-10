"""代理头信任面的单点解析(issue #172)。

容器里 uvicorn 的直接客户端是 nginx 容器(源 IP 落在 compose 默认 bridge 网段,典型
172.x),不在 uvicorn 默认白名单 ``127.0.0.1`` 内 —— ``ProxyHeadersMiddleware`` 整段跳过,
边缘透传的 ``X-Forwarded-Proto`` 被丢弃,``request.base_url`` / ``scope["scheme"]`` 在
HTTPS 部署下恒为 ``http``。

信任面只在这里定义一次,由 ``docker/entrypoint.py`` 交给
``uvicorn.run(forwarded_allow_ips=...)``;``docker-compose.yml`` 的默认值与本模块的
默认值由 ``tests/test_forwarded_proto.py`` 断言逐字一致,免得两处各写一份而漂移。

为什么默认不含 ``*``:容器内 nginx 是唯一入口、backend 不发布宿主端口,所以精确网段足够;
而 ``*`` 会让任何能连到 backend 的来源自由声明协议与客户端 IP。``127.0.0.1`` 必须保留:
``deploy-docker.sh`` 用 ``DORAMI_HTTP_LISTEN=127.0.0.1:8080`` 把站点收进环回时,经宿主
直连 backend 的请求(健康探针/排障 curl)源 IP 就是它。
"""
import os

# 环境变量名(compose 注入,可覆盖以适配自定义 bridge 网段)
FORWARDED_ALLOW_IPS_ENV = "DORAMI_FORWARDED_ALLOW_IPS"

# 默认:uvicorn 原白名单(环回)+ docker 默认 bridge 私网段
DEFAULT_FORWARDED_ALLOW_IPS = "127.0.0.1,172.16.0.0/12"


def forwarded_allow_ips(environ=None) -> str:
    """返回交给 uvicorn 的 ``forwarded_allow_ips``:逗号分隔的 IP / CIDR 串。

    未设置或只留空白时回落 :data:`DEFAULT_FORWARDED_ALLOW_IPS`;
    设置时去空白(uvicorn 自身也接受带空格的串,这里归一以便断言可逐字比较)。
    """
    raw = (environ if environ is not None else os.environ).get(FORWARDED_ALLOW_IPS_ENV, "")
    entries = [item.strip() for item in raw.split(",") if item.strip()]
    if not entries:
        return DEFAULT_FORWARDED_ALLOW_IPS
    return ",".join(entries)
