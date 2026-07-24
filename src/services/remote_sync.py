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

增量游标:每次成功同步把本次所见最大 `fetched_date` 记入 KV
(`remote_sync:state`,按 base_url 分目标),下次以 `fetched_date_start` 透传给
远端导出实现增量;重复区间由导入端幂等跳过,天然安全。

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
from sqlmodel import Session

from models.db import AppSettingRecord

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

_REQUEST_TIMEOUT = httpx.Timeout(20.0, read=120.0)
_MAX_RETRIES = 3


class RemoteSyncError(Exception):
    """远端不可达 / 登录失败 / 契约不符等,消息面向管理员界面直接展示。"""


def normalize_base_url(raw: str) -> str:
    base = (raw or "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RemoteSyncError("远端地址必须是 http(s)://主机[:端口] 形式")
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
    """轻量解析一页 NDJSON:取 manifest、article 行数与本页最大 fetched_date。"""
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
            fetched = str((item.get("article") or {}).get("fetched_date") or "")
            if fetched > max_fetched_date:
                max_fetched_date = fetched
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


def _make_client(transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    """transport 可注入(测试用 httpx.MockTransport 假远端,不打真网——仓内约定)。"""
    return httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True, transport=transport)


async def probe(
    base_url: str, username: str, password: str,
    *, transport: Optional[httpx.AsyncBaseTransport] = None,
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

        sample_text = await _fetch_export_page(client, base, cookie_header, skip=0, limit=1)
        sample = _parse_export_page(sample_text)
        manifest = sample["manifest"] or {}
        schema_version = str(manifest.get("schema_version") or "")
        if not schema_version:
            raise RemoteSyncError("远端导出响应缺少 manifest——契约不符,可能是版本过旧的后端")

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
        "article_total": article_total,
        "sample_count": sample["article_count"],
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


# ── KV 游标(按 base_url 分目标)────────────────────────────────────────────────

def load_sync_state(engine) -> Dict[str, Any]:
    with Session(engine) as session:
        record = session.get(AppSettingRecord, REMOTE_SYNC_STATE_KEY)
        if record is None or not record.value:
            return {"targets": {}}
        try:
            state = json.loads(record.value)
        except json.JSONDecodeError:
            return {"targets": {}}
        if not isinstance(state, dict) or not isinstance(state.get("targets"), dict):
            return {"targets": {}}
        return state


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
    result: Dict[str, Any] = {
        "enabled": bool(raw.get("enabled", False)),
        "cron": str(raw.get("cron") or _SCHEDULE_DEFAULT_CRON),
        "base_url": str(raw.get("base_url") or ""),
        "username": str(raw.get("username") or ""),
        "source_ids": list(raw.get("source_ids") or []),
        "updated_at": str(raw.get("updated_at") or ""),
        "password_set": bool(password),
    }
    if include_secret:
        result["password"] = password
    return result


def save_schedule(engine, updates: Dict[str, Any], *, updated_at: str) -> Dict[str, Any]:
    """合并写回定时同步配置,返回 `load_schedule(include_secret=False)` 形状。

    `updates` 里 `password` 为空串/None 表示**保留已存密码**(与 X API token
    的「只写不回显、不改即留旧」范式一致);服务层不依赖 FastAPI,`updated_at`
    由调用方传入。
    """
    merged = dict(_load_schedule_raw(engine))
    for key in ("enabled", "cron", "base_url", "username", "source_ids"):
        if key in updates:
            merged[key] = updates[key]
    if updates.get("password"):
        merged["password"] = str(updates["password"])
    # 空/缺失 password 时保留 merged 里已存的密码。
    merged["enabled"] = bool(merged.get("enabled", False))
    merged["cron"] = str(merged.get("cron") or _SCHEDULE_DEFAULT_CRON)
    merged["base_url"] = str(merged.get("base_url") or "")
    merged["username"] = str(merged.get("username") or "")
    merged["source_ids"] = list(merged.get("source_ids") or [])
    merged["updated_at"] = updated_at

    with Session(engine) as session:
        record = session.get(AppSettingRecord, REMOTE_SYNC_SCHEDULE_KEY)
        value = json.dumps(merged, ensure_ascii=False)
        if record is None:
            record = AppSettingRecord(key=REMOTE_SYNC_SCHEDULE_KEY, value=value)
        else:
            record.value = value
        session.add(record)
        session.commit()
    return load_schedule(engine, include_secret=False)
