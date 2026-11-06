"""Agent 收录推送通道(issue #262 波 1)。

外部主动式 Agent 自己发现文章 / 推文 / 播客单集,推进平台。它常常来自平台从未定义过的出处,
所以这条通道的职责是:**认出已有源,认不出就建临时源**,并且由服务端决定一切会影响下游判定的
字段(条目 id、``content_type``、系统扩展键、来路)。

- 鉴权:带作用域的收录令牌(``ding_``,只存 HMAC hash,绑定一个 actor 名,日额度为软上限);
- 身份解析:显式 ``source_id`` → 身份键(X 的 ``user_id`` / ``handle``)→ feed 地址 → 主机 + 路径前缀;
  都不中则建 ``provisional`` 临时源(不进发现目录、不计入多源印证、默认不进公共日报);
- 条目 id 与抓取器同一公式:X ``{sid}_{post_id}``;网页 / RSS ``{sid}_{sha1(guid 或链接)[:16]}``;
  播客 ``{sid}_{sha1(guid 或音频地址)[:16]}``——Agent 先推、抓取器后抓(或反之)天然幂等合流;
- 跨源 URL 去重:同一链接已在别的源里 → 不新建,返回 ``duplicate_url``,只记一条收录记录;
- 逐条结果:``created | duplicate | duplicate_url | refreshed | rejected``。

接收端(内网 replica)直接拒绝:Agent 只推外网 authority。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from sqlalchemy import func
from sqlmodel import Session, select

from models.content import BaseContent, PodcastEpisodeContent, SocialPostContent, WebPageArticleContent
from models.db import (
    ArticlePickRecord,
    ArticleRecord,
    IngestTokenRecord,
    SourceConfigRecord,
    SourceIdentityKeyRecord,
)
from services import ingest_policy, source_catalog, source_identity
from services.source_visibility import hidden_source_ids


TOKEN_PREFIX = "ding_"
CHANNEL_PUSH = "push"
MAX_BATCH = 100
DEFAULT_DAILY_LIMIT = 500

KINDS = ("article", "social_post", "podcast_episode")
CONTENT_TYPE_BY_KIND = {
    "article": "web_article",
    "social_post": "social_post",
    "podcast_episode": "podcast_episode",
}

# 多租户主机:一个主机上住着许多互不相干的作者,按「主机 + 首段路径」建源(github.com/openai)。
# 一作者一子域的平台(substack、blogspot 等)按主机即可,不在名单里。
MULTI_TENANT_HOSTS = frozenset({
    "github.com",
    "gitlab.com",
    "medium.com",
    "huggingface.co",
    "youtube.com",
    "www.youtube.com",
    "arxiv.org",
    "x.com",
    "twitter.com",
    "reddit.com",
    "www.reddit.com",
    "news.ycombinator.com",
    "zhihu.com",
    "www.zhihu.com",
    "zhuanlan.zhihu.com",
    "mp.weixin.qq.com",
    "weibo.com",
    "bilibili.com",
    "www.bilibili.com",
    "linkedin.com",
    "www.linkedin.com",
})

# 推送载荷里允许落进 extensions 的键(按 kind 白名单);系统键(社区信号、翻译回执、抽取方式、
# 来路)一律剥离——它们会被分析 / 论文全文 / 翻译当成服务端事实(方案 §3 C7)。
EXTENSION_KEYS = {
    "article": frozenset({"author", "site_name", "summary", "tags", "language", "image_url"}),
    "social_post": frozenset({"lang", "tags", "media_urls"}),
    "podcast_episode": frozenset({"author", "tags", "image_url", "episode", "season"}),
}

_UNSAFE_ID = re.compile(r"[^a-z0-9_]+")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class IngestRejected(ValueError):
    """单条拒收:原因直接进逐条回执。"""


# ── 令牌 ────────────────────────────────────────────────────────────────────


def _secret() -> str:
    from api.tokens import AUTH_SECRET

    return AUTH_SECRET


def hash_token(token: str) -> str:
    return hmac.new(_secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def create_token(
    session: Session,
    *,
    actor: str,
    created_by: str,
    daily_limit: int = DEFAULT_DAILY_LIMIT,
) -> tuple[IngestTokenRecord, str]:
    actor = re.sub(r"\s+", " ", str(actor or "").strip())[:60]
    if not actor:
        raise ValueError("actor 不能为空")
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    record = IngestTokenRecord(
        actor=actor,
        token_hash=hash_token(token),
        token_preview=f"...{token[-6:]}",
        daily_limit=max(1, min(int(daily_limit or DEFAULT_DAILY_LIMIT), 10000)),
        is_active=True,
        created_by=str(created_by or ""),
        created_at=_now(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record, token


def revoke_token(session: Session, token_id: int) -> Optional[IngestTokenRecord]:
    record = session.get(IngestTokenRecord, token_id)
    if record is None:
        return None
    record.is_active = False
    record.revoked_at = record.revoked_at or _now()
    session.add(record)
    session.commit()
    return record


def authenticate(session: Session, raw_token: str) -> Optional[IngestTokenRecord]:
    token = str(raw_token or "").strip()
    if not token.startswith(TOKEN_PREFIX):
        return None
    record = session.exec(
        select(IngestTokenRecord).where(IngestTokenRecord.token_hash == hash_token(token))
    ).first()
    if record is None or not record.is_active or record.revoked_at:
        return None
    return record


def serialize_token(record: IngestTokenRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "actor": record.actor,
        "token_preview": record.token_preview,
        "daily_limit": record.daily_limit,
        "is_active": bool(record.is_active and not record.revoked_at),
        "created_by": record.created_by,
        "created_at": record.created_at,
        "last_used_at": record.last_used_at,
        "revoked_at": record.revoked_at,
    }


def picks_today(session: Session, actor: str) -> int:
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    return int(session.exec(
        select(func.count()).select_from(ArticlePickRecord).where(
            ArticlePickRecord.actor == actor,
            ArticlePickRecord.picked_at >= day,
        )
    ).one() or 0)


# ── URL 与身份 ──────────────────────────────────────────────────────────────


def canonical_url(value: str) -> str:
    from services.event_assignment import canonical_url as _canonical

    return _canonical(value)


def _host(url: str) -> str:
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").casefold()
    except ValueError:
        return ""
    return host


def _site_key(url: str) -> str:
    """站点身份:多租户主机取「主机 + 首段路径」,其余取主机(去 www.)。"""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold()
    if not host:
        return ""
    if host in MULTI_TENANT_HOSTS:
        segment = next((seg for seg in (parts.path or "").split("/") if seg), "")
        return f"{host}/{segment.casefold()}" if segment else host
    return host[4:] if host.startswith("www.") else host


def _derive_web_source_id(site_key: str) -> str:
    slug = _UNSAFE_ID.sub("_", site_key.replace(".", "_").replace("/", "_").casefold()).strip("_")
    if not slug:
        raise IngestRejected("无法从链接确定来源站点")
    return f"web_{slug}"[:80]


@dataclass
class _WebIndex:
    feeds: dict[str, str] = field(default_factory=dict)            # 规范 feed URL → source_id
    prefixes: list[tuple[str, str, str]] = field(default_factory=list)  # (host, path 前缀, source_id)


def _web_index(session: Session) -> _WebIndex:
    index = _WebIndex()

    def add_prefix(url: str, source_id: str) -> None:
        try:
            parts = urlsplit(str(url or "").strip())
        except ValueError:
            return
        host = (parts.hostname or "").casefold()
        if not host:
            return
        path = re_slash(parts.path or "/")
        if host in MULTI_TENANT_HOSTS and path in {"", "/"}:
            return  # github.com 根路径不代表任何一个源
        index.prefixes.append((host[4:] if host.startswith("www.") else host, path, source_id))

    for source_id in source_catalog.registry_source_ids():
        view = source_catalog.registry_view(source_id)
        try:
            from fetchers.registry import fetcher_registry

            cls = fetcher_registry.get_class(source_id)
        except Exception:  # noqa: BLE001
            cls = None
        feed = str(getattr(cls, "feed_url", "") or "") if cls is not None else ""
        if feed:
            index.feeds.setdefault(canonical_url(feed), source_id)
        for url in (view.base_url if view else "", getattr(cls, "listing_url", "") if cls else ""):
            if url:
                add_prefix(str(url), source_id)
    for row in session.exec(select(SourceConfigRecord)).all():
        if (row.owner_username or "").strip() or row.source_id.startswith(source_catalog.USER_SOURCE_PREFIX):
            continue
        if row.retired_at:
            continue
        if (row.source_type or "").strip().lower() in {"rss", "atom", "podcast", "podcast_rss"} and row.url:
            index.feeds.setdefault(canonical_url(row.url), row.source_id)
        for url in (row.homepage_url, row.base_url, row.url if (row.source_type or "") in {"web", "webpage"} else ""):
            if url:
                add_prefix(url, row.source_id)
    # 长前缀优先:anthropic.com/news 先于 anthropic.com。
    index.prefixes.sort(key=lambda item: (-len(item[1]), item[0]))
    return index


def re_slash(path: str) -> str:
    path = re.sub(r"/{2,}", "/", path or "/")
    return path.rstrip("/") or "/"


def _match_prefix(index: _WebIndex, url: str) -> Optional[str]:
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    host = (parts.hostname or "").casefold()
    host = host[4:] if host.startswith("www.") else host
    path = re_slash(parts.path or "/")
    for prefix_host, prefix_path, source_id in index.prefixes:
        if prefix_host != host:
            continue
        if prefix_path == "/" or path == prefix_path or path.startswith(prefix_path + "/"):
            return source_id
    return None


@dataclass
class ResolvedSource:
    source_id: str
    created: bool = False
    hint: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchContext:
    session: Session
    actor: str
    x_index: dict[str, str]
    web_index: _WebIndex
    hidden: set[str]
    planned: dict[str, ResolvedSource] = field(default_factory=dict)  # 本批次新建的临时源


def new_context(session: Session, actor: str) -> BatchContext:
    return BatchContext(
        session=session,
        actor=actor,
        x_index=source_identity.x_identity_index(session),
        web_index=_web_index(session),
        hidden=hidden_source_ids(session),
    )


def _existing_identity(session: Session, key: str) -> Optional[str]:
    row = session.get(SourceIdentityKeyRecord, key)
    return row.source_id if row is not None else None


def _check_target(ctx: BatchContext, source_id: str) -> None:
    """已有源能否接受推送。"""
    reserved = ingest_policy.reserved_source_id_reason(source_id)
    if reserved:
        raise IngestRejected(reserved)
    view = source_catalog.get(ctx.session, source_id)
    if view.is_private:
        raise IngestRejected("读者自定源不接受推送")
    if view.synced:
        raise IngestRejected("该来源由远端权威节点管理")
    if view.retired:
        raise IngestRejected("该来源已下线")
    if source_id in ctx.hidden:
        raise IngestRejected("该来源已下架,拒收推送")


def _unique_new_id(ctx: BatchContext, base: str) -> str:
    candidate = base
    suffix = 2
    while (
        ctx.session.get(SourceConfigRecord, candidate) is not None
        or source_catalog.registry_view(candidate) is not None
    ):
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def resolve_source(ctx: BatchContext, kind: str, source: dict[str, Any], *, item_url: str = "") -> ResolvedSource:
    explicit = str(source.get("source_id") or "").strip()
    if explicit:
        view = source_catalog.get(ctx.session, explicit)
        if not view.known:
            raise IngestRejected("source_id 不存在;未登记的出处请给平台账号或站点链接")
        _check_target(ctx, explicit)
        return ResolvedSource(source_id=explicit)

    name = str(source.get("name") or "").strip()
    avatar = str(source.get("avatar_url") or "").strip()

    if kind == "social_post":
        platform = str(source.get("platform") or "x").strip().casefold() or "x"
        handle = str(source.get("handle") or "").strip()
        user_id = str(source.get("user_id") or "").strip()
        keys = source_identity.social_identity_keys(platform, handle=handle, user_id=user_id)
        for key in keys:
            if key in ctx.planned:
                return ctx.planned[key]
            hit = ctx.x_index.get(key) if platform == "x" else _existing_identity(ctx.session, key)
            if hit:
                _check_target(ctx, hit)
                return ResolvedSource(source_id=hit)
        if not source_identity.normalize_handle(handle):
            raise IngestRejected("社交帖需要作者 handle")
        derived = source_identity.derive_social_source_id(platform, handle)
        if ctx.session.get(SourceConfigRecord, derived) is not None or source_catalog.registry_view(derived):
            # 同名 id 已存在但身份键没对上(例如旧导入行):沿用,不再造一个平行源。
            _check_target(ctx, derived)
            return ResolvedSource(source_id=derived)
        display = name or handle.lstrip("@")
        resolved = ResolvedSource(source_id=derived, created=True, hint={
            "origin": source_catalog.ORIGIN_AGENT,
            "admission_status": source_catalog.ADMISSION_PROVISIONAL,
            "name": f"X · {display}" if platform == "x" else display,
            "platform": platform,
            "avatar_url": avatar,
            "homepage_url": f"https://x.com/{handle.lstrip('@')}" if platform == "x" else "",
            "identity_keys": keys,
        })
        for key in keys:
            ctx.planned[key] = resolved
        return resolved

    feed_url = str(source.get("feed_url") or "").strip()
    site = str(source.get("url") or source.get("site") or "").strip()
    if feed_url:
        hit = ctx.web_index.feeds.get(canonical_url(feed_url))
        if hit:
            _check_target(ctx, hit)
            return ResolvedSource(source_id=hit)
    lookup_url = site or item_url
    if lookup_url and kind != "podcast_episode":
        hit = _match_prefix(ctx.web_index, lookup_url)
        if hit:
            _check_target(ctx, hit)
            return ResolvedSource(source_id=hit)
    anchor = feed_url or lookup_url
    if not anchor:
        raise IngestRejected("缺少出处:给 source_id、平台账号或站点 / feed 链接")
    if kind == "podcast_episode":
        if not feed_url:
            raise IngestRejected("播客单集需要节目 feed 地址")
        key = source_identity.identity_key("podcast", "feed", canonical_url(feed_url))
        base = f"podcast_{_UNSAFE_ID.sub('_', _site_key(feed_url).replace('.', '_'))}"[:60].rstrip("_")
        base = f"{base}_{hashlib.sha1(canonical_url(feed_url).encode('utf-8')).hexdigest()[:6]}"
    else:
        site_key = _site_key(lookup_url or feed_url)
        key = source_identity.identity_key("web", "site", site_key)
        base = _derive_web_source_id(site_key)
    if key in ctx.planned:
        return ctx.planned[key]
    existing = _existing_identity(ctx.session, key)
    if existing:
        _check_target(ctx, existing)
        return ResolvedSource(source_id=existing)
    source_id = _unique_new_id(ctx, base)
    homepage = site or (f"https://{_host(lookup_url or feed_url)}" if (lookup_url or feed_url) else "")
    resolved = ResolvedSource(source_id=source_id, created=True, hint={
        "origin": source_catalog.ORIGIN_AGENT,
        "admission_status": source_catalog.ADMISSION_PROVISIONAL,
        "name": name or _site_key(lookup_url or feed_url),
        "avatar_url": avatar,
        "homepage_url": homepage,
        "identity_keys": [key],
    })
    if kind == "podcast_episode":
        resolved.hint["podcast_feed_url"] = feed_url
    ctx.planned[key] = resolved
    return resolved


# ── 条目 ────────────────────────────────────────────────────────────────────


def _sha16(value: str) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]


def item_id_for(kind: str, source_id: str, item: dict[str, Any]) -> str:
    if kind == "social_post":
        post_id = str(item.get("post_id") or "").strip()
        if not post_id:
            raise IngestRejected("社交帖需要 post_id")
        return f"{source_id}_{post_id}"
    if kind == "podcast_episode":
        stable = str(item.get("guid") or item.get("audio_url") or "").strip()
        if not stable:
            raise IngestRejected("播客单集需要 guid 或音频地址")
        return f"{source_id}_{_sha16(stable)}"
    stable = str(item.get("guid") or item.get("url") or "").strip()
    if not stable:
        raise IngestRejected("文章需要链接")
    return f"{source_id}_{_sha16(stable)}"


def _extensions(kind: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    allowed = EXTENSION_KEYS[kind]
    return {key: value for key, value in raw.items() if key in allowed}


def build_content(kind: str, source_id: str, item: dict[str, Any]) -> BaseContent:
    item_id = item_id_for(kind, source_id, item)
    url = str(item.get("url") or "").strip()
    publish_date = str(item.get("publish_date") or "").strip() or _now()
    text = str(item.get("content") or item.get("text") or "")
    title = str(item.get("title") or "").strip()
    extensions = _extensions(kind, item.get("extensions"))
    if kind == "social_post":
        post_id = str(item.get("post_id") or "").strip()
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        handle = str(author.get("handle") or "").strip().lstrip("@")
        content: BaseContent = SocialPostContent(
            id=item_id,
            title=title or (text.strip().splitlines()[0][:80] if text.strip() else f"post {post_id}"),
            source_url=url or (f"https://x.com/{handle}/status/{post_id}" if handle else ""),
            publish_date=publish_date,
            content=text,
            has_content=bool(text),
            platform=str(item.get("platform") or "x"),
            author_id=str(author.get("user_id") or ""),
            author_handle=handle,
            author_name=str(author.get("name") or ""),
            author_avatar_url=str(author.get("avatar_url") or ""),
            author_avatar_url_large=str(author.get("avatar_url") or ""),
            post_id=post_id,
            lang=str(extensions.get("lang") or ""),
            tags=list(extensions.get("tags") or []),
            media_urls=[str(value) for value in (extensions.get("media_urls") or [])][:20],
        )
    elif kind == "podcast_episode":
        audio_url = str(item.get("audio_url") or "").strip()
        if not audio_url:
            raise IngestRejected("播客单集需要音频地址")
        duration = item.get("duration_seconds")
        content = PodcastEpisodeContent(
            id=item_id,
            title=title or "未命名单集",
            source_url=url or audio_url,
            publish_date=publish_date,
            content=text,
            has_content=bool(text),
            guid=str(item.get("guid") or ""),
            summary=str(item.get("summary") or text[:2000]),
            audio_url=audio_url,
            audio_mime=str(item.get("audio_mime") or ""),
            duration_seconds=int(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
            image_url=str(extensions.get("image_url") or ""),
            author=str(extensions.get("author") or ""),
            tags=list(extensions.get("tags") or []),
        )
    else:
        if not url:
            raise IngestRejected("文章需要链接")
        content = WebPageArticleContent(
            id=item_id,
            title=title or url,
            source_url=url,
            publish_date=publish_date,
            content=text,
            has_content=bool(text.strip()),
            site_name=str(extensions.get("site_name") or ""),
            summary=str(item.get("summary") or extensions.get("summary") or ""),
            tags=list(extensions.get("tags") or []),
        )
    content.source_id = source_id
    content._ingest_channel = CHANNEL_PUSH
    return content


def existing_article_by_url(session: Session, url: str) -> Optional[ArticleRecord]:
    """跨源 URL 去重:原样、规范形与带尾斜杠三种写法任一命中即算同一篇。"""
    raw = str(url or "").strip()
    if not raw:
        return None
    canonical = canonical_url(raw)
    candidates = {raw, canonical, canonical + "/"} - {""}
    return session.exec(
        select(ArticleRecord).where(ArticleRecord.source_url.in_(sorted(candidates))).limit(1)
    ).first()


def record_pick(session: Session, article_id: str, actor: str, note: str = "") -> bool:
    existing = session.exec(
        select(ArticlePickRecord).where(
            ArticlePickRecord.article_id == article_id,
            ArticlePickRecord.actor == actor,
        )
    ).first()
    if existing is not None:
        return False
    session.add(ArticlePickRecord(
        article_id=article_id,
        actor=actor,
        channel=CHANNEL_PUSH,
        note=str(note or "")[:500],
        picked_at=_now(),
    ))
    return True


def ensure_podcast_source(session: Session, resolved: ResolvedSource) -> None:
    """播客单集入库前必须有播客源行(存储层围栏);临时播客源只到元数据与简介初评。"""
    from services import source_rows

    row = session.get(SourceConfigRecord, resolved.source_id)
    if row is not None:
        return
    hint = dict(resolved.hint)
    feed_url = hint.pop("podcast_feed_url", "")
    row = source_rows.ensure_source_row(
        session, resolved.source_id, content_type="podcast_episode", **hint
    )
    if row is not None and feed_url:
        row.url = feed_url
        session.add(row)


def summarize(results: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts


def audit_body(results: list[dict[str, Any]], *, actor: str) -> dict[str, Any]:
    created_sources = sorted({r["source_id"] for r in results if r.get("source_created")})
    return {
        "actor": actor,
        "counts": summarize(results),
        "created_sources": created_sources[:20],
        "rejected": [r.get("reason") for r in results if r["status"] == "rejected"][:10],
    }


def audit_summary_text(body: dict[str, Any]) -> str:
    counts = body.get("counts") or {}
    parts = [f"{key} {value}" for key, value in sorted(counts.items())]
    created = body.get("created_sources") or []
    suffix = f";新建临时源 {len(created)} 个" if created else ""
    return f"Agent 收录({body.get('actor')}):" + ("、".join(parts) or "空批次") + suffix


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
