"""代理头信任面守卫(issue #172)。

事故形态:容器里 uvicorn 的直接客户端是 nginx 容器,源 IP 落在 compose 默认 bridge 网段
(典型 172.x),不在 uvicorn 默认白名单 ``127.0.0.1`` 内 —— ``ProxyHeadersMiddleware``
整段跳过,边缘透传的 ``X-Forwarded-Proto`` 被丢弃,``request.base_url`` /
``scope["scheme"]`` 在 HTTPS 部署下恒为 ``http``(``/api/skill`` 注入的 ``{BASE_URL}``
即真实受害者)。

本文件锁三件事:
1. **配置面**:compose 显式注入 ``DORAMI_FORWARDED_ALLOW_IPS`` 且默认值含环回 + docker
   私网段;entrypoint 确实把它交给 ``uvicorn.run(forwarded_allow_ips=...)``。
2. **行为面**:进程内起真实 ``uvicorn.Server``,可信来源的 ``X-Forwarded-Proto: https``
   必须产出 ``scope["scheme"] == "https"``(含非法值回落),不可信来源必须被忽略。
3. **安全底线**:默认值不得是 ``*``(等于信任任意来源声明的协议与客户端 IP)。

为什么行为面不用 ``TestClient``:它把 ASGI scope 预置成 ``https``/``http``,根本不经过
``ProxyHeadersMiddleware`` 的信任判定,构造不出这个 bug(实测 uvicorn 0.46.0 与 0.53.0
行为一致)。这里改为真起 socket —— 测得的正是「默认白名单信任环回、私网段不信任环回」
两侧,恰好覆盖把 127.0.0.1 写丢(容器里经宿主直连失效)与把信任面写成 ``*``(伪造面)
两种退化。
"""
import os
import re
import socket
import sys
import threading
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.proxy_headers import forwarded_allow_ips  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker" / "entrypoint.py"
COMPOSE = ROOT / "docker-compose.yml"

DEFAULT_ALLOW_IPS = "127.0.0.1,172.16.0.0/12"


def _compose_backend_env() -> dict:
    """从 compose 里提取 backend 服务的 environment 映射(不依赖 PyYAML)。"""
    text = COMPOSE.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("  backend:"))
    env_at = next(
        i for i, line in enumerate(lines[start:], start) if line.strip() == "environment:"
    )
    # environment 块一直排到同缩进的 volumes: 为止(该块内全是 6 空格缩进的行)
    entries: dict[str, str] = {}
    for line in lines[env_at + 1 :]:
        if line.strip() and not line.startswith("      "):
            break
        match = re.match(r"\s{6}([A-Z0-9_]+):\s*(.*)$", line)
        if match:
            entries[match.group(1)] = match.group(2).split("#", 1)[0].strip()
    return entries


# --- 1. 配置面 -------------------------------------------------------------


def test_compose_declares_forwarded_allow_ips_with_loopback_and_private_net():
    env = _compose_backend_env()
    raw = env.get("DORAMI_FORWARDED_ALLOW_IPS")
    assert raw, "docker-compose.yml 的 backend.environment 必须显式注入 DORAMI_FORWARDED_ALLOW_IPS"
    # 形态:${DORAMI_FORWARDED_ALLOW_IPS:-<默认>} —— 默认值既含环回又含私网段
    assert raw.startswith("${DORAMI_FORWARDED_ALLOW_IPS:-"), raw
    default = raw[len("${DORAMI_FORWARDED_ALLOW_IPS:-") :].rstrip("}")
    assert default == DEFAULT_ALLOW_IPS, default


def test_entrypoint_passes_forwarded_allow_ips_to_uvicorn():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "forwarded_allow_ips=" in source, (
        "docker/entrypoint.py 必须把信任面交给 uvicorn.run(forwarded_allow_ips=...),"
        "否则容器内 nginx 的 X-Forwarded-Proto 会被丢弃(issue #172)"
    )
    # 信任面的取值只能来自这个共享解析点,免得 compose 默认值与进程默认值各写一份而漂移
    assert "forwarded_allow_ips()" in source
    assert not re.search(r'forwarded_allow_ips\s*=\s*["\']', source), (
        "不要在 entrypoint 里另写字面量信任面,统一走 services.proxy_headers.forwarded_allow_ips()"
    )


