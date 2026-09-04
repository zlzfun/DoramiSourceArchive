"""远程内容同步(v3.18 互通波):接收方从另一个存量后端拉取归档内容。

场景:多套部署——新部署后端要快速灌入原始内容;或内网环境打通到每个源的网络
很难,但打通到另一个后端的单点网络容易。方向为**接收方主动拉取**(只需
接收方 → 发送方的单向可达),发送方零改动:复用归档同步契约 articles-jsonl-v1
(`GET /api/archive/export/articles.jsonl`),本地导入直接走
`api.routers.archive_sync.import_archive_sync_jsonl`(checksum 校验 / 按 id 幂等 /
空正文回填全部现成)。

安全要点:
- 远端管理员凭据只在单次探测/任务内存中使用,**绝不落库、绝不写日志**;
  job 的 payload 快照与 KV 游标只记 base_url + username。
- Cookie 手工回传:从 Set-Cookie 抽 name=value 显式带 `Cookie` 头——远端若开
  `cookie_secure`(HTTPS 生产姿态)而接收方经 http 访问,httpx 的 cookiejar 会因
  Secure 属性拒发导致一律 401,显式头绕开该坑(生产实操验证过的行为)。

增量游标:每次成功同步把本次所见最大 `archive_updated_at`（旧端点缺失时回退
`fetched_date`）记入 KV
(`remote_sync:state`,按 base_url 分目标),下次以 `fetched_date_start` 透传给
远端导出实现增量。参数名为兼容既有契约保留；重复区间由导入端幂等跳过。

定时任务的凭据存储(有意的契约变更):v3.18 的「凭据绝不落库」是针对一次性
**手动**同步(`probe`/`start` 端点的凭据只进单次请求/任务内存,至今不落库)。
但**定时同步**无人值守,必须持久化凭据——沿用 X API token 的既有范式:凭据存
AppSettingRecord KV(`remote_sync:schedule`),**只写不回显**。读取端点
(`load_schedule` 默认 `include_secret=False`)绝不回传 password 键、只给
`password_set: bool`;日志绝不打印 password;后台 job 的 payload 快照同样不含。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete
from sqlmodel import Session, select

from models.db import (
    AppSettingRecord,
    ArchiveSyncEntityStateRecord,
    SourceConfigRecord,
    SourceStateRecord,
)
from services import credentials
from services import archive_sync_v2

_logger = logging.getLogger("dorami.remote_sync")

REMOTE_SYNC_STATE_KEY = "remote_sync:state"
REMOTE_SYNC_SCHEDULE_KEY = "remote_sync:schedule"
REMOTE_SYNC_JOB_TYPE = "remote_archive_sync"

# 定时同步默认配置(未配置时 GET 返回的形状)。
_SCHEDULE_DEFAULT_CRON = "0 3 * * *"

# 每页拉取条数:与导出端 5000 上限留余量,单页体量适中(正文全量,页大易超时)。
DEFAULT_PAGE_SIZE = 1000
# 安全阀:单次任务最多翻页数(1000 页 × 1000 条 = 百万条,远超当前归档规模;
# 防远端异常返回导致无限翻页)。
MAX_PAGES = 1000
# source_states is the readiness fence: publish producer terminal states only
# after the matching article/analysis/media snapshot has reached the reader.
V2_STREAM_ORDER = ("sources", "taxonomy", "articles", "analyses", "media", "source_states")

_REQUEST_TIMEOUT = httpx.Timeout(20.0, read=120.0)
_MAX_RETRIES = 3


class RemoteSyncError(Exception):
    """远端不可达 / 登录失败 / 契约不符等,消息面向管理员界面直接展示。"""


def _decode_sync_state_record(record: AppSettingRecord | None) -> Dict[str, Any]:
    if record is None or not record.value:
        return {"targets": {}}
    try:
        state = json.loads(record.value)
    except json.JSONDecodeError:
        return {"targets": {}}
    if not isinstance(state, dict) or not isinstance(state.get("targets"), dict):
        return {"targets": {}}
    return state


def prepare_transaction_revision_consumer(
    session: Session,
    *,
    base_url: str,
    username: str,
    authority_id: str,
    schema_version: str,
    prepared_at: str,
) -> bool:
    """Prepare one target for the transaction-revision protocol exactly once."""

    if schema_version != archive_sync_v2.SCHEMA_VERSION:
        raise RemoteSyncError(
            f"远端 schema_version={schema_version or '<empty>'} 与本机 "
            f"{archive_sync_v2.SCHEMA_VERSION} 不兼容"
        )
    authority_id = str(authority_id or "").strip()
    if not authority_id:
        raise RemoteSyncError("远端 transaction-revision manifest 缺少 authority_id")

    record = session.get(AppSettingRecord, REMOTE_SYNC_STATE_KEY)
    state = _decode_sync_state_record(record)
    targets = dict(state["targets"])
    target = dict(targets.get(base_url) or {})
    current_schema = str(target.get("v2_schema_version") or "")
    current_authority = str(target.get("v2_authority_id") or "")
    if current_schema == schema_version:
        if current_authority and current_authority != authority_id:
            raise RemoteSyncError(
                "远端 authority_id 已变化；需人工确认 producer 身份并执行新的 rebase"
            )
        if current_authority == authority_id:
            return False
    legacy_authorities = {
        str(checkpoint.get("authority_id") or "")
        for checkpoint in (target.get("v2_streams") or {}).values()
        if isinstance(checkpoint, dict) and checkpoint.get("authority_id")
    }
    if legacy_authorities and legacy_authorities != {authority_id}:
        raise RemoteSyncError(
            "旧 checkpoint 的 authority_id 与当前 producer 不一致，拒绝自动 rebase"
        )

    # Timestamp checkpoints cannot be compared to integer revisions. Preserve
    # Articles/Analyses so read counters and local manual overlays survive a
    # failed first pull; authoritative upserts and presence-confirmed absence
    # reconcile them. Source metadata becomes inactive until its full upsert,
    # readiness is cleared, and Media remains for reference-based local GC.
    eligible_sources = session.exec(
        select(SourceConfigRecord).where(
            ~SourceConfigRecord.source_id.startswith("user_rss_", autoescape=True),
            SourceConfigRecord.owner_username == "",
            SourceConfigRecord.collection_authority_id.in_(("", authority_id)),
        )
    ).all()
    for source in eligible_sources:
        source.is_active = False
        session.add(source)
    eligible_source_ids = {source.source_id for source in eligible_sources}
    for state_row in session.exec(select(SourceStateRecord).where(
        ~SourceStateRecord.source_id.startswith("user_rss_", autoescape=True),
        SourceStateRecord.authority_id.in_(("", authority_id)),
    )).all():
        source = session.get(SourceConfigRecord, state_row.source_id)
        if source is None or state_row.source_id in eligible_source_ids:
            session.delete(state_row)
    session.exec(delete(ArchiveSyncEntityStateRecord).where(
        ArchiveSyncEntityStateRecord.authority_id == authority_id
    ))

    target.update({
        "username": username,
        "v2_schema_version": schema_version,
        "v2_authority_id": authority_id,
        "v2_rebased_at": prepared_at,
        "v2_streams": {},
    })
    targets[base_url] = target
    state["targets"] = targets
    value = json.dumps(state, ensure_ascii=False)
    if record is None:
        record = AppSettingRecord(key=REMOTE_SYNC_STATE_KEY, value=value)
    else:
        record.value = value
    session.add(record)
    session.flush()
    return True


def normalize_base_url(raw: str) -> str:
    base = (raw or "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RemoteSyncError("远端地址必须是 http(s)://主机[:端口] 形式")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteSyncError("远端地址禁止内嵌用户名或密码")
    if parsed.query or parsed.fragment:
        raise RemoteSyncError("远端地址禁止携带 query 或 fragment")
    return base


def _cookie_header_from(response: httpx.Response) -> str:
    """从登录响应的 Set-Cookie 抽 name=value 拼显式 Cookie 头(绕开 Secure 属性限制)。"""
    pairs: List[str] = []
    for raw in response.headers.get_list("set-cookie"):
        first = raw.split(";", 1)[0].strip()
        if "=" in first:
            pairs.append(first)
    return "; ".join(pairs)


async def _request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    last_error: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = await client.request(method, url, **kwargs)
            # 5xx 视为暂态重试;4xx 是确定性失败,立即抛给上层定性。
            if response.status_code >= 500:
                raise RemoteSyncError(f"远端服务错误 HTTP {response.status_code}")
            return response
        except (httpx.HTTPError, RemoteSyncError) as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(min(2 ** attempt, 8))
    raise RemoteSyncError(f"请求远端失败(已重试 {_MAX_RETRIES} 次): {last_error}")


async def _login(client: httpx.AsyncClient, base_url: str, username: str, password: str) -> str:
    """登录远端,返回显式 Cookie 头值;校验账户为 admin(导出面需要)。"""
    response = await _request_with_retry(
        client, "POST", f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
    )
    if response.status_code == 401:
        raise RemoteSyncError("远端登录失败:账号或密码错误")
    if response.status_code != 200:
        raise RemoteSyncError(f"远端登录异常:HTTP {response.status_code}")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RemoteSyncError("远端登录响应不是合法 JSON——该地址可能不是哆啦美后端") from exc
    role = ((payload or {}).get("user") or {}).get("role")
    if role != "admin":
        raise RemoteSyncError("远端账户不是管理员——归档导出需要远端 admin 账号")
    cookie_header = _cookie_header_from(response)
    if not cookie_header:
        raise RemoteSyncError("远端登录未返回会话 Cookie")
    return cookie_header


def _parse_export_page(raw_text: str) -> Dict[str, Any]:
    """轻量解析一页 NDJSON:取 manifest、article 行数与本页最大同步游标。"""
    manifest: Optional[Dict[str, Any]] = None
    article_count = 0
    max_fetched_date = ""
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue  # 坏行交给导入端计入 errors,这里只做进度/游标统计
        if item.get("kind") == "manifest":
            manifest = item
        elif item.get("kind") == "article":
            article_count += 1
            article = item.get("article") or {}
            cursor_value = str(
                article.get("archive_updated_at") or article.get("fetched_date") or ""
            )
            if cursor_value > max_fetched_date:
                max_fetched_date = cursor_value
    return {"manifest": manifest, "article_count": article_count, "max_fetched_date": max_fetched_date}


async def _fetch_export_page(
    client: httpx.AsyncClient,
    base_url: str,
    cookie_header: str,
    *,
    skip: int,
    limit: int,
    fetched_date_start: Optional[str] = None,
    source_ids: Optional[List[str]] = None,
) -> str:
    params: Dict[str, Any] = {"skip": skip, "limit": limit}
    if fetched_date_start:
        params["fetched_date_start"] = fetched_date_start
    if source_ids:
        params["source_ids"] = ",".join(source_ids)
    response = await _request_with_retry(
        client, "GET", f"{base_url}/api/archive/export/articles.jsonl",
        params=params, headers={"Cookie": cookie_header},
    )
    if response.status_code == 401:
        raise RemoteSyncError("远端会话失效(401)——同步中断")
    if response.status_code == 403:
        raise RemoteSyncError("远端拒绝导出(403)——请确认远端部署允许归档导出(collector/all 形态)")
    if response.status_code != 200:
        raise RemoteSyncError(f"远端导出异常:HTTP {response.status_code}")
    return response.text


async def _fetch_v2_page(
    client: httpx.AsyncClient,
    base_url: str,
    cookie_header: str,
    *,
    stream: str,
    snapshot: str,
    since: str,
    after: str,
    limit: int,
) -> str:
    params: Dict[str, Any] = {"limit": limit}
    if snapshot:
        params["snapshot"] = snapshot
    if since:
        params["since"] = since
    if after:
        params["after"] = after
    response = await _request_with_retry(
        client,
        "GET",
        f"{base_url}/api/archive/v2/export/{stream}.jsonl",
        params=params,
        headers={"Cookie": cookie_header},
    )
    if response.status_code in {401, 403}:
        raise RemoteSyncError(f"远端拒绝 v2 {stream} 导出(HTTP {response.status_code})")
    if response.status_code != 200:
        raise RemoteSyncError(f"远端 v2 {stream} 导出异常:HTTP {response.status_code}")
    return response.text


async def _fetch_v2_presence(
    client: httpx.AsyncClient,
    base_url: str,
    cookie_header: str,
    *,
    stream: str,
    identities: list[str],
    authority_id: str,
) -> set[str]:
    """Confirm one bounded candidate batch before any receiver-side prune."""

    response = await _request_with_retry(
        client,
        "POST",
        f"{base_url}/api/archive/v2/presence",
        headers={"Cookie": cookie_header},
        json={"stream": stream, "identities": identities},
    )
    if response.status_code in {401, 403}:
        raise RemoteSyncError(f"远端拒绝 v2 {stream} presence 查询(HTTP {response.status_code})")
    if response.status_code != 200:
        raise RemoteSyncError(f"远端 v2 {stream} presence 查询异常:HTTP {response.status_code}")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RemoteSyncError(f"远端 v2 {stream} presence 响应不是 JSON") from exc
    if (
        payload.get("schema_version") != archive_sync_v2.SCHEMA_VERSION
        or payload.get("capability") != archive_sync_v2.AUTHORITATIVE_PRESENCE_CAPABILITY
        or payload.get("authority_id") != authority_id
        or payload.get("stream") != stream
        or payload.get("requested") != identities
        or not isinstance(payload.get("present"), list)
    ):
        raise RemoteSyncError(f"远端 v2 {stream} presence 响应契约不匹配")
    present = payload["present"]
    if any(not isinstance(value, str) for value in present) or not set(present) <= set(identities):
        raise RemoteSyncError(f"远端 v2 {stream} presence 返回了未请求的 identity")
    return set(present)


async def _fetch_v2_media_bytes(
    client: httpx.AsyncClient,
    base_url: str,
    cookie_header: str,
    url_hash: str,
    *,
    expected_size: int,
    max_bytes: int,
) -> bytes:
    if expected_size <= 0 or expected_size > max_bytes:
        raise RemoteSyncError(f"远端媒体 {url_hash} 声明大小超出限制")
    last_error: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with client.stream(
                "GET",
                f"{base_url}/api/archive/v2/media/{url_hash}",
                headers={"Cookie": cookie_header},
            ) as response:
                if response.status_code >= 500:
                    raise RemoteSyncError(f"远端服务错误 HTTP {response.status_code}")
                if response.status_code != 200:
                    raise RemoteSyncError(
                        f"远端媒体 {url_hash} 下载失败:HTTP {response.status_code}"
                    )
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) != expected_size:
                    raise RemoteSyncError(f"远端媒体 {url_hash} Content-Length 不一致")
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > expected_size or received > max_bytes:
                        raise RemoteSyncError(f"远端媒体 {url_hash} 响应超过声明大小")
                    chunks.append(chunk)
                if received != expected_size:
                    raise RemoteSyncError(f"远端媒体 {url_hash} 响应大小不一致")
                return b"".join(chunks)
        except (httpx.HTTPError, RemoteSyncError) as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(min(2 ** attempt, 8))
    raise RemoteSyncError(f"远端媒体 {url_hash} 下载失败: {last_error}")


def _make_client(transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    """transport 可注入(测试用 httpx.MockTransport 假远端,不打真网——仓内约定)。"""
    return httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True, transport=transport)


async def probe(
    base_url: str, username: str, password: str,
    *, protocol: str = "v2", transport: Optional[httpx.AsyncBaseTransport] = None,
) -> Dict[str, Any]:
    """「测试连接」探针:登录 → 版本 → 契约可用性 → 总量(尽力而为)。"""
    base = normalize_base_url(base_url)
    async with _make_client(transport) as client:
        cookie_header = await _login(client, base, username, password)

        version = ""
        try:
            runtime_res = await _request_with_retry(
                client, "GET", f"{base}/api/runtime", headers={"Cookie": cookie_header}
            )
            if runtime_res.status_code == 200:
                version = str(runtime_res.json().get("version") or "")
        except (RemoteSyncError, json.JSONDecodeError):
            pass  # 版本只是展示信息,拿不到不阻断

        protocol = (protocol or "v2").strip().lower()
        if protocol == "v2":
            sample_text = await _fetch_v2_page(
                client, base, cookie_header, stream="sources",
                snapshot="", since="", after="", limit=1,
            )
            try:
                manifest, rows = archive_sync_v2.parse_page(
                    sample_text, expected_stream="sources",
                    requested_snapshot="", requested_since="", requested_after="",
                )
                archive_sync_v2.require_transaction_revision_capability(manifest)
            except archive_sync_v2.SyncV2Error as exc:
                raise RemoteSyncError(f"远端 v2 契约校验失败:{exc}") from exc
            schema_version = str(manifest["schema_version"])
            capabilities = list(manifest.get("capabilities") or [])
            sample_count = len(rows)
            authority_id = str(manifest["authority_id"])
            taxonomy_text = await _fetch_v2_page(
                client, base, cookie_header, stream="taxonomy",
                snapshot="", since="", after="", limit=5000,
            )
            try:
                taxonomy_manifest, _taxonomy_rows = archive_sync_v2.parse_page(
                    taxonomy_text, expected_stream="taxonomy",
                    requested_snapshot="", requested_since="", requested_after="",
                )
                archive_sync_v2.require_transaction_revision_capability(taxonomy_manifest)
            except archive_sync_v2.SyncV2Error as exc:
                if "published taxonomy_version" in str(exc):
                    raise RemoteSyncError(
                        "远端 Taxonomy catalog 尚未人工发布，拒绝启动 v2 同步"
                    ) from exc
                raise RemoteSyncError(f"远端 taxonomy v2 契约校验失败:{exc}") from exc
            if str(taxonomy_manifest["authority_id"]) != authority_id:
                raise RemoteSyncError("远端 sources/taxonomy authority_id 不一致")
            taxonomy_version = int(taxonomy_manifest.get("taxonomy_version") or 0)
            if taxonomy_version <= 0:
                raise RemoteSyncError("远端 Taxonomy catalog 尚未人工发布，拒绝启动 v2 同步")
            taxonomy_ready = True
        elif protocol == "v1":
            sample_text = await _fetch_export_page(client, base, cookie_header, skip=0, limit=1)
            sample = _parse_export_page(sample_text)
            manifest = sample["manifest"] or {}
            schema_version = str(manifest.get("schema_version") or "")
            if not schema_version:
                raise RemoteSyncError("远端导出响应缺少 manifest——契约不符,可能是版本过旧的后端")
            sample_count = sample["article_count"]
            authority_id = ""
            capabilities = []
            taxonomy_version = 0
            taxonomy_ready = False
        else:
            raise RemoteSyncError("protocol 仅支持 v1/v2")

        article_total: Optional[int] = None
        try:
            total_res = await _request_with_retry(
                client, "GET", f"{base}/api/articles",
                params={"limit": 1, "include_total": "true"},
                headers={"Cookie": cookie_header},
            )
            if total_res.status_code == 200:
                total = total_res.json().get("total")
                if isinstance(total, int):
                    article_total = total
        except (RemoteSyncError, json.JSONDecodeError):
            pass  # 总量只用于进度展示,拿不到就走未知总量

    return {
        "ok": True,
        "base_url": base,
        "version": version,
        "schema_version": schema_version,
        "capabilities": capabilities,
        "protocol": protocol,
        "authority_id": authority_id,
        "taxonomy_version": taxonomy_version,
        "taxonomy_ready": taxonomy_ready,
        "article_total": article_total,
        "sample_count": sample_count,
    }


async def run_pull(
    *,
    base_url: str,
    username: str,
    password: str,
    fetched_date_start: Optional[str] = None,
    source_ids: Optional[List[str]] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    import_fn: Callable[[str], Dict[str, Any]],
    on_total: Optional[Callable[[int], None]] = None,
    on_advance: Optional[Callable[[int], None]] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> Dict[str, Any]:
    """分页拉取远端导出并逐页导入本地。

    ``import_fn`` 注入导入实现(生产 = archive_sync.import_archive_sync_jsonl,
    同步且 DB 密集,调用方应已用 asyncio.to_thread 包装或接受阻塞;测试注入假体)。
    ``on_total``/``on_advance`` 桥接 jobs.Job 的进度上报。
    """
    base = normalize_base_url(base_url)
    page_size = min(max(int(page_size), 1), 5000)

    totals = {"pages": 0, "pulled": 0, "imported": 0, "updated": 0, "skipped": 0, "errors": 0}
    error_samples: List[Dict[str, Any]] = []
    max_fetched_date = ""

    async with _make_client(transport) as client:
        cookie_header = await _login(client, base, username, password)

        # 仅无过滤的全量同步才设 total(远端 total 是全库量,带增量/来源过滤时它不是
        # 本次任务的总数,拿来画进度条会误导——那时前端退化为「已拉取 N 条」计数)。
        if on_total is not None and not fetched_date_start and not source_ids:
            try:
                total_res = await _request_with_retry(
                    client, "GET", f"{base}/api/articles",
                    params={"limit": 1, "include_total": "true"},
                    headers={"Cookie": cookie_header},
                )
                if total_res.status_code == 200:
                    total = total_res.json().get("total")
                    if isinstance(total, int) and total > 0:
                        on_total(total)
            except (RemoteSyncError, json.JSONDecodeError):
                pass

        skip = 0
        for _ in range(MAX_PAGES):
            raw_text = await _fetch_export_page(
                client, base, cookie_header,
                skip=skip, limit=page_size,
                fetched_date_start=fetched_date_start, source_ids=source_ids,
            )
            page = _parse_export_page(raw_text)
            article_count = page["article_count"]
            if article_count == 0:
                break

            result = await asyncio.to_thread(import_fn, raw_text)
            totals["pages"] += 1
            totals["pulled"] += article_count
            totals["imported"] += int(result.get("imported_count") or 0)
            totals["updated"] += int(result.get("updated_count") or 0)
            totals["skipped"] += int(result.get("skipped_count") or 0)
            totals["errors"] += int(result.get("error_count") or 0)
            for err in (result.get("errors") or []):
                if len(error_samples) < 20:
                    error_samples.append(err)
            if page["max_fetched_date"] > max_fetched_date:
                max_fetched_date = page["max_fetched_date"]
            if on_advance is not None:
                on_advance(article_count)

            if article_count < page_size:
                break
            skip += page_size
        else:
            raise RemoteSyncError(f"翻页超过安全上限 {MAX_PAGES} 页,同步中止(已导入部分保留)")

    return {
        "base_url": base,
        "username": username,
        "fetched_date_start": fetched_date_start or "",
        "source_ids": source_ids or [],
        "max_fetched_date": max_fetched_date,
        "error_samples": error_samples,
        **totals,
    }


async def run_pull_v2(
    *,
    engine,
    base_url: str,
    username: str,
    password: str,
    media_root=None,
    media_max_bytes: int = 20 * 1024 * 1024,
    page_size: int = DEFAULT_PAGE_SIZE,
    checkpoints: Optional[Dict[str, Dict[str, str]]] = None,
    on_advance: Optional[Callable[[int], None]] = None,
    on_stream_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    push_candidate_evidence: bool = True,
    expected_authority_id: str = "",
) -> Dict[str, Any]:
    """Pull every v2 stream with independently checkpointed keyset pages.

    A page is validated and committed atomically by ``archive_sync_v2``.  A
    stream checkpoint is published only after its terminal page (and, for media,
    all declared binaries) has succeeded.  Replaying committed pages after a
    later failure is intentional and idempotent. All non-Taxonomy streams share
    one transaction-revision snapshot; ``source_states`` is applied last as the
    readiness fence.
    """

    base = normalize_base_url(base_url)
    page_size = min(max(int(page_size), 1), 5000)
    checkpoints = checkpoints or {}
    result: Dict[str, Any] = {
        "base_url": base,
        "username": username,
        "schema_version": archive_sync_v2.SCHEMA_VERSION,
        "streams": {},
    }
    expected_authority = str(expected_authority_id or "").strip()
    # Sources is the first transaction-revision stream and pins one committed
    # generation for every dependent stream. Taxonomy keeps its own governed
    # version counter and is the only exception.
    generation_snapshot = ""
    async with _make_client(transport) as client:
        cookie_header = await _login(client, base, username, password)
        for stream in V2_STREAM_ORDER:
            previous = checkpoints.get(stream) or {}
            # A completed prior snapshot is the exclusive lower watermark for
            # the next run. `after` is only for pages within the new snapshot.
            since = str(previous.get("snapshot") or "")
            snapshot = "" if stream == "taxonomy" else generation_snapshot
            after = ""
            stats = {
                "pages": 0,
                "count": 0,
                "inserted": 0,
                "updated": 0,
                "deleted": 0,
                "pruned": 0,
                "media_downloaded": 0,
            }
            for _ in range(MAX_PAGES):
                raw_text = await _fetch_v2_page(
                    client,
                    base,
                    cookie_header,
                    stream=stream,
                    snapshot=snapshot,
                    since=since,
                    after=after,
                    limit=page_size,
                )
                try:
                    manifest, rows = archive_sync_v2.parse_page(
                        raw_text,
                        expected_stream=stream,
                        requested_snapshot=snapshot,
                        requested_since=since,
                        requested_after=after,
                    )
                    archive_sync_v2.require_transaction_revision_capability(manifest)
                except archive_sync_v2.SyncV2Error as exc:
                    raise RemoteSyncError(f"v2 {stream} 契约校验失败:{exc}") from exc
                authority = str(manifest["authority_id"])
                if expected_authority and authority != expected_authority:
                    raise RemoteSyncError(
                        "v2 页面 authority_id 与连接预检不一致，拒绝写入"
                    )
                previous_authority = str(previous.get("authority_id") or "")
                if previous_authority and authority != previous_authority:
                    raise RemoteSyncError(
                        f"v2 {stream} authority_id 已变化，需人工重置 checkpoint"
                    )
                expected_authority = authority
                if snapshot and manifest.get("snapshot") != snapshot:
                    raise RemoteSyncError(f"v2 {stream} snapshot 在翻页中发生变化")
                snapshot = str(manifest.get("snapshot") or "")
                if stream == "sources":
                    generation_snapshot = snapshot
                try:
                    applied = await asyncio.to_thread(
                        archive_sync_v2.import_page,
                        engine,
                        raw_text,
                        expected_stream=stream,
                    )
                except archive_sync_v2.SyncV2Error as exc:
                    raise RemoteSyncError(f"v2 {stream} 导入失败:{exc}") from exc
                stats["pages"] += 1
                stats["count"] += int(applied["count"])
                stats["inserted"] += int(applied["inserted"])
                stats["updated"] += int(applied["updated"])
                stats["deleted"] += int(applied.get("deleted") or 0)

                if stream == "media" and rows:
                    if media_root is None:
                        raise RemoteSyncError("本地媒体库未配置，不能完成 v2 media stream")
                    for item in rows:
                        key = str(item["payload"]["url_hash"])
                        expected_size = int(item["payload"].get("size_bytes") or 0)
                        body = await _fetch_v2_media_bytes(
                            client,
                            base,
                            cookie_header,
                            key,
                            expected_size=expected_size,
                            max_bytes=media_max_bytes,
                        )
                        await asyncio.to_thread(
                            archive_sync_v2.install_media_bytes,
                            engine,
                            media_root,
                            key,
                            body,
                            max_bytes=media_max_bytes,
                        )
                        stats["media_downloaded"] += 1
                if on_advance is not None:
                    on_advance(int(applied["count"]))
                after = str(manifest.get("next_cursor") or after)
                if bool(manifest.get("complete")):
                    if not since and stream in {"articles", "analyses"}:
                        candidates = await asyncio.to_thread(
                            archive_sync_v2.full_authority_stale_identities,
                            engine,
                            stream,
                            authority,
                        )
                        present: set[str] = set()
                        for start in range(0, len(candidates), 1000):
                            present.update(await _fetch_v2_presence(
                                client,
                                base,
                                cookie_header,
                                stream=stream,
                                identities=candidates[start:start + 1000],
                                authority_id=authority,
                            ))
                        confirmed_absent = [
                            identity for identity in candidates if identity not in present
                        ]
                        stats["pruned"] += await asyncio.to_thread(
                            archive_sync_v2.finalize_full_authority_stream,
                            engine,
                            stream,
                            authority,
                            absent_identities=confirmed_absent,
                        )
                    checkpoint = {
                        "authority_id": authority,
                        "snapshot": snapshot,
                        "cursor": after,
                        "completed_at": "",  # caller stamps its local clock
                        **stats,
                    }
                    result["streams"][stream] = checkpoint
                    if on_stream_complete is not None:
                        on_stream_complete(stream, checkpoint)
                    break
            else:
                raise RemoteSyncError(f"v2 {stream} 翻页超过安全上限 {MAX_PAGES}")
        if push_candidate_evidence:
            evidence_result = {"status": "success", "pages": 0, "inserted": 0, "skipped": 0}
            try:
                after = ""
                evidence_snapshot = ""
                for _ in range(MAX_PAGES):
                    evidence_page = await asyncio.to_thread(
                        archive_sync_v2.export_custom_candidate_evidence_page,
                        engine,
                        snapshot=evidence_snapshot,
                        after=after,
                    )
                    evidence_manifest, _ = archive_sync_v2.parse_candidate_evidence_page(evidence_page)
                    if evidence_snapshot and evidence_manifest["snapshot"] != evidence_snapshot:
                        raise RemoteSyncError("Candidate 证据快照在翻页中发生变化")
                    evidence_snapshot = evidence_manifest["snapshot"]
                    response = await _request_with_retry(
                        client,
                        "POST",
                        f"{base}/api/archive/v2/candidate-evidence.jsonl",
                        headers={
                            "Cookie": cookie_header,
                            "Content-Type": "application/x-ndjson",
                        },
                        content=evidence_page.encode("utf-8"),
                    )
                    if response.status_code != 200:
                        raise RemoteSyncError(
                            f"自定 RSS Candidate 证据上传失败:HTTP {response.status_code}"
                        )
                    payload = response.json()
                    evidence_result["pages"] += 1
                    evidence_result["inserted"] += int(payload.get("inserted") or 0)
                    evidence_result["skipped"] += int(payload.get("skipped") or 0)
                    after = str(evidence_manifest.get("next_cursor") or "")
                    if evidence_manifest.get("complete") is True:
                        break
                else:
                    raise RemoteSyncError("Candidate 证据分页超过安全上限")
            except Exception as exc:  # Candidate is an auxiliary reverse channel.
                evidence_result = {"status": "failed", "error": str(exc)[:500]}
                _logger.warning("Candidate evidence upload failed after main v2 streams: %s", exc)
            result["candidate_evidence"] = evidence_result
    result["authority_id"] = expected_authority
    return result


# ── KV 游标(按 base_url 分目标)────────────────────────────────────────────────

def load_sync_state(engine) -> Dict[str, Any]:
    with Session(engine) as session:
        return _decode_sync_state_record(
            session.get(AppSettingRecord, REMOTE_SYNC_STATE_KEY)
        )


def record_sync_success(engine, result: Dict[str, Any], *, synced_at: str) -> None:
    """成功后落 KV 游标:只记 base_url/username/游标/摘要,**绝不含密码**。"""
    state = load_sync_state(engine)
    base = result["base_url"]
    previous = state["targets"].get(base) or {}
    # 增量同步没有新数据时保留旧游标,不让空跑把游标清空。
    cursor = result.get("max_fetched_date") or previous.get("last_fetched_date") or ""
    state["targets"][base] = {
        "username": result.get("username") or "",
        "last_fetched_date": cursor,
        "last_synced_at": synced_at,
        "last_result": {
            key: result.get(key, 0)
            for key in ("pages", "pulled", "imported", "updated", "skipped", "errors")
        },
    }
    with Session(engine) as session:
        record = session.get(AppSettingRecord, REMOTE_SYNC_STATE_KEY)
        value = json.dumps(state, ensure_ascii=False)
        if record is None:
            record = AppSettingRecord(key=REMOTE_SYNC_STATE_KEY, value=value)
        else:
            record.value = value
        session.add(record)
        session.commit()


def record_v2_stream_success(
    engine,
    *,
    base_url: str,
    username: str,
    stream: str,
    checkpoint: Dict[str, Any],
    synced_at: str,
) -> None:
    """原子推进一个 v2 stream checkpoint，不影响其他 stream。"""
    if stream not in V2_STREAM_ORDER:
        raise ValueError(f"unsupported v2 stream: {stream}")
    state = load_sync_state(engine)
    target = dict(state["targets"].get(base_url) or {})
    target_schema = str(target.get("v2_schema_version") or "")
    target_authority = str(target.get("v2_authority_id") or "")
    if target_schema != archive_sync_v2.SCHEMA_VERSION:
        raise RemoteSyncError("transaction-revision consumer 尚未完成协议 rebase")
    streams = dict(target.get("v2_streams") or {})
    previous = streams.get(stream) or {}
    incoming_authority = str(checkpoint.get("authority_id") or "")
    if target_authority != incoming_authority:
        raise RemoteSyncError("checkpoint authority_id 与已确认的 consumer epoch 不一致")
    if previous.get("authority_id") and previous["authority_id"] != incoming_authority:
        raise RemoteSyncError(
            f"{stream} authority_id 从 {previous['authority_id']} 变为 {incoming_authority}，"
            "需人工确认 producer 身份后重置 checkpoint"
        )
    streams[stream] = {
        **checkpoint,
        "completed_at": synced_at,
    }
    target.update({"username": username, "last_synced_at": synced_at, "v2_streams": streams})
    state["targets"][base_url] = target
    with Session(engine) as session:
        row = session.get(AppSettingRecord, REMOTE_SYNC_STATE_KEY)
        value = json.dumps(state, ensure_ascii=False)
        if row is None:
            row = AppSettingRecord(key=REMOTE_SYNC_STATE_KEY, value=value)
        else:
            row.value = value
        session.add(row)
        session.commit()


# ── 定时同步配置(KV,凭据只写不回显)──────────────────────────────────────────

def _load_schedule_raw(engine) -> Dict[str, Any]:
    with Session(engine) as session:
        record = session.get(AppSettingRecord, REMOTE_SYNC_SCHEDULE_KEY)
        if record is None or not record.value:
            return {}
        try:
            data = json.loads(record.value)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}


def load_schedule(engine, *, include_secret: bool = False) -> Dict[str, Any]:
    """读定时同步配置。

    默认 **不含 password 键**,只给 `password_set: bool`(是否已存密码);
    `include_secret=True` 时额外带 `password`(仅供无人值守 job 内部使用,绝不
    经端点回传)。KV 缺失时返回全默认(enabled=False)。
    """
    raw = _load_schedule_raw(engine)
    password = str(raw.get("password") or "")
    legacy_source_ids = list(raw.get("source_ids") or [])
    # A pre-v2 filtered schedule is ambiguous: retaining v1 silently keeps the
    # double-writer rollout hazard, while upgrading to v2 broadens its scope.
    # Preserve the raw JSON, but make the effective schedule safe-disabled until
    # an administrator explicitly saves either protocol.
    migration_required = bool("protocol" not in raw and legacy_source_ids)
    protocol = str(raw.get("protocol") or ("" if migration_required else "v2"))
    from services import sync_consumer_policy

    with Session(engine) as session:
        protocol_downgrade_blocked = bool(
            protocol == "v1"
            and sync_consumer_policy.v2_receiver_state_present(session)
        )
    result: Dict[str, Any] = {
        "enabled": (
            bool(raw.get("enabled", False))
            and not migration_required
            and not protocol_downgrade_blocked
        ),
        "cron": str(raw.get("cron") or _SCHEDULE_DEFAULT_CRON),
        "base_url": str(raw.get("base_url") or ""),
        "username": str(raw.get("username") or ""),
        "source_ids": legacy_source_ids,
        "protocol": protocol,
        "migration_required": migration_required,
        "protocol_downgrade_blocked": protocol_downgrade_blocked,
        "updated_at": str(raw.get("updated_at") or ""),
        "password_set": bool(password),
    }
    if include_secret:
        result["password"] = password
    return result


def save_schedule(engine, updates: Dict[str, Any], *, updated_at: str) -> Dict[str, Any]:
    """合并写回定时同步配置,返回 `load_schedule(include_secret=False)` 形状。

    `updates` 里 `password` 为空串/None 表示**保留已存密码**(统一凭据保管
    契约的写入半边,见 `services/credentials`;本 blob 是该契约下的 JSON blob
    历史形态);服务层不依赖 FastAPI,`updated_at` 由调用方传入。
    """
    raw = _load_schedule_raw(engine)
    legacy_protocol_choice_required = bool(
        "protocol" not in raw and list(raw.get("source_ids") or [])
    )
    if legacy_protocol_choice_required and "protocol" not in updates:
        raise RemoteSyncError(
            "旧版局部同步配置需要管理员显式选择并保存 v1 或 v2 协议"
        )
    merged = dict(raw)
    for key in ("enabled", "cron", "base_url", "username", "source_ids", "protocol"):
        if key in updates:
            merged[key] = updates[key]
    # 空/缺失 password 时保留 merged 里已存的密码。
    credentials.apply_secret_update(merged, updates, "password")
    merged["enabled"] = bool(merged.get("enabled", False))
    merged["cron"] = str(merged.get("cron") or _SCHEDULE_DEFAULT_CRON)
    merged["base_url"] = str(merged.get("base_url") or "")
    merged["username"] = str(merged.get("username") or "")
    merged["source_ids"] = list(merged.get("source_ids") or [])
    merged["protocol"] = str(merged.get("protocol") or "v2").strip().lower()
    if merged["protocol"] not in {"v1", "v2"}:
        raise RemoteSyncError("protocol 仅支持 v1/v2")
    if merged["protocol"] == "v2" and merged["source_ids"]:
        raise RemoteSyncError("v2 是一致性全流同步，不支持 source_ids 局部过滤")
    merged["updated_at"] = updated_at

    with Session(engine) as session:
        if merged["enabled"] and merged["protocol"] == "v1":
            from services import sync_consumer_policy

            if sync_consumer_policy.v2_receiver_state_present(session):
                raise RemoteSyncError(
                    "本机已进入 v2 consumer 模式，不能降级为 v1；"
                    "如需回退请先停止 worker 并恢复升级前备份"
                )
        record = session.get(AppSettingRecord, REMOTE_SYNC_SCHEDULE_KEY)
        value = json.dumps(merged, ensure_ascii=False)
        if record is None:
            record = AppSettingRecord(key=REMOTE_SYNC_SCHEDULE_KEY, value=value)
        else:
            record.value = value
        session.add(record)
        if merged["enabled"] and merged["protocol"] == "v2":
            from services import sync_consumer_policy

            # Schedule intent and receiver fence share one transaction, so no
            # public writer can slip between two commits.
            sync_consumer_policy.activate_v2_consumer_mode(
                session,
                reason="v2_schedule",
                activated_at=updated_at,
                commit=False,
            )
        session.commit()
    return load_schedule(engine, include_secret=False)


def activate_consumer_for_enabled_v2_schedule(engine, *, reason: str) -> bool:
    """Restore the v2 receiver fence before registering collector schedules."""

    schedule = load_schedule(engine)
    if not (schedule.get("enabled") and schedule.get("protocol") == "v2"):
        return False
    from services import sync_consumer_policy

    with Session(engine) as session:
        return sync_consumer_policy.activate_v2_consumer_mode(
            session,
            reason=reason,
        )
