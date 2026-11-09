"""同源内容判重的正文指纹(issue #262 推送入口统一波,用户 2026-11-06 拍板)。

只服务一件事:**推送**一篇文章到**已有源**时,认出该源里换了链接的同一篇。范围刻意收窄:

- 只比同一个 ``source_id``;跨源一律不比——官方双发(Google 博客 / DeepMind 博客)、媒体转载各自保留,
  同一事件的归并由事件层与日报在消费端做;
- 抓取路径完全不动:指纹不在入库时算,推送前对目标源现场补齐(缺的、条目改过的),
  同源反复发布的抓取条目照样保留;
- 判定:规范化正文(NFKC + casefold + 去标签、去标点空白)≥ 300 字;正文 sha1 相同,或 64 位
  SimHash(字符 4-gram)海明距离 ≤ 3 且 5-gram Jaccard ≥ 0.8 复核;
- 排除:共用页面链接 / 模板化的源(``item_url_identity`` 的名单)、模型卡、GitHub trending。

指纹旁表 ``article_fingerprints`` 只在本机用,不进 Archive Sync。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import unicodedata
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from models.db import ArticleFingerprintRecord, ArticleRecord, SourceConfigRecord
from services import item_url_identity


MIN_CHARS = 300
MAX_DISTANCE = 3
MIN_JACCARD = 0.8
# SimHash 只取规范化正文前 2 万字,控制超长论文的耗时;sha1 与 Jaccard 复核用全文。
_SIMHASH_CHARS = 20_000
_EXCLUDED_CONTENT_TYPES = frozenset({"social_post", "podcast_episode", "daily_brief", "hf_model", "github_trending"})
_EXCLUDED_SOURCE_PREFIXES = ("hf_",)

_TAG = re.compile(r"<[^>]+>")
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    stripped = html.unescape(_TAG.sub(" ", text or ""))
    return _NON_WORD.sub("", unicodedata.normalize("NFKC", stripped).casefold())


def _grams(normalized: str, k: int) -> set[str]:
    if len(normalized) <= k:
        return {normalized} if normalized else set()
    return {normalized[i:i + k] for i in range(len(normalized) - k + 1)}


def simhash(normalized: str) -> int:
    """64 位 SimHash(字符 4-gram 去重后等权)。逐位计数用「转二进制串再按列数 1」,比逐位移位快数倍。"""
    grams = _grams(normalized[:_SIMHASH_CHARS], 4)
    if not grams:
        return 0
    rows = [
        format(int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big"), "064b")
        for gram in grams
    ]
    half = len(rows) / 2
    value = 0
    # format(…, "064b") 第 0 列是最高位(bit 63)。
    for position, column in enumerate(zip(*rows)):
        if column.count("1") > half:
            value |= 1 << (63 - position)
    return value


def content_hash(normalized: str) -> str:
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def jaccard(a: str, b: str, k: int = 5) -> float:
    left, right = _grams(a, k), _grams(b, k)
    if not left and not right:
        return 1.0
    return len(left & right) / max(1, len(left | right))


def _source_content_types(session: Session, source_id: str) -> set[str]:
    """目标源的真实条目类型:注册表类与行上绑定类的 ``content_type``(推送的文章一律是 web_article,
    看不出源本身是不是 GitHub release 一类;检视 R1 #2)。"""
    types: set[str] = set()
    try:
        from fetchers.registry import fetcher_registry
    except Exception:  # noqa: BLE001
        return types
    row = session.get(SourceConfigRecord, source_id)
    for fetcher_id in (source_id, (row.fetcher_id if row is not None else "") or ""):
        cls = fetcher_registry.get_class(fetcher_id) if fetcher_id else None
        value = str(getattr(cls, "content_type", "") or "") if cls is not None else ""
        if value:
            types.add(value)
    return types


def _excluded_types() -> frozenset[str]:
    from services.event_assignment import URL_DIRECT_EXCLUDED_CONTENT_TYPES

    return _EXCLUDED_CONTENT_TYPES | URL_DIRECT_EXCLUDED_CONTENT_TYPES


def eligible(session: Session, source_id: str, content_type: str) -> bool:
    """该源能否做同源内容判重(共用页面链接 / 模板化的源、模型卡、trending 不做)。"""
    sid = str(source_id or "").strip()
    if not sid or sid.startswith(_EXCLUDED_SOURCE_PREFIXES):
        return False
    excluded = _excluded_types()
    if (content_type or "") in excluded or _source_content_types(session, sid) & excluded:
        return False
    return item_url_identity.url_is_item_identity(session, sid, content_type)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _fingerprint_values(text: str) -> tuple[int, str, str]:
    normalized = normalize(text)
    if len(normalized) < MIN_CHARS:
        return len(normalized), "", ""
    return len(normalized), content_hash(normalized), format(simhash(normalized), "016x")


def sync_source(session: Session, source_id: str) -> int:
    """现场补齐该源缺的 / 过期的指纹(条目 ``archive_updated_at`` 变了即重算);返回重算条数。

    - 短正文也落一行(``length`` < 300、哈希为空),免得每次收录都重新规范化一遍;
    - 写入用 SQLite 原子 UPSERT:两个请求同时首次补齐同一个源不会撞主键(检视 R1 #1);旧版本偶尔
      写赢时,下次同步按版本号不一致自愈重算;
    - 已删除 / 换了源 / 不再有正文的条目留下的孤儿指纹顺手删掉(检视 R1 #4)。
    """
    live = {
        article_id: revision or ""
        for article_id, revision in session.exec(
            select(ArticleRecord.id, ArticleRecord.archive_updated_at)
            .where(ArticleRecord.source_id == source_id)
            .where(ArticleRecord.has_content == True)  # noqa: E712 - SQL 表达式
        ).all()
    }
    known = {
        article_id: revision
        for article_id, revision in session.exec(
            select(ArticleFingerprintRecord.article_id, ArticleFingerprintRecord.revision)
            .where(ArticleFingerprintRecord.source_id == source_id)
        ).all()
    }
    orphans = [article_id for article_id in known if article_id not in live]
    for start in range(0, len(orphans), 500):
        session.exec(delete(ArticleFingerprintRecord).where(
            ArticleFingerprintRecord.article_id.in_(orphans[start:start + 500]),
            ArticleFingerprintRecord.source_id == source_id,
        ))
    stale = [article_id for article_id, revision in live.items() if known.get(article_id) != revision]
    for start in range(0, len(stale), 200):
        rows = []
        for record in session.exec(
            select(ArticleRecord).where(ArticleRecord.id.in_(stale[start:start + 200]))
        ).all():
            length, digest, sim = _fingerprint_values(record.content or "")
            rows.append({
                "article_id": record.id,
                "source_id": record.source_id,
                "content_hash": digest,
                "simhash": sim,
                "length": length,
                "revision": record.archive_updated_at or "",
                "updated_at": _now(),
            })
        if rows:
            statement = sqlite_insert(ArticleFingerprintRecord).values(rows)
            session.exec(statement.on_conflict_do_update(
                index_elements=["article_id"],
                set_={
                    key: getattr(statement.excluded, key)
                    for key in ("source_id", "content_hash", "simhash", "length", "revision", "updated_at")
                },
            ))
        session.commit()
    if orphans and not stale:
        session.commit()
    return len(stale)


def _live_fingerprints(source_id: str):
    """该源存活条目的指纹(JOIN 当前 articles:同源、仍有正文、类型不在禁比名单)。"""
    return (
        select(ArticleFingerprintRecord.article_id, ArticleFingerprintRecord.simhash)
        .join(ArticleRecord, ArticleRecord.id == ArticleFingerprintRecord.article_id)
        .where(ArticleFingerprintRecord.source_id == source_id)
        .where(ArticleRecord.source_id == source_id)
        .where(ArticleRecord.has_content == True)  # noqa: E712 - SQL 表达式
        .where(ArticleRecord.content_type.not_in(sorted(_excluded_types())))
    )


def find_same_source_duplicate(
    session: Session, source_id: str, text: str, content_type: str = ""
) -> Optional[ArticleRecord]:
    """同一个源里正文相同或近似的已有条目;不判(短文本 / 排除源)或没有时返回 None。"""
    normalized = normalize(text)
    if len(normalized) < MIN_CHARS or not eligible(session, source_id, content_type):
        return None
    sync_source(session, source_id)
    exact = session.exec(
        _live_fingerprints(source_id)
        .where(ArticleFingerprintRecord.content_hash == content_hash(normalized))
        .limit(1)
    ).first()
    if exact:
        return session.get(ArticleRecord, exact[0])
    target = simhash(normalized)
    candidates = []
    for article_id, sim in session.exec(
        _live_fingerprints(source_id).where(ArticleFingerprintRecord.simhash != "")
    ).all():
        distance = bin(int(sim, 16) ^ target).count("1")
        if distance <= MAX_DISTANCE:
            candidates.append((distance, article_id))
    for _distance, article_id in sorted(candidates)[:10]:
        row = session.get(ArticleRecord, article_id)
        if row is not None and jaccard(normalized, normalize(row.content or "")) >= MIN_JACCARD:
            return row
    return None


def find_same_source_duplicate_id(
    engine, source_id: str, text: str, content_type: str = ""
) -> Optional[tuple[str, str]]:
    """线程里调用的版本:自带会话,只回 (条目 id, source_id)。"""
    with Session(engine) as session:
        row = find_same_source_duplicate(session, source_id, text, content_type)
        return (row.id, row.source_id) if row is not None else None