def test_compose_and_process_defaults_agree():
    env = _compose_backend_env()
    raw = env.get("DORAMI_FORWARDED_ALLOW_IPS", "")
    compose_default = raw[len("${DORAMI_FORWARDED_ALLOW_IPS:-") :].rstrip("}")
    assert compose_default == forwarded_allow_ips({})


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", DEFAULT_ALLOW_IPS),  # 缺省:环回 + docker 私网段
        ("   ", DEFAULT_ALLOW_IPS),
        ("10.11.0.0/16", "10.11.0.0/16"),  # 显式覆盖(自定义 bridge 网段)
        ("127.0.0.1,10.0.0.0/8 , ::1", "127.0.0.1,10.0.0.0/8,::1"),  # 去空白
    ],
)
def test_trusted_hosts_resolution(raw, expected):
    assert forwarded_allow_ips({"DORAMI_FORWARDED_ALLOW_IPS": raw}) == expected


def test_default_trust_never_widens_to_every_source():
    """默认信任面必须是具体网段,不能退化成 ``*``。"""
    resolved = forwarded_allow_ips({})
    assert "*" not in resolved.split(",")
    assert "127.0.0.1" in resolved.split(","), "写丢环回会让「站点收进环回 + 宿主直连」失效"


# --- 1b. 容器 nginx 的协议透传(根因①) ------------------------------------


def _nginx_conf() -> str:
    return (ROOT / "docker" / "nginx.conf").read_text(encoding="utf-8")


