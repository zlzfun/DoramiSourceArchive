"""推送绑定(issue #344):哪个收录令牌可以往哪个源推,以及登记一个不带抓取的源。

推送绑定是源的一种获取方式,与源行上的抓取绑定(``fetcher_id`` + ``params_json``)并列:抓取是平台去拿,
推送是别人送来。绑定行只在收录入口所在的节点,不随 Archive Sync 同步。

- 令牌只能往它绑定的源推(``bound_source_ids`` → ``ingest_push.resolve_bound_source``);
- 登记(``register_source``)建一个没有抓取绑定的源行:给了账号 / 站点 / feed 的是定向源,沿用收录通道的
  身份派生;不给的是合集源,id 是 ``push_<后缀>``。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Optional

from sqlmodel import Session, select

from models.db import IngestBindingRecord, IngestTokenRecord, SourceConfigRecord
from services import ingest_policy, ingest_push, source_catalog, source_identity, source_rows

ADMISSION_LISTED = "incubating"
_SUFFIX = re.compile(r"^[a-z0-9_]{3,40}$")
MAX_NAME_CHARS = 120


class BindingError(ValueError):
    """绑定或登记做不了;``status`` 给路由层用,``existing_source_id`` 是「这个出处已经有源」时的那个源。"""

    def __init__(self, message: str, *, status: int = 400, existing_source_id: str = ""):
        super().__init__(message)
        self.status = status
        self.existing_source_id = existing_source_id


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ── 绑定 ────────────────────────────────────────────────────────────────────


def bound_source_ids(session: Session, token_id: int) -> frozenset[str]:
    return frozenset(session.exec(
        select(IngestBindingRecord.source_id).where(IngestBindingRecord.token_id == token_id)
    ).all())


def unbindable_reason(session: Session, source_id: str) -> Optional[str]:
    """这个源不能挂推送绑定的原因;可以时返回 None。已下架的源可以绑:恢复后生效。"""
    reserved = ingest_policy.reserved_source_id_reason(source_id)
    if reserved:
        return reserved
    view = source_catalog.get(session, source_id)
    if not view.known:
        return "来源不存在"
    if view.is_private:
        return "读者自定源不接受推送"
    if view.retired:
        return "该来源已下线"
    # 带同步印记的源照常可以绑:印记只是「上游也有这个源」的簿记(issue #263),收录写进去的是本机版本。
    return None


def _active_token(session: Session, token_id: int) -> IngestTokenRecord:
    token = session.get(IngestTokenRecord, token_id)
    if token is None:
        raise BindingError("令牌不存在", status=404)
    if not token.is_active or token.revoked_at:
        raise BindingError("令牌已吊销")
    return token


def bind(session: Session, token_id: int, source_id: str, *, created_by: str = "") -> IngestBindingRecord:
    """把源绑定到令牌;已绑定的原样返回。调用方提交。"""
    _active_token(session, token_id)
    sid = str(source_id or "").strip()
    reason = unbindable_reason(session, sid)
    if reason:
        raise BindingError(reason)
    existing = session.exec(
        select(IngestBindingRecord).where(
            IngestBindingRecord.token_id == token_id, IngestBindingRecord.source_id == sid
        )
    ).first()
    if existing is not None:
        return existing
    record = IngestBindingRecord(
        source_id=sid, token_id=token_id, created_by=str(created_by or ""), created_at=_now()
    )
    session.add(record)
    session.flush()
    return record


def unbind(session: Session, token_id: int, source_id: str) -> bool:
    record = session.exec(
        select(IngestBindingRecord).where(
            IngestBindingRecord.token_id == token_id, IngestBindingRecord.source_id == str(source_id or "").strip()
        )
    ).first()
    if record is None:
        return False
    session.delete(record)
    return True


def drop_for_source(session: Session, source_id: str) -> int:
    """删掉某个源的全部绑定(源被物理删除时同事务调用)。调用方提交。"""
    records = session.exec(
        select(IngestBindingRecord).where(IngestBindingRecord.source_id == str(source_id or "").strip())
    ).all()
    for record in records:
        session.delete(record)
    return len(records)


def touch(session: Session, token_id: int, source_ids: Iterable[str]) -> None:
    """记「最近一批」:这个令牌刚有条目被这些源接受。调用方提交。"""
    ids = sorted({str(sid) for sid in source_ids if sid})
    if not ids:
        return
    now = _now()
    for record in session.exec(
        select(IngestBindingRecord).where(
            IngestBindingRecord.token_id == token_id, IngestBindingRecord.source_id.in_(ids)
        )
    ).all():
        record.last_pushed_at = now
        session.add(record)


def by_token(session: Session) -> dict[int, list[dict[str, Any]]]:
    """令牌 id → 它绑定的源(名称、最近一批),按绑定先后。"""
    out: dict[int, list[dict[str, Any]]] = {}
    for record in session.exec(select(IngestBindingRecord).order_by(IngestBindingRecord.id)).all():
        view = source_catalog.get(session, record.source_id)
        out.setdefault(record.token_id, []).append({
            "source_id": record.source_id,
            "name": view.name or record.source_id,
            "shape": view.shape,
            "open": record.source_id.startswith(ingest_push.OPEN_SOURCE_PREFIX),
            "last_pushed_at": record.last_pushed_at,
            "created_at": record.created_at,
        })
    return out


def by_source(session: Session) -> dict[str, list[dict[str, Any]]]:
    """源 id → 在推它的令牌(调用方、尾号、是否有效、最近一批)。吊销的令牌也列出,界面上标出来。"""
    tokens = {row.id: row for row in session.exec(select(IngestTokenRecord)).all()}
    out: dict[str, list[dict[str, Any]]] = {}
    for record in session.exec(select(IngestBindingRecord).order_by(IngestBindingRecord.id)).all():
        token = tokens.get(record.token_id)
        if token is None:
            continue
        out.setdefault(record.source_id, []).append({
            "token_id": token.id,
            "actor": token.actor,
            "token_preview": token.token_preview,
            "is_active": bool(token.is_active and not token.revoked_at),
            "last_pushed_at": record.last_pushed_at,
        })
    return out


# ── 登记一个不带抓取的源 ────────────────────────────────────────────────────


def _open_source_id(session: Session, suffix: str) -> str:
    suffix = str(suffix or "").strip().lower()
    if not _SUFFIX.match(suffix):
        raise BindingError("源 id 的后缀用小写字母、数字、下划线,3–40 位")
    source_id = f"{ingest_push.OPEN_SOURCE_PREFIX}{suffix}"
    if session.get(SourceConfigRecord, source_id) is not None or source_catalog.registry_view(source_id) is not None:
        raise BindingError(f"来源 {source_id} 已存在", status=409, existing_source_id=source_id)
    return source_id


def register_source(
    session: Session,
    *,
    kind: str,
    name: str,
    handle: str = "",
    user_id: str = "",
    url: str = "",
    feed_url: str = "",
    source_id_suffix: str = "",
    avatar_url: str = "",
    homepage_url: str = "",
    description: str = "",
    listed: bool = True,
) -> SourceConfigRecord:
    """建一个没有抓取绑定的源行(``origin=ingest``)。调用方提交。

    给了出处(账号 / 站点 / feed)的沿用收录通道的身份派生,id 与抓取器的公式一致;这个出处已经有源时
    抛 ``BindingError(status=409)`` 并带上已有的源 id。不给出处的是合集源,id 是 ``push_<后缀>``。
    """
    if kind not in ingest_push.KINDS:
        raise BindingError(f"kind 必须是 {' / '.join(ingest_push.KINDS)}")
    name = re.sub(r"\s+", " ", str(name or "").strip())[:MAX_NAME_CHARS]
    if not name:
        raise BindingError("名称不能为空")
    content_type = ingest_push.CONTENT_TYPE_BY_KIND[kind]
    admission = ADMISSION_LISTED if listed else source_catalog.ADMISSION_PROVISIONAL
    avatar = str(avatar_url or "").strip()
    homepage = str(homepage_url or "").strip()
    handle = str(handle or "").strip().lstrip("@")
    user_id = str(user_id or "").strip()
    url = str(url or "").strip()
    feed_url = str(feed_url or "").strip()
    if kind == "social_post":
        described = bool(handle or user_id)
        if described and not source_identity.normalize_handle(handle):
            raise BindingError("账号源需要账号名")
        source = {"platform": "x", "handle": handle, "user_id": user_id}
    elif kind == "podcast_episode":
        if not feed_url:
            raise BindingError("播客源需要节目 feed 地址")
        described = True
        source = {"feed_url": feed_url}
    else:
        described = bool(url or feed_url)
        source = {"url": url, "feed_url": feed_url}

    if not described:
        source_id = _open_source_id(session, source_id_suffix)
        row = source_rows.ensure_source_row(
            session, source_id,
            content_type=content_type,
            origin=source_catalog.ORIGIN_INGEST,
            admission_status=admission,
            name=name,
            platform="x" if kind == "social_post" else "",
            avatar_url=avatar,
            homepage_url=homepage,
            description=description,
        )
        return row

    ctx = ingest_push.new_context(session, "")
    try:
        resolved = ingest_push.resolve_source(ctx, kind, source)
    except ValueError as exc:  # IngestRejected,以及身份派生对清洗后为空的账号名抛的 ValueError
        raise BindingError(str(exc)) from exc
    if not resolved.created:
        raise BindingError(
            f"这个出处已经有来源 {resolved.source_id},直接绑定它即可",
            status=409, existing_source_id=resolved.source_id,
        )
    hint = dict(resolved.hint)
    podcast_feed = hint.pop("podcast_feed_url", "")
    hint.update({"name": name, "admission_status": admission})
    if kind == "article" and feed_url:
        # 文章源按 feed 登记:留下 feed 身份键,之后带同一个 feed_url 的条目(文章链接可以在别的主机上)
        # 才认得回来(检视 R1 #4)。
        hint["identity_keys"] = [*hint.get("identity_keys", []), ingest_push.feed_identity_key(feed_url)]
    if avatar:
        hint["avatar_url"] = avatar
    if homepage:
        hint["homepage_url"] = homepage
    row = source_rows.ensure_source_row(
        session, resolved.source_id, content_type=content_type, description=description, **hint
    )
    if row is not None and podcast_feed:
        row.url = podcast_feed
        session.add(row)
    return row
