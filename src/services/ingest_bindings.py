"""推送绑定(issue #344):哪个收录令牌可以往哪个源推,以及登记一个不带抓取的源。

推送绑定是源的一种获取方式,与源行上的抓取绑定(``fetcher_id`` + ``params_json``)并列:抓取是平台去拿,
推送是别人送来。绑定行只在收录入口所在的节点,不随 Archive Sync 同步。

- 令牌只能往它绑定的源推(``bound_source_ids`` → ``ingest_push.resolve_bound_source``);
- 登记(``register_source``)建一个没有抓取绑定的源行:给了账号 / 站点 / feed 的是定向源,沿用收录通道的
  身份派生;不给的是合集源,id 是 ``push_<后缀>``。
- 有权限的令牌可以自己登记(``register_by_token``):登记的源先是待列入,自动绑定给它,每天有上限。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from sqlmodel import Session, select

from models.db import IngestBindingRecord, IngestTokenRecord, SourceConfigRecord
from services import ingest_policy, ingest_push, source_catalog, source_identity, source_rows

ADMISSION_LISTED = "incubating"
_SUFFIX = re.compile(r"^[a-z0-9_]{3,40}$")
MAX_NAME_CHARS = 120
MAX_URL_CHARS = 2000


class BindingError(ValueError):
    """绑定或登记做不了;``status`` 给路由层用,``existing_source_id`` 是「这个出处已经有源」时的那个源。"""

    def __init__(self, message: str, *, status: int = 400, existing_source_id: str = ""):
        super().__init__(message)
        self.status = status
        self.existing_source_id = existing_source_id
        # 令牌自己登记、出处已经有源时:这个源是不是已经绑定给它了(None = 不适用)。
        self.bound: Optional[bool] = None


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
            # 待列入(provisional)的来源在令牌抽屉里带「临时」徽标:令牌自己登记的源都从这里起步。
            "admission_status": view.admission_status,
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


def _http_url(value: str, label: str) -> str:
    """登记时给的地址:空串放行,其余必须是 http(s)。

    这些地址会成为源的主页链接与头像,在管理面和读者面直接当链接 / 图片地址用;令牌也能登记来源之后,
    不能让 ``javascript:`` 之类的东西落进源行。
    """
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if parts is None or parts.scheme.casefold() not in ("http", "https") or not parts.hostname:
        raise BindingError(f"{label}要是 http(s) 地址")
    if len(url) > MAX_URL_CHARS:
        raise BindingError(f"{label}太长(最多 {MAX_URL_CHARS} 个字符)")
    return url


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
    avatar = _http_url(avatar_url, "头像地址")
    homepage = _http_url(homepage_url, "主页地址")
    handle = str(handle or "").strip().lstrip("@")
    user_id = str(user_id or "").strip()
    url = _http_url(url, "站点地址")
    feed_url = _http_url(feed_url, "feed 地址")
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


# ── 令牌自己登记来源(issue #344 第二个 PR) ──────────────────────────────────

SELF_REGISTERED_BY = "ingest:"  # 绑定行 created_by 的前缀:是令牌自己登记时绑上的,后面跟调用方名


def register_by_token(session: Session, token: IngestTokenRecord, **fields: Any) -> SourceConfigRecord:
    """令牌自己登记一个来源:先是待列入(临时源状态),并绑定给这个令牌。调用方提交,出错时调用方回滚。

    - 是令牌的一项权限,默认没有;
    - 只能新建:出处已经有源时抛 409,``existing_source_id`` 给出那个源,``bound`` 说明它是否已经绑定
      给这个令牌(绑了就直接推,没绑要管理员绑);
    - 每个令牌每天(UTC)最多登记 ``ingest_push.REGISTER_DAILY_LIMIT`` 个。名额先占、失败回滚时退还。
    """
    if not token.can_register_sources:
        raise BindingError("这个令牌没有登记来源的权限;请管理员在运维「自定源与收录」里打开", status=403)
    if not ingest_push.reserve_registration(session, token.id):
        raise BindingError(
            f"今天登记的来源已到上限({ingest_push.REGISTER_DAILY_LIMIT} 个);明天(UTC)再登记", status=429
        )
    try:
        row = register_source(session, listed=False, **fields)
    except BindingError as exc:
        if not exc.existing_source_id:
            raise
        bound = exc.existing_source_id in bound_source_ids(session, token.id)
        conflict = BindingError(
            f"这个出处已经有来源 {exc.existing_source_id}"
            + (",这个令牌已经绑定,直接推送即可" if bound else ";请管理员把它绑定给这个令牌"),
            status=409, existing_source_id=exc.existing_source_id,
        )
        conflict.bound = bound
        raise conflict from exc
    if row is None:
        raise BindingError("登记失败:这个 id 不能用作来源")
    bind(session, token.id, row.source_id, created_by=f"{SELF_REGISTERED_BY}{token.actor}")
    return row