def _location_block(config: str, header_line: str) -> str:
    """按 ``location <header_line> {`` 取到配对的右花括号。"""
    start = config.index(header_line)
    depth = 0
    for offset, char in enumerate(config[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return config[start : offset + 1]
    raise AssertionError(f"未闭合的 location: {header_line}")


def test_nginx_forwards_upstream_proto_instead_of_overwriting_it():
    """根因①:容器只监听 80,``$scheme`` 恒为 http。直写它会把边缘透传的 https 踩掉,
    故两处反代都必须用 ``map`` 得到的变量(上游缺省才回落 ``$scheme``)。"""
    config = _nginx_conf()
    # map 本身:默认透传上游,上游未给(直连容器、无边缘)才回落本跳的 $scheme
    assert "map $http_x_forwarded_proto $dorami_forwarded_proto {" in config
    map_block = _location_block(config, "map $http_x_forwarded_proto $dorami_forwarded_proto {")
    assert "default $http_x_forwarded_proto;" in map_block
    assert '""' in map_block and "$scheme;" in map_block

    for header in ("location /api/ {", "location /mcp {"):
        block = _location_block(config, header)
        assert "proxy_set_header X-Forwarded-Proto $dorami_forwarded_proto;" in block, header
        # 反面:写死 $scheme 或 $http_x_forwarded_proto 都满足不了「透传 + 回落」两半
        assert "proxy_set_header X-Forwarded-Proto $scheme;" not in block, header


def test_nginx_conf_has_no_direct_scheme_forwarding_left():
    """全文兜底:任何一处反代指令都不许再直写 ``$scheme`` 充当转发协议。

    只看非注释行——文件头注释里正引着这条错误写法当反面教材。
    """
    directives = [
        line for line in _nginx_conf().splitlines() if not line.lstrip().startswith("#")
    ]
    assert not [line for line in directives if "X-Forwarded-Proto $scheme;" in line]


# --- 2. 行为面(真实 socket) ----------------------------------------------


def _probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(request: Request):  # pragma: no cover - 由子线程里的 server 调用
        return {
            "scheme": request.scope["scheme"],
            "client": request.scope["client"][0] if request.scope.get("client") else None,
        }

    return app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(port: int, headers: dict[str, str], path: str = "/probe") -> dict:
    import http.client
    import json

    # http.client 而不是裸 socket:真实应用会回 chunked,手工拆报文容易错。它照旧经真实
    # TCP 连入,``ProxyHeadersMiddleware`` 的信任判定不受影响。headers 里显式给 Host 时
    # http.client 不会再自动补一个。
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        content_type = response.getheader("content-type", "")
        status = response.status
    finally:
        connection.close()
    if "json" not in content_type:
        # 非 JSON 响应(/api/skill/*.zip 就是 zip)原样交回字节,别去 decode 成文本再猜。
        return {"status": status, "body": raw}
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        body = {"raw": raw[:200].decode("utf-8", "replace")}
    return {"status": status, "body": body}


def _serve(
    trusted_hosts: str | None,
    headers: dict[str, str],
    path: str = "/probe",
    app=None,
) -> dict:
    """在真实 socket 上起一次 uvicorn(源 IP 恒 127.0.0.1),返回 ``{status, body}``。

    非环回来源造不出来(macOS 上 ``bind("127.0.0.2")`` 即 ``OSError [Errno 49]``),
    故覆盖的是「默认白名单信任环回 / 私网段不信任环回」两侧——正是本 bug 的
    「配置写丢环回」与「写成 ``*``」两种退化方向。``app`` 缺省用探针应用;传真实应用
    时须给 Host 头(它决定 ``request.base_url``),且 lifespan 会被关掉(见调用处)。
    """
    port = _free_port()
    options = {"log_level": "error"}
    if trusted_hosts is not None:
        options["forwarded_allow_ips"] = trusted_hosts
    if app is not None:
        # 真实应用带 lifespan(调度器 / 播客对账 / FastMCP)——这里只验代理头到
        # request.base_url 的那一跳,不必也不该把它拉起来。
        options["lifespan"] = "off"

    config = uvicorn.Config(
        _probe_app() if app is None else app, host="127.0.0.1", port=port, **options
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = 20.0
        step = 0.02
        waited = 0.0
        while not server.started and waited < deadline:
            threading.Event().wait(step)
            waited += step
        assert server.started, "uvicorn 未能起来"
        return _request(port, dict(headers), path=path)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _serve_probe(trusted_hosts: str | None, headers: dict[str, str]) -> dict:
    payload = _serve(trusted_hosts, headers)
    assert payload["status"] == 200, payload
    return payload["body"]


def test_trusted_source_scheme_becomes_https():
    observed = _serve_probe(forwarded_allow_ips({}), {"X-Forwarded-Proto": "https"})
    assert observed["scheme"] == "https", (
        "默认信任面下的 X-Forwarded-Proto: https 必须被采信,否则 HTTPS 部署下 base_url 恒为 http"
    )


def test_trusted_source_client_ip_follows_x_forwarded_for():
    observed = _serve_probe(
        forwarded_allow_ips({}),
        {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9"},
    )
    assert observed["client"] == "203.0.113.9"


@pytest.mark.parametrize("bogus", ["junk", "javascript:alert(1)", "ftp", "http://x"])
def test_trusted_source_still_rejects_bogus_scheme_values(bogus):
    """信任来源不代表什么值都收:非法 scheme 必须回落 http(uvicorn 只认 http/https/ws/wss)。"""
    observed = _serve_probe(forwarded_allow_ips({}), {"X-Forwarded-Proto": bogus})
    assert observed["scheme"] == "http"


def test_untrusted_source_is_ignored():
    """docker 私网段**不含**环回:白名单一旦配错,伪造的 XFP 必须无效——这正是本 bug 的另一面。"""
    observed = _serve_probe("172.16.0.0/12", {"X-Forwarded-Proto": "https"})
    assert observed["scheme"] == "http"


def test_wildcard_would_trust_unknown_sources():
    """反向对照:``*`` 会全盘采信——记录默认值为何绝不能是它。"""
    observed = _serve_probe("*", {"X-Forwarded-Proto": "https"})
    assert observed["scheme"] == "https"


def test_compose_bridge_subnets_stay_inside_default_trust():
    """默认信任段覆盖 docker 常见 bridge 网段(文档给的自定义 bip 提醒不至于变成日常坑)。"""
    trusted = forwarded_allow_ips({}).split(",")
    assert "172.16.0.0/12" in trusted
    import ipaddress

    networks = [ipaddress.ip_network(entry) for entry in trusted if "/" in entry]
    for sample in ("172.17.0.1", "172.18.0.2", "172.31.255.254"):
        assert any(ipaddress.ip_address(sample) in net for net in networks), sample


# --- 3. 端到端:真实应用的受害者端点(/api/mcp/status) ----------------------


def _real_app(monkeypatch, tmp_path):
    """把真实 ``api.app`` 换成测试库与 ``role=all``,并签一枚管理员 Cookie。

    直接起 ``api.app``(生产里 uvicorn 包的就是它)而不是另造外壳:``require_admin_session``
    与 uvicorn 的 ``ProxyHeadersMiddleware`` 的相对位置正是要验的东西。
    """
    from dataclasses import replace

    import api.app as app_module
    from config import RuntimeConfig
    from sqlmodel import Session
    from storage.impl.db_storage import DatabaseStorage
    from tests.conftest import seed_default_accounts

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'forwarded_proto.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine)
    with Session(sink.engine) as session:
        record = app_module.accounts_service.get_user(session, "admin")
        token = app_module.create_auth_token("admin", record.role, record.session_epoch or "")
    return app_module.app, {app_module.AUTH_COOKIE_NAME: token}


def test_mcp_status_reports_https_behind_trusted_proxy(monkeypatch, tmp_path):
    """issue #172 的验收断言:受信来源 + ``X-Forwarded-Proto: https`` 时,
    ``/api/mcp/status`` 导出的 MCP 地址必须是 https(管理后台据此生成客户端配置)。"""
    app, cookies = _real_app(monkeypatch, tmp_path)
    cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
    observed = _serve(
        forwarded_allow_ips({}),
        {
            "X-Forwarded-Proto": "https",
            "Host": "www.dorami.cloud",
            "Cookie": cookie_header,
        },
        path="/api/mcp/status",
        app=app,
    )
    assert observed["status"] == 200, observed
    assert observed["body"]["url"] == "https://www.dorami.cloud/mcp", observed


def test_mcp_status_stays_http_without_forwarded_proto(monkeypatch, tmp_path):
    """反向对照:没有边缘声明协议时不得凭空变成 https(直连容器端口的原有行为)。"""
    app, cookies = _real_app(monkeypatch, tmp_path)
    cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
    observed = _serve(
        forwarded_allow_ips({}),
        {"Host": "127.0.0.1:8080", "Cookie": cookie_header},
        path="/api/mcp/status",
        app=app,
    )
    assert observed["status"] == 200, observed
    assert observed["body"]["url"] == "http://127.0.0.1:8080/mcp", observed


def test_skill_zip_embeds_https_base_url_behind_trusted_proxy(monkeypatch, tmp_path):
    """同一根因的另一个受害者:``api/skill_router.py:15`` 把 ``request.base_url``
    写进下载包里的 ``SKILL.md``(替换 ``{BASE_URL}``)。响应用 zipfile 读回。"""
    import io
    import zipfile

    app, cookies = _real_app(monkeypatch, tmp_path)
    cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
    observed = _serve(
        forwarded_allow_ips({}),
        {
            "X-Forwarded-Proto": "https",
            "Host": "www.dorami.cloud",
            "Cookie": cookie_header,
        },
        path="/api/skill/daily-brief",
        app=app,
    )
    assert observed["status"] == 200, observed
    payload = observed["body"]
    assert isinstance(payload, bytes), observed
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        skill_md = archive.read("dorami-daily-brief/SKILL.md").decode("utf-8")
    assert "https://www.dorami.cloud" in skill_md, skill_md[:500]
    assert "{BASE_URL}" not in skill_md
