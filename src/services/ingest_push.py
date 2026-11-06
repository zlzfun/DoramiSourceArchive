"""收录推送通道(issue #262 波 1)。

调用方(Agent、自动化流程或人)把发现的文章 / 推文 / 播客单集推进平台。它们常常来自平台从未定义过的出处,
所以这条通道的职责是:**认出已有源,认不出就建临时源**,并且由服务端决定一切会影响下游判定的
字段(条目 id、``content_type``、系统扩展键、来路)。

- 鉴权:带作用域的收录令牌(``ding_``,只存 HMAC hash,绑定一个 actor 名,日额度为软上限);
- 身份解析:显式 ``source_id`` → 身份键(X 的 ``user_id`` / ``handle``)→ feed 地址 → 主机 + 路径前缀;
  都不中则建 ``provisional`` 临时源(不进发现目录、不计入多源印证、默认不进公共日报);
- 条目 id 与抓取器同一公式:X ``{sid}_{post_id}``;网页 / RSS ``{sid}_{sha1(guid 或链接)[:16]}``;
  播客 ``{前缀}_{sha1(guid 或单集链接)[:16]}``(前缀沿用 ``entry_id_namespace``)——先推后抓(或反之)天然幂等合流;
- 跨源 URL 去重:同一链接已在别的源里 → 不新建,返回 ``duplicate_url``,只记一条收录记录;
- 逐条结果:``created | duplicate | duplicate_url | refreshed | rejected``。

接收端(内网 replica)直接拒绝:只推外网 authority。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional
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
from services import ingest_policy, item_url_identity, source_catalog, source_identity
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
# 推送类型 → 目标源形态(三容器互斥):社交源不收文章,文章源不收社交帖,播客单集只进播客源。
SHAPE_BY_KIND = {
    "article": "article",
    "social_post": "social",
    "podcast_episode": "podcast",
}
_SHAPE_LABEL = {"article": "文章源", "social": "社交源", "podcast": "播客源"}
_KIND_LABEL = {"article": "文章", "social_post": "社交帖", "podcast_episode": "播客单集"}

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


def canonical_feed(value: str) -> str:
    """feed 地址的规范形:保留 query(hnrss、`feed?source=alice` 这类以参数区分内容的 feed),
    与读者自定源同一口径(``user_sources.canonical_feed_url``);非 http(s) 时退回原串。"""
    from services.user_sources import canonical_feed_url

    raw = str(value or "").strip()
    try:
        return canonical_feed_url(raw)
    except ValueError:
        return raw


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
            index.feeds.setdefault(canonical_feed(feed), source_id)
        for url in (view.base_url if view else "", getattr(cls, "listing_url", "") if cls else ""):
            if url:
                add_prefix(str(url), source_id)
    for row in session.exec(select(SourceConfigRecord)).all():
        if (row.owner_username or "").strip() or row.source_id.startswith(source_catalog.USER_SOURCE_PREFIX):
            continue
        if row.retired_at:
            continue
        if (row.source_type or "").strip().lower() in {"rss", "atom", "podcast", "podcast_rss"} and row.url:
            index.feeds.setdefault(canonical_feed(row.url), row.source_id)
        for url in (row.homepage_url, row.base_url, row.url if (row.source_type or "") in {"web", "webpage"} else ""):
            if url:
                add_prefix(url, row.source_id)
    # 长前缀优先:anthropic.com/news 先于 anthropic.com。
    index.prefixes.sort(key=lambda item: (-len(item[1]), item[0]))
    return index


def re_slash(path: str) -> str:
    path = re.sub(r"/{2,}", "/", path or "/")
    return path.rstrip("/") or "/"


def _match_prefix(
    index: _WebIndex, url: str, accept: Optional[Callable[[str], bool]] = None
) -> Optional[str]:
    """按「主机 + 路径前缀」认已有源(长前缀优先);``accept`` 过滤掉不收该类型的源后继续往下找。"""
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
            if accept is not None and not accept(source_id):
                continue
            return source_id
    return None


def _match_host(ctx: BatchContext, url: str, kind: str) -> Optional[str]:
    """前缀都没命中时的主机兜底:该主机上恰好一个形态相同、链接即条目身份的已有源就认它。

    策展源登记的常是分类页或 feed 地址(``openai.com/news/rss.xml``、``blogs.nvidia.com/blog/category/…``),
    文章链接不在前缀下;不兜底就会为同一个发布方另建临时源,抓取器随后把同一篇收进策展源,落成跨源两行。
    多租户主机、多个候选(有歧义)、共用页面链接的源(单页文档 / ``docs_*`` / ``github_*``)不兜底。
    """
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").casefold()
    except ValueError:
        return None
    host = host[4:] if host.startswith("www.") else host
    if not host or host in MULTI_TENANT_HOSTS or f"www.{host}" in MULTI_TENANT_HOSTS:
        return None
    content_type = CONTENT_TYPE_BY_KIND.get(kind, "")
    candidates = {
        source_id
        for prefix_host, _path, source_id in ctx.web_index.prefixes
        if prefix_host == host and source_id not in ctx.hidden
    }
    usable = []
    for source_id in sorted(candidates):
        view = source_catalog.get(ctx.session, source_id)
        if view.retired or view.is_private or view.synced:
            continue
        if _shape_mismatch(ctx, source_id, kind) is not None:
            continue
        if not item_url_identity.url_is_item_identity(ctx.session, source_id, content_type):
            continue
        usable.append(source_id)
    return usable[0] if len(usable) == 1 else None


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


def _known_user_ids(ctx: BatchContext, platform: str, source_id: str) -> set[str]:
    """某个源已登记的稳定 user_id 身份键(注册表预设、配置行、身份键表)。"""
    prefix = f"{platform}:user_id:"
    known = {key for key, sid in ctx.x_index.items() if sid == source_id and key.startswith(prefix)}
    known.update(
        row.key for row in ctx.session.exec(
            select(SourceIdentityKeyRecord).where(
                SourceIdentityKeyRecord.source_id == source_id,
                SourceIdentityKeyRecord.key.startswith(prefix),
            )
        ).all()
    )
    return known


def _shape_mismatch(ctx: BatchContext, source_id: str, kind: str) -> Optional[str]:
    """推送类型与目标源形态不符时给拒收原因。"""
    want = SHAPE_BY_KIND.get(kind)
    shape = source_catalog.get(ctx.session, source_id).shape
    if not want or not shape or shape == want:
        return None
    return f"该来源是{_SHAPE_LABEL.get(shape, shape)},不收{_KIND_LABEL.get(kind, kind)}"


def _accepts_kind(ctx: BatchContext, kind: str) -> Callable[[str], bool]:
    return lambda source_id: _shape_mismatch(ctx, source_id, kind) is None


def _check_target(ctx: BatchContext, source_id: str, kind: str) -> None:
    """已有源能否接受这一类推送。"""
    reserved = ingest_policy.reserved_source_id_reason(source_id)
    if reserved:
        raise IngestRejected(reserved)
    mismatch = _shape_mismatch(ctx, source_id, kind)
    if mismatch:
        raise IngestRejected(mismatch)
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
    planned_ids = {resolved.source_id for resolved in ctx.planned.values()}
    while (
        ctx.session.get(SourceConfigRecord, candidate) is not None
        or source_catalog.registry_view(candidate) is not None
        or candidate in planned_ids
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
        _check_target(ctx, explicit, kind)
        return ResolvedSource(source_id=explicit)

    name = str(source.get("name") or "").strip()
    avatar = str(source.get("avatar_url") or "").strip()

    if kind == "social_post":
        platform = str(source.get("platform") or "x").strip().casefold() or "x"
        handle = str(source.get("handle") or "").strip()
        user_id = str(source.get("user_id") or "").strip()
        keys = source_identity.social_identity_keys(platform, handle=handle, user_id=user_id)
        user_key = source_identity.identity_key(platform, "user_id", user_id) if user_id else ""
        handle_conflict = False
        for key in keys:
            if key in ctx.planned:
                planned = ctx.planned[key]
                planned_users = {
                    k for k in planned.hint.get("identity_keys", []) if ":user_id:" in k
                }
                if user_key and key != user_key and planned_users - {user_key}:
                    handle_conflict = True  # 同批里同 handle、不同稳定 user_id(检视复检 #5)
                    continue
                return planned
            hit = ctx.x_index.get(key) if platform == "x" else _existing_identity(ctx.session, key)
            if hit and user_key and key != user_key and _known_user_ids(ctx, platform, hit) - {user_key}:
                # handle 命中的源登记过另一个稳定 user_id:账号改名后旧 handle 被他人接手,
                # 不能按 handle 把新账号的内容塞进原账号的源(实现检视 R1 #5)。
                handle_conflict = True
                continue
            if hit:
                _check_target(ctx, hit, kind)
                return ResolvedSource(source_id=hit)
        if not source_identity.normalize_handle(handle):
            raise IngestRejected("社交帖需要作者 handle")
        derived = source_identity.derive_social_source_id(platform, handle)
        derived_exists = (
            ctx.session.get(SourceConfigRecord, derived) is not None
            or source_catalog.registry_view(derived) is not None
            or derived in {resolved.source_id for resolved in ctx.planned.values()}
        )
        if derived_exists and handle_conflict:
            derived = _unique_new_id(ctx, derived)
        elif derived_exists:
            # 同名 id 已存在但身份键没对上(例如旧导入行):沿用,不再造一个平行源。
            _check_target(ctx, derived, kind)
            return ResolvedSource(source_id=derived)
        display = name or handle.lstrip("@")
        resolved = ResolvedSource(source_id=derived, created=True, hint={
            "origin": source_catalog.ORIGIN_INGEST,
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
        hit = ctx.web_index.feeds.get(canonical_feed(feed_url))
        # 形态不同的源(例如往文章 RSS 的 feed 推播客单集)不算命中,继续往下解析。
        if hit and _shape_mismatch(ctx, hit, kind) is None:
            _check_target(ctx, hit, kind)
            return ResolvedSource(source_id=hit)
    lookup_url = site or item_url
    if lookup_url and kind != "podcast_episode":
        # x.com/<账号>/… 的文章不落进该账号的社交源:前缀匹配跳过形态不同的源。
        hit = _match_prefix(ctx.web_index, lookup_url, accept=_accepts_kind(ctx, kind))
        if hit:
            _check_target(ctx, hit, kind)
            return ResolvedSource(source_id=hit)
        hit = _match_host(ctx, lookup_url, kind)
        if hit:
            _check_target(ctx, hit, kind)
            return ResolvedSource(source_id=hit)
    anchor = feed_url or lookup_url
    if not anchor:
        raise IngestRejected("缺少出处:给 source_id、平台账号或站点 / feed 链接")
    if kind == "podcast_episode":
        if not feed_url:
            raise IngestRejected("播客单集需要节目 feed 地址")
        key = source_identity.identity_key("podcast", "feed", canonical_feed(feed_url))
        base = f"podcast_{_UNSAFE_ID.sub('_', _site_key(feed_url).replace('.', '_'))}"[:60].rstrip("_")
        base = f"{base}_{hashlib.sha1(canonical_feed(feed_url).encode('utf-8')).hexdigest()[:6]}"
    else:
        site_key = _site_key(lookup_url or feed_url)
        key = source_identity.identity_key("web", "site", site_key)
        base = _derive_web_source_id(site_key)
    if key in ctx.planned:
        return ctx.planned[key]
    existing = _existing_identity(ctx.session, key)
    if existing:
        _check_target(ctx, existing, kind)
        return ResolvedSource(source_id=existing)
    source_id = _unique_new_id(ctx, base)
    homepage = site or (f"https://{_host(lookup_url or feed_url)}" if (lookup_url or feed_url) else "")
    resolved = ResolvedSource(source_id=source_id, created=True, hint={
        "origin": source_catalog.ORIGIN_INGEST,
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


def item_id_prefix(session: Session, source_id: str) -> str:
    """条目 id 前缀:与绑定抓取器同一身份策略。

    收养过的播客保留 ``entry_id_namespace``(旧自定源 id),抓取器继续用它生成条目 id
    (``podcast_rss_fetcher``);推送必须用同一前缀,否则同一集落两行(实现检视 R1 #6)。
    """
    row = session.get(SourceConfigRecord, source_id)
    if row is not None:
        try:
            params = json.loads(row.params_json or "{}")
        except (TypeError, json.JSONDecodeError):
            params = {}
        namespace = str((params or {}).get("entry_id_namespace") or "").strip() if isinstance(params, dict) else ""
        if namespace:
            return namespace
    return source_id


def item_id_for(kind: str, source_id: str, item: dict[str, Any], *, prefix: str = "") -> str:
    prefix = prefix or source_id
    if kind == "social_post":
        post_id = str(item.get("post_id") or "").strip()
        if not post_id:
            raise IngestRejected("社交帖需要 post_id")
        return f"{prefix}_{post_id}"
    if kind == "podcast_episode":
        # 与 podcast_rss_fetcher._entry_id 同序:feed 条目 id / guid,其次单集链接。两样都没有时
        # 抓取器改用「音频地址 + 发布时间」组合,推送复刻不稳,所以要求二者给一(检视复检 #6)。
        stable = str(item.get("guid") or item.get("url") or "").strip()
        if not stable:
            raise IngestRejected("播客单集需要 guid 或单集链接")
        return f"{prefix}_{_sha16(stable)}"
    stable = str(item.get("guid") or item.get("url") or "").strip()
    if not stable:
        raise IngestRejected("文章需要链接")
    return f"{prefix}_{_sha16(stable)}"


_LIST_EXTENSION_KEYS = frozenset({"tags", "media_urls"})
_INT_EXTENSION_KEYS = frozenset({"episode", "season"})


def _extensions(kind: str, raw: Any) -> dict[str, Any]:
    """按 kind 的白名单取扩展键,并规整类型;值不合法时拒收该条(实现检视 R1 #12)。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise IngestRejected("extensions 必须是对象")
    allowed = EXTENSION_KEYS[kind]
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed or value is None:
            continue
        if key in _LIST_EXTENSION_KEYS:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list) or not all(isinstance(v, (str, int, float)) for v in value):
                raise IngestRejected(f"extensions.{key} 必须是字符串列表")
            out[key] = [str(v).strip() for v in value if str(v).strip()][:50]
        elif key in _INT_EXTENSION_KEYS:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or value > 1_000_000
            ):
                raise IngestRejected(f"extensions.{key} 必须是非负整数")
            out[key] = int(value)
        else:
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise IngestRejected(f"extensions.{key} 必须是字符串")
            out[key] = str(value).strip()[:2000]
    return out


