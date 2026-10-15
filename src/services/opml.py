"""读者侧 OPML 导出与导入(issue #179)。

只处理当前读者自己的订阅,不提供管理端全站导出。OPML 只承载真实的 RSS/Atom
与播客 feed:网页、X、GitHub、arXiv 等没有 feed 地址的来源跳过,不伪造 xmlUrl。

导入默认是预览。确认时新建自定源复用 ``prepare_user_source`` 的 SSRF、大小上限、
配额与写锁;命中已有公共源或同 URL 自定源则订阅那一条,不另建一份。单次最多
200 条。确认后的首抓放到响应之后的后台任务,避免一次请求里同步抓最多 200 个 feed。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import formatdate
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit

from sqlmodel import Session

from services import source_visibility
from services import user_sources
from services.media_store import SSRFError

logger = logging.getLogger("dorami.opml")

OPML_IMPORT_LIMIT = 200
OPML_MAX_CHARS = 1_000_000
_PREVIEW_CONCURRENCY = 8
_LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain"})
_RSS_SOURCE_TYPES = frozenset({"rss", "atom", "podcast", "podcast_rss"})
_PODCAST_SOURCE_TYPES = frozenset({"podcast", "podcast_rss"})
_SSRF_REASON = "内网地址，出于安全原因不可添加"
_UNAVAILABLE_REASON = "该来源暂不可用"
_FETCH_FAIL_REASON = "无法访问该地址，请检查 URL 或稍后再试"
_ITEM_FAIL_REASON = "导入这一条时失败，请稍后重试"
_DAILY_QUOTA_REASON = "今日新增自定源已达上限,请明天再试"
_ENTITY_DECL_RE = re.compile(r"<!ENTITY", re.IGNORECASE)

_PUBLIC_ITEM_FIELDS = (
    "title",
    "xml_url",
    "canonical_url",
    "status",
    "reason",
    "source_id",
    "matched_name",
    "content_kind",
    "selectable",
)


def _clean_text(value: str, limit: int = 200) -> str:
    text = "".join(ch for ch in str(value or "") if ch >= " " or ch in "\t")
    return " ".join(text.split())[:limit]


def _local_name(tag: str) -> str:
    return str(tag or "").split("}")[-1].lower()


def _attr(element: ET.Element, name: str) -> str:
    wanted = name.lower()
    for key, value in element.attrib.items():
        if _local_name(key) == wanted:
            return str(value or "").strip()
    return ""


def _host_is_literal_or_local(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if normalized in _LOCAL_HOSTS:
        return True
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return True


async def _literal_target_is_private(url: str) -> bool:
    """字面 IP 与 localhost 在出网前按 SSRF 规则拒绝;普通域名交给 feed 预览。"""
    host = urlsplit(url).hostname or ""
    if not _host_is_literal_or_local(host):
        return False
    from services.media_store import ensure_public_host

    try:
        await ensure_public_host(host)
    except SSRFError:
        return True
    return False


def _blank_item(title: str, xml_url: str) -> Dict[str, Any]:
    return {
        "title": title or "未命名来源",
        "xml_url": xml_url,
        "canonical_url": "",
        "status": "invalid",
        "reason": "",
        "source_id": "",
        "matched_name": "",
        "content_kind": "",
        "selectable": False,
        "category": "",
    }


def _public_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {key: item.get(key, "") for key in _PUBLIC_ITEM_FIELDS}


def _counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {
        "new": 0,
        "already_subscribed": 0,
        "matched_existing": 0,
        "invalid": 0,
        "rejected_ssrf": 0,
    }
    for item in items:
        status = item.get("status")
        if status in counts:
            counts[status] += 1
    return counts


# ==================== 导出 ====================

def _export_feed_url(raw: str) -> Optional[str]:
    """导出用的 feed 地址。普通地址规范化;含凭证的地址原样保留。

    ``canonical_feed_url`` 会拒绝 userinfo。导出是读者自己的订阅备份,token、
    签名和 userinfo 都要留下,由调用方转义后写入文件,不在这里改写成掩码。
    """
    text = (raw or "").strip()
    if not text:
        return None
    if user_sources.feed_url_has_credentials(text):
        parts = urlsplit(text)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        return text
    try:
        return user_sources.canonical_feed_url(text)
    except ValueError:
        return None


def _outline_for_source(session: Session, source_id: str) -> Optional[Dict[str, Any]]:
    """订阅源里能写进 OPML 的真实 feed;没有 feed 地址则返回 None。"""
    from fetchers.registry import fetcher_registry
    from models.db import SourceConfigRecord

    fetcher_class = fetcher_registry.get_class(source_id)
    if fetcher_class is not None:
        if getattr(fetcher_class, "is_template", False):
            return None
        feed_url = str(getattr(fetcher_class, "feed_url", "") or "").strip()
        xml_url = _export_feed_url(feed_url)
        if not xml_url:
            return None
        html_url = str(getattr(fetcher_class, "source_url", "") or "").strip()
        return {
            "name": _clean_text(getattr(fetcher_class, "name", "") or source_id) or source_id,
            "xml_url": xml_url,
            "html_url": _public_html_url(html_url, xml_url),
            "podcast": str(getattr(fetcher_class, "content_shape", "") or "").lower() == "podcast",
        }

    record = session.get(SourceConfigRecord, source_id)
    if record is None or not (record.url or "").strip():
        return None
    source_type = (record.source_type or "").strip().lower()
    if source_type not in _RSS_SOURCE_TYPES:
        return None
    xml_url = _export_feed_url(record.url)
    if not xml_url:
        return None
    podcast = source_type in _PODCAST_SOURCE_TYPES or (
        (record.fetcher_id or "").strip().lower() == "generic_podcast_rss"
    )
    return {
        "name": _clean_text(record.name or source_id) or source_id,
        "xml_url": xml_url,
        "html_url": _public_html_url(record.base_url or "", xml_url),
        "podcast": podcast,
    }


def _public_html_url(raw: str, xml_url: str) -> str:
    text = (raw or "").strip()
    if not text or text == xml_url:
        return ""
    try:
        canonical = user_sources.canonical_feed_url(text)
    except ValueError:
        return ""
    if canonical == xml_url:
        return ""
    return canonical


def export_plan(session: Session, source_ids: List[str]) -> Dict[str, Any]:
    """把订阅名单分成可导出的 outline 与跳过的非 RSS 数量。

    调用方传入的名单含已隐藏的订阅(与源栏保留「暂不可用」条目同一口径)。
    这里仍导出其中的真实 feed,文件本身不标注隐藏或停用。
    """
    outlines: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    skipped = 0
    for source_id in source_ids:
        outline = _outline_for_source(session, source_id)
        if outline is None:
            skipped += 1
            continue
        if outline["xml_url"] in seen:
            continue
        seen.add(outline["xml_url"])
        outlines.append(outline)
    outlines.sort(key=lambda item: (item["name"].casefold(), item["xml_url"]))
    return {
        "exportable_count": len(outlines),
        "skipped_count": skipped,
        "outlines": outlines,
    }


def render_opml(outlines: List[Dict[str, Any]], *, title: str = "哆啦美订阅") -> str:
    """生成 OPML 2.0。标题与地址一律走 ElementTree 转义。"""
    opml = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = title or "哆啦美订阅"
    ET.SubElement(head, "dateCreated").text = formatdate(usegmt=True)
    body = ET.SubElement(opml, "body")
    for outline in outlines:
        name = outline.get("name") or "未命名来源"
        attrs = {
            "text": name,
            "title": name,
            "type": "rss",
            "xmlUrl": outline["xml_url"],
        }
        html_url = outline.get("html_url") or ""
        if html_url:
            attrs["htmlUrl"] = html_url
        if outline.get("podcast"):
            attrs["category"] = "podcast"
        ET.SubElement(body, "outline", attrs)
    body_xml = ET.tostring(opml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body_xml


# ==================== 解析 ====================

def parse_opml_outlines(text: str) -> List[Dict[str, str]]:
    """抽出带 xmlUrl 的 outline(含文件夹嵌套)。超过上限或不是 OPML 时抛 ValueError。"""
    raw = str(text or "").lstrip("\ufeff").strip()
    if not raw:
        raise ValueError("请提供 OPML 文件")
    if len(raw) > OPML_MAX_CHARS:
        raise ValueError("OPML 文件过大")
    # stdlib ElementTree 不解析外部实体,但会展开内部实体。先拒绝实体声明,
    # 避免外部实体与 billion laughs;无内部子集的 PUBLIC DOCTYPE 仍可解析。
    if _ENTITY_DECL_RE.search(raw):
        raise ValueError("不是有效的 OPML 文件")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("不是有效的 OPML 文件") from exc

    outlines: List[Dict[str, str]] = []
    for element in root.iter():
        if _local_name(element.tag) != "outline":
            continue
        xml_url = _attr(element, "xmlUrl")
        if not xml_url:
            continue
        title = _clean_text(_attr(element, "title") or _attr(element, "text") or element.text or "")
        outlines.append({
            "title": title,
            "xml_url": xml_url.strip(),
            "html_url": _attr(element, "htmlUrl"),
            "category": _attr(element, "category"),
        })
    if _local_name(root.tag) != "opml" and not outlines:
        raise ValueError("不是有效的 OPML 文件")
    if len(outlines) > OPML_IMPORT_LIMIT:
        raise ValueError(f"单次导入最多 {OPML_IMPORT_LIMIT} 条")
    return outlines


def _dedupe_outlines(outlines: List[Dict[str, str]]) -> tuple[List[Dict[str, str]], int]:
    seen: Set[str] = set()
    unique: List[Dict[str, str]] = []
    duplicates = 0
    for outline in outlines:
        try:
            key = user_sources.canonical_feed_url(outline["xml_url"])
        except ValueError:
            key = "invalid:" + outline["xml_url"].strip()
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(outline)
    return unique, duplicates


def _custom_create_block(session: Session) -> str:
    """自定源不可用时的原因;空串表示可以新建。"""
    import importlib

    app = importlib.import_module("api.app")
    if not app.runtime_collector_enabled():
        return "当前部署形态不支持添加自定源"
    if not user_sources.feature_enabled(session):
        return "自定源功能未开放"
    return ""


def _mark_existing(
    item: Dict[str, Any],
    existing: Dict[str, Any],
    subscribed_ids: Set[str],
) -> None:
    source_id = existing.get("source_id") or ""
    item["source_id"] = source_id
    item["matched_name"] = existing.get("name") or ""
    item["content_kind"] = existing.get("content_kind") or "article"
    if item["title"] == "未命名来源" and item["matched_name"]:
        item["title"] = item["matched_name"]
    if source_id in subscribed_ids:
        item["status"] = "already_subscribed"
        item["reason"] = "已在订阅中"
        item["selectable"] = False
        return
    item["status"] = "matched_existing"
    item["reason"] = ""
    item["selectable"] = True


def _classify_local(
    session: Session,
    outline: Dict[str, str],
    subscribed_ids: Set[str],
    create_block: str,
) -> Dict[str, Any]:
    item = _blank_item(outline.get("title") or "", outline.get("xml_url") or "")
    item["category"] = outline.get("category") or ""
    try:
        canonical = user_sources.canonical_feed_url(item["xml_url"])
    except ValueError as exc:
        item["reason"] = str(exc)
        return item
    item["canonical_url"] = canonical
    if item["title"] == "未命名来源":
        item["title"] = urlsplit(canonical).hostname or "未命名来源"
    try:
        checked = user_sources.prepare_check(session, canonical)
    except ValueError as exc:
        item["reason"] = str(exc)
        return item
    if checked.get("blocked"):
        item["reason"] = _UNAVAILABLE_REASON
        return item
    existing = checked.get("existing")
    if existing:
        _mark_existing(item, existing, subscribed_ids)
        return item
    if create_block:
        item["reason"] = create_block
        return item
    item["_needs_fetch"] = True
    return item


def _safe_value_reason(exc: ValueError) -> str:
    message = str(exc).strip() or "不是有效的 RSS/Atom"
    if "http://" in message or "https://" in message:
        return "不是有效的 RSS/Atom"
    return message


async def _attach_preview(item: Dict[str, Any]) -> None:
    item.pop("_needs_fetch", None)
    canonical = item["canonical_url"]
    try:
        preview = await user_sources.fetch_feed_preview(canonical)
    except SSRFError:
        item["status"] = "rejected_ssrf"
        item["reason"] = _SSRF_REASON
        return
    except ValueError as exc:
        item["status"] = "invalid"
        item["reason"] = _safe_value_reason(exc)
        return
    except Exception:  # noqa: BLE001 - 网络失败不把地址或栈带回给读者
        logger.warning("OPML preview fetch failed for host %s", urlsplit(canonical).hostname)
        item["status"] = "invalid"
        item["reason"] = _FETCH_FAIL_REASON
        return

    requested_podcast = "podcast" in (item.get("category") or "").lower()
    detected = preview.get("detected_kind") or "article"
    kind = "podcast" if requested_podcast else detected
    playable = int(preview.get("playable_entry_count") or preview.get("audio_entry_count") or 0)
    if kind == "podcast" and not playable:
        item["status"] = "invalid"
        item["reason"] = "该 feed 未检测到可播放的音频条目"
        return
    feed_title = _clean_text(preview.get("feed_title") or "")
    if item["title"] == (urlsplit(canonical).hostname or "未命名来源") and feed_title:
        item["title"] = feed_title
    item["status"] = "new"
    item["reason"] = ""
    item["content_kind"] = kind if kind in user_sources.USER_SOURCE_KINDS else "article"
    item["selectable"] = True


def _quota_snapshot(session: Session, username: str) -> Dict[str, int]:
    used = len(user_sources.subscribed_user_source_ids(session, username))
    daily_used = user_sources.daily_created_count(session, username)
    room = user_sources.MAX_SOURCES_PER_USER - used
    daily_room = user_sources.DAILY_ADD_LIMIT - daily_used
    return {
        "used": used,
        "max": user_sources.MAX_SOURCES_PER_USER,
        "daily_used": daily_used,
        "daily_max": user_sources.DAILY_ADD_LIMIT,
        "remaining": max(0, min(room, daily_room)),
    }


def _apply_quota(session: Session, username: str, items: List[Dict[str, Any]]) -> Dict[str, int]:
    """同时卡住总数与当日新增。超出的新建行标为无效,不再计入可勾选数。"""
    quota = _quota_snapshot(session, username)
    room = quota["max"] - quota["used"]
    daily_room = quota["daily_max"] - quota["daily_used"]
    budget = quota["remaining"]
    reason = (
        f"自定源数量已达上限({user_sources.MAX_SOURCES_PER_USER} 个)"
        if room <= daily_room
        else _DAILY_QUOTA_REASON
    )
    seen = 0
    for item in items:
        if item.get("status") != "new":
            continue
        if seen < budget:
            seen += 1
            continue
        item["status"] = "invalid"
        item["reason"] = reason
        item["selectable"] = False
    return quota


async def _classify(
    session: Session, username: str, outlines: List[Dict[str, str]]
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    import importlib

    app = importlib.import_module("api.app")
    subscribed_ids = set(app.resolve_subscribed_source_ids(session, username, include_hidden=True))
    create_block = _custom_create_block(session)
    items = [
        _classify_local(session, outline, subscribed_ids, create_block)
        for outline in outlines
    ]
    # 字面 IP / localhost 在出网前拒绝;普通域名交给 feed 预览(含重定向 SSRF)。
    pending: List[Dict[str, Any]] = []
    for item in items:
        if not item.get("_needs_fetch"):
            continue
        if await _literal_target_is_private(item["canonical_url"]):
            item.pop("_needs_fetch", None)
            item["status"] = "rejected_ssrf"
            item["reason"] = _SSRF_REASON
            item["selectable"] = False
            continue
        pending.append(item)
    semaphore = asyncio.Semaphore(_PREVIEW_CONCURRENCY)

    async def _run(item: Dict[str, Any]) -> None:
        async with semaphore:
            await _attach_preview(item)

    if pending:
        await asyncio.gather(*[_run(item) for item in pending])
    for item in items:
        item.pop("_needs_fetch", None)
    quota = _apply_quota(session, username, items)
    return items, quota


def _response(
    items: List[Dict[str, Any]],
    *,
    dry_run: bool,
    duplicate_count: int,
    created_count: int = 0,
    subscribed_count: int = 0,
    created_source_ids: Optional[List[str]] = None,
    quota: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    return {
        "dry_run": dry_run,
        "written": created_count + subscribed_count > 0,
        "limit": OPML_IMPORT_LIMIT,
        "duplicate_count": duplicate_count,
        "quota": dict(quota or {}),
        "counts": _counts(items),
        "created_count": created_count,
        "subscribed_count": subscribed_count,
        "created_source_ids": list(created_source_ids or []),
        "items": [_public_item(item) for item in items],
    }


def _selected_urls(urls: Optional[List[str]], items: List[Dict[str, Any]]) -> Set[str]:
    selectable = {
        item["canonical_url"]
        for item in items
        if item.get("selectable") and item.get("canonical_url")
    }
    if urls is None:
        return selectable
    chosen: Set[str] = set()
    for raw in urls:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            key = user_sources.canonical_feed_url(text)
        except ValueError:
            key = text
        if key in selectable:
            chosen.add(key)
    return chosen


def _subscribe_existing(session: Session, username: str, source_id: str, name: str) -> bool:
    import importlib

    app = importlib.import_module("api.app")
    from services import reader_state

    if not source_id:
        raise LookupError(_UNAVAILABLE_REASON)
    if source_id in source_visibility.reader_unavailable_source_ids(session):
        raise LookupError(_UNAVAILABLE_REASON)
    if user_sources.is_user_source(source_id):
        if user_sources.get_user_source(session, source_id) is None:
            raise LookupError(_UNAVAILABLE_REASON)
    already = source_id in set(
        app.resolve_subscribed_source_ids(session, username, include_hidden=True)
    )
    if already:
        return False
    app._create_single_source_subscription(session, username, source_id, name or source_id)
    reader_state.init_cursor_with_backlog(session, username=username, source_id=source_id)
    return True


def _mark_subscribed(item: Dict[str, Any]) -> None:
    item["status"] = "already_subscribed"
    item["reason"] = "已在订阅中"
    item["selectable"] = False


async def _apply_new(session: Session, username: str, item: Dict[str, Any]) -> str:
    """复用自定源创建路径。返回 created / subscribed / skipped。"""
    from sqlalchemy.exc import IntegrityError

    canonical = item["canonical_url"]
    try:
        preview = await user_sources.fetch_feed_preview(canonical)
    except SSRFError:
        item["status"] = "rejected_ssrf"
        item["reason"] = _SSRF_REASON
        item["selectable"] = False
        return "skipped"
    except ValueError as exc:
        item["status"] = "invalid"
        item["reason"] = _safe_value_reason(exc)
        item["selectable"] = False
        return "skipped"
    except Exception:  # noqa: BLE001
        logger.warning("OPML confirm fetch failed for host %s", urlsplit(canonical).hostname)
        item["status"] = "invalid"
        item["reason"] = _FETCH_FAIL_REASON
        item["selectable"] = False
        return "skipped"

    requested_podcast = "podcast" in (item.get("category") or "").lower()
    detected = preview.get("detected_kind") or "article"
    kind = "podcast" if requested_podcast else detected
    if kind not in user_sources.USER_SOURCE_KINDS:
        kind = "article"
    playable = int(preview.get("playable_entry_count") or preview.get("audio_entry_count") or 0)
    if kind == "podcast" and not playable:
        item["status"] = "invalid"
        item["reason"] = "该 feed 未检测到可播放的音频条目"
        item["selectable"] = False
        return "skipped"

    name = item["title"] if item["title"] != (urlsplit(canonical).hostname or "") else ""
    if not name:
        name = _clean_text(preview.get("feed_title") or "")

    with user_sources._WRITE_LOCK:  # noqa: SLF001 - 与单条添加同一把锁
        try:
            prepared = user_sources.prepare_user_source(
                session, username, canonical, name=name, content_kind=kind,
            )
        except user_sources.UserSourceQuotaError as exc:
            session.rollback()
            item["status"] = "invalid"
            item["reason"] = str(exc)
            item["selectable"] = False
            return "skipped"
        except ValueError as exc:
            session.rollback()
            item["status"] = "invalid"
            item["reason"] = _safe_value_reason(exc)
            item["selectable"] = False
            return "skipped"
        if prepared.get("blocked"):
            session.rollback()
            item["status"] = "invalid"
            item["reason"] = _UNAVAILABLE_REASON
            item["selectable"] = False
            return "skipped"
        if prepared.get("existing_system"):
            existing = prepared["existing_system"]
            try:
                subscribed = _subscribe_existing(
                    session, username, existing["source_id"], existing.get("name") or item["title"],
                )
                session.commit()
            except LookupError as exc:
                session.rollback()
                item["status"] = "invalid"
                item["reason"] = str(exc) or _UNAVAILABLE_REASON
                item["selectable"] = False
                return "skipped"
            item["source_id"] = existing["source_id"]
            item["matched_name"] = existing.get("name") or ""
            _mark_subscribed(item)
            return "subscribed" if subscribed else "skipped"

        source_id = prepared["source_id"]
        record = prepared["record"]
        created = bool(prepared.get("created"))
        try:
            subscribed = _subscribe_existing(session, username, source_id, record.name or item["title"])
            session.commit()
        except IntegrityError:
            session.rollback()
            from models.db import SourceConfigRecord

            record = session.get(SourceConfigRecord, source_id)
            if record is None:
                item["status"] = "invalid"
                item["reason"] = "添加冲突，请重试"
                item["selectable"] = False
                return "skipped"
            subscribed = _subscribe_existing(session, username, source_id, record.name or item["title"])
            session.commit()
            created = False
        except LookupError as exc:
            session.rollback()
            item["status"] = "invalid"
            item["reason"] = str(exc) or _UNAVAILABLE_REASON
            item["selectable"] = False
            return "skipped"
        item["source_id"] = source_id
        item["content_kind"] = kind
        _mark_subscribed(item)
        if created:
            return "created"
        return "subscribed" if subscribed else "skipped"


async def _apply_match(session: Session, username: str, item: Dict[str, Any]) -> str:
    source_id = item.get("source_id") or ""
    lock = user_sources._WRITE_LOCK if user_sources.is_user_source(source_id) else None  # noqa: SLF001
    if lock is not None:
        lock.acquire()
    try:
        subscribed = _subscribe_existing(
            session,
            username,
            source_id,
            item.get("matched_name") or item.get("title") or source_id,
        )
        if subscribed:
            session.commit()
        _mark_subscribed(item)
        return "subscribed" if subscribed else "skipped"
    except LookupError as exc:
        session.rollback()
        item["status"] = "invalid"
        item["reason"] = str(exc) or _UNAVAILABLE_REASON
        item["selectable"] = False
        return "skipped"
    finally:
        if lock is not None:
            lock.release()


async def import_subscriptions(
    session: Session,
    username: str,
    text: str,
    *,
    dry_run: bool = True,
    urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """预览或确认导入。dry_run 不写库;确认只处理选中的 new / matched_existing。"""
    outlines = parse_opml_outlines(text)
    unique, duplicate_count = _dedupe_outlines(outlines)
    items, quota = await _classify(session, username, unique)
    if dry_run:
        return _response(items, dry_run=True, duplicate_count=duplicate_count, quota=quota)

    selected = _selected_urls(urls, items)
    created_count = 0
    subscribed_count = 0
    created_source_ids: List[str] = []
    for item in items:
        if item.get("canonical_url") not in selected:
            continue
        if item.get("status") not in {"new", "matched_existing"}:
            continue
        try:
            if item["status"] == "matched_existing":
                outcome = await _apply_match(session, username, item)
            else:
                outcome = await _apply_new(session, username, item)
        except Exception:  # noqa: BLE001 - 单条失败不让整批变成 500
            logger.warning(
                "OPML import item failed host=%s",
                urlsplit(item.get("canonical_url") or "").hostname,
                exc_info=True,
            )
            session.rollback()
            item["status"] = "invalid"
            item["reason"] = _ITEM_FAIL_REASON
            item["selectable"] = False
            continue
        if outcome == "created":
            created_count += 1
            if item.get("source_id"):
                created_source_ids.append(item["source_id"])
        elif outcome == "subscribed":
            subscribed_count += 1
    return _response(
        items,
        dry_run=False,
        duplicate_count=duplicate_count,
        created_count=created_count,
        subscribed_count=subscribed_count,
        created_source_ids=created_source_ids,
        quota=_quota_snapshot(session, username),
    )


async def fetch_imported_user_sources(source_ids: List[str]) -> None:
    """确认导入后的首抓。与单条添加同一条抓取入口,失败不撤销已建成的订阅。"""
    import importlib

    app = importlib.import_module("api.app")
    from api.routers.source_configs import build_source_fetch_params, resolve_source_fetcher_id

    for source_id in source_ids:
        with Session(app.db_sink.engine) as session:
            record = user_sources.get_user_source(session, source_id)
            if record is None or record.retired_at:
                continue
            fetch_params = build_source_fetch_params(record, {})
            fetcher_id = resolve_source_fetcher_id(record)
            name = record.name
        if not fetcher_id:
            continue
        try:
            await app.run_single_fetch_as_collection(
                fetcher_id,
                fetch_params,
                name=f"自定源首抓: {name}",
                trigger_type="manual",
                run_scope="ad_hoc",
            )
        except Exception:  # noqa: BLE001 - 首抓失败留给定时调度重试
            logger.warning("OPML 导入首抓失败: %s", source_id)
            continue
        with user_sources._WRITE_LOCK:  # noqa: SLF001
            with Session(app.db_sink.engine) as check_session:
                current = user_sources.get_user_source(check_session, source_id)
                if current is None or current.retired_at:
                    user_sources.dispose_user_source(check_session, source_id)
                    check_session.commit()
