"""条目链接即身份:收录条目与抓取条目按链接合流(issue #262)。

抓取入库只按条目 id 去重,而推送 / 手工录入 / 导入的条目 id 公式(``{源}_{sha1(guid 或 url)}``)
与不少预设抓取器不同(RSS 取 ``entry.id``、单页文档按锚点、GitHub 按 tag……)。先推后抓时,同一篇
会在同一个源里落成两行。这里给出两条入库路径共用的链接口径:

- 候选写法:原样 / 规范形(``event_assignment.canonical_url``)/ 规范形带尾斜杠;
- 只对「链接即条目身份」的源成立:播客单集常共用节目页链接,单页文档(changelog / release notes)
  与 GitHub 类源的多个条目共用同一页面链接,这些一律不按链接合流;
- 抓取侧只认**收录来路**(``ingest_channel`` ∈ 推送 / 手工 / 导入)的旧行,普通抓取条目之间的
  行为不变;同源里已有同链接的抓取条目时说明该源多条共用链接,也不合流。
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from sqlmodel import Session, select

from models.db import ArticleRecord, SourceConfigRecord


INGEST_CHANNELS = frozenset({"push", "manual", "import"})
_NON_IDENTITY_CONTENT_TYPES = frozenset({"podcast_episode"})


def url_candidates(url: str, *, keep_fragment: bool = False) -> list[str]:
    """同一链接的三种入库写法。``keep_fragment``:带锚点的链接只认原样(锚点是条目身份的一部分)。"""
    from services.event_assignment import canonical_url

    raw = str(url or "").strip()
    if not raw:
        return []
    if keep_fragment:
        try:
            has_fragment = bool(urlsplit(raw).fragment)
        except ValueError:
            has_fragment = False
        if has_fragment:
            return [raw]
    canonical = canonical_url(raw)
    return sorted({raw, canonical, canonical + "/"} - {"", "/"})


def _shared_page_fetcher(fetcher_id: str) -> bool:
    if not fetcher_id:
        return False
    try:
        from fetchers.impl.curated_core_fetcher import SinglePageDocumentFetcher
        from fetchers.registry import fetcher_registry

        cls = fetcher_registry.get_class(fetcher_id)
    except Exception:  # noqa: BLE001 - 未注册的绑定按普通源处理
        return False
    return isinstance(cls, type) and issubclass(cls, SinglePageDocumentFetcher)


def url_is_item_identity(session: Session, source_id: str, content_type: str = "") -> bool:
    """该源的条目链接能否当条目身份(多个条目共用页面链接的源不能)。"""
    from services.event_assignment import (
        URL_DIRECT_EXCLUDED_CONTENT_TYPES,
        URL_DIRECT_EXCLUDED_SOURCE_PREFIXES,
    )

    sid = str(source_id or "").strip()
    if not sid:
        return False
    if (content_type or "") in _NON_IDENTITY_CONTENT_TYPES | URL_DIRECT_EXCLUDED_CONTENT_TYPES:
        return False
    if sid.startswith(URL_DIRECT_EXCLUDED_SOURCE_PREFIXES):
        return False
    if _shared_page_fetcher(sid):
        return False
    row = session.get(SourceConfigRecord, sid)
    if row is not None and _shared_page_fetcher(row.fetcher_id or ""):
        return False
    return True


def ingested_row_for(session: Session, source_id: str, url: str, content_type: str = "") -> Optional[ArticleRecord]:
    """同一个源里、同一链接的收录来路旧行(新条目入库前调用)。不合流的情形返回 None。"""
    candidates = url_candidates(url, keep_fragment=True)
    if not candidates or not url_is_item_identity(session, source_id, content_type):
        return None
    rows = session.exec(
        select(ArticleRecord)
        .where(ArticleRecord.source_id == source_id)
        .where(ArticleRecord.source_url.in_(candidates))
        .order_by(ArticleRecord.fetched_date, ArticleRecord.id)
        .limit(20)
    ).all()
    ingested = [row for row in rows if (row.ingest_channel or "") in INGEST_CHANNELS]
    if not ingested or len(ingested) != len(rows):
        # 同源已有同链接的抓取条目:这个源的多个条目共用链接,链接不是条目身份。
        return None
    return ingested[0]