def build_content(kind: str, source_id: str, item: dict[str, Any], *, id_prefix: str = "") -> BaseContent:
    item_id = item_id_for(kind, source_id, item, prefix=id_prefix)
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
            episode=extensions.get("episode"),
            season=extensions.get("season"),
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
    # 白名单里模型没有字段的键(文章 author / language / image_url,播客 episode / season)
    # 经 extra_extensions 落进 extensions_json(实现检视 R1 #11)。
    model_fields = set(getattr(type(content), "__dataclass_fields__", {}))
    extra = {key: value for key, value in extensions.items() if key not in model_fields}
    if extra:
        content.extra_extensions = extra
    content.source_id = source_id
    content._ingest_channel = CHANNEL_PUSH
    return content


def existing_article_by_url(session: Session, url: str) -> Optional[ArticleRecord]:
    """跨源 URL 去重:原样、规范形与带尾斜杠三种写法任一命中即算同一篇。

    只在可公开交付的条目里查(实现检视 R1 #3):读者私有源(``user_rss_`` 前缀、带 owner)与
    带凭证的源不参与——否则推送方能凭一个链接探出私有条目的 id 与源,公开内容也会被吞掉。
    """
    from services.user_sources import source_is_credentialed

    candidates = item_url_identity.url_candidates(url)
    if not candidates:
        return None
    rows = session.exec(
        select(ArticleRecord)
        .where(ArticleRecord.source_url.in_(candidates))
        .where(~ArticleRecord.source_id.startswith(source_catalog.USER_SOURCE_PREFIX, autoescape=True))
        .limit(20)
    ).all()
    for row in rows:
        source = session.get(SourceConfigRecord, row.source_id)
        if source is not None and ((source.owner_username or "").strip() or source_is_credentialed(source)):
            continue
        return row
    return None


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
    return f"收录推送({body.get('actor')}):" + ("、".join(parts) or "空批次") + suffix


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
