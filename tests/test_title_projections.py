"""Strict cached title projection in rankings and reader answer citations."""
from datetime import datetime, timezone
import hashlib
import json

import pytest
from sqlmodel import Session

from models.db import ArticleAnalysisRecord, ArticleRecord, RankingContentItemRecord
from services import rankings, reader_ai
from services.title_cache import cached_title
from storage.impl.db_storage import DatabaseStorage

TITLE = "A useful research paper"
TRANSLATED = "一篇有用的研究论文"
RECEIPT = {"translation_zh_title": TRANSLATED, "translation_zh_title_fp": hashlib.sha256(TITLE.encode()).hexdigest()[:16]}


def _article(**extra):
    now = datetime.now(timezone.utc).isoformat()
    fields = dict(id="paper", title=TITLE, content_type="web_article", source_id="projection-public-source", source_url="https://example.test/paper", publish_date=now, fetched_date=now, content="Original evidence.", extensions_json=json.dumps(RECEIPT))
    fields.update(extra)
    return ArticleRecord(**fields)


def test_ranking_payloads_expose_valid_title_without_changing_original_or_score(tmp_path):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'title-ranking.db'}")
    article = _article()
    item = RankingContentItemRecord(score=9.2, score_basis="article_body", appearance_count=3)
    payload = rankings._content_payload(item, article)
    assert payload["title"] == TITLE and payload["title_zh"] == TRANSLATED
    assert payload["score"] == 9.2 and payload["appearance_count"] == 3
    assert "content" not in payload and "extensions" not in payload
    with Session(sink.engine) as session:
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(article_id=article.id, status="succeeded", quality_score=9.2, created_at=article.fetched_date, updated_at=article.fetched_date))
        session.commit()
        ranked = rankings._all_time_high_scores(session, shape="article")
        assert [(entry["title"], entry["title_zh"], entry["score"]) for entry in ranked] == [(TITLE, TRANSLATED, 9.2)]
        article.title = "A changed original title"
        session.add(article)
        session.commit()
        assert rankings._content_payload(item, article)["title_zh"] is None
        assert rankings._all_time_high_scores(session, shape="article")[0]["title_zh"] is None


def test_answer_sources_keep_numbering_context_and_original_titles():
    record = _article()
    mapping = {"id": "dict-paper", "title": TITLE, "content": "Second original evidence.", "extensions": dict(RECEIPT)}
    context, sources = reader_ai.assemble_articles_context([record, mapping])
    assert [source["id"] for source in sources] == ["paper", "dict-paper"]
    assert [source["title"] for source in sources] == [TITLE, TITLE]
    assert [source["title_zh"] for source in sources] == [TRANSLATED, TRANSLATED]
    assert f"[1] {TITLE}" in context and f"[2] {TITLE}" in context
    assert TRANSLATED not in context
    assert "translation_zh_title" not in json.dumps(sources)
    assert reader_ai.public_sources(sources)[0]["title_zh"] == TRANSLATED
    assert record.title == TITLE and mapping["title"] == TITLE
    assert mapping["extensions"] == RECEIPT


@pytest.mark.parametrize("fields, expected", [
    ({"extensions_json": json.dumps(RECEIPT)}, TRANSLATED),
    ({"extensions_json": dict(RECEIPT)}, TRANSLATED),
    ({"extensions": dict(RECEIPT)}, TRANSLATED),
    ({"title_zh": "未经校验的译名"}, None),
    ({"extensions": {"translation_zh_title": TRANSLATED}}, None),
    ({"extensions": {**RECEIPT, "translation_zh_title_fp": "stale"}}, None),
    ({"extensions": {**RECEIPT, "translation_zh_title": "   "}}, None),
    ({"extensions_json": "[]"}, None),
    ({"extensions_json": "malformed", "extensions": dict(RECEIPT)}, None),
])
def test_dict_citations_require_a_raw_valid_title_receipt(fields, expected):
    record = {"id": "dict", "title": TITLE, **fields}
    assert reader_ai.build_sources_payload([record])[0]["title_zh"] == expected


def test_dictionary_support_preserves_frozen_title_override_contract():
    record = {"title": "Current edited title", "extensions_json": json.dumps(RECEIPT)}
    assert cached_title(record) is None
    assert cached_title(record, title=TITLE) == TRANSLATED
