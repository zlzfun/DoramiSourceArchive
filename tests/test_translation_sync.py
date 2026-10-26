"""Only current final translation caches survive an authority title update."""
from datetime import datetime, timezone
import hashlib
import json

import pytest
from sqlmodel import Session

from models.db import ArticleRecord, SourceConfigRecord
from services import archive_sync_v2
from services.reader_ai import TRANSLATION_VERSION, SOCIAL_TRANSLATION_VERSION, social_translation_sources
from services.translation_sync import preserve_local_translations
from storage.impl.db_storage import DatabaseStorage


def _fp(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _article(**overrides):
    now = datetime.now(timezone.utc).isoformat()
    fields = dict(id="article-1", title="Original title", content_type="rss_article", source_id="rss_platform", source_url="https://example.test/a", publish_date=now, fetched_date=now, archive_updated_at=now, has_content=True, content="Full body", extensions_json="{}", analysis_authority_id="authority-a")
    fields.update(overrides)
    return ArticleRecord(**fields)


def _body(text="本地全文", body="Full body", **extra):
    return {"translation_zh": text, "translation_zh_fp": _fp(body.strip()), "translation_zh_version": TRANSLATION_VERSION, **extra}


def _social(ext, content="RT @user: truncated", translated="本地主推", quoted="本地引用"):
    sources = social_translation_sources(content, ext)
    return {"text": translated, "quoted": quoted, "fp": _fp(json.dumps(sources, ensure_ascii=False, sort_keys=True)), "v": SOCIAL_TRANSLATION_VERSION}


def _merge(local_ext, remote_ext, **incoming_fields):
    local = _article(extensions_json=json.dumps(local_ext))
    remote = _article(extensions_json=json.dumps(remote_ext), **incoming_fields)
    return json.loads(preserve_local_translations(local, remote, "authority-a"))


def test_title_only_update_preserves_only_known_body_fields_and_remote_valid_wins():
    local = _body(local_unknown="must drop", translation_zh_title="旧译名")
    remote = {"translation_zh_title": "新译名", "translation_zh_title_fp": _fp("Changed title"), "source_metadata": "remote"}
    merged = _merge(local, remote, title="Changed title")
    assert merged == {**remote, **_body()}
    assert _merge(local, {**remote, **_body("远端全文")}, title="Changed title")["translation_zh"] == "远端全文"
    assert _merge(local, {**remote, **_body("旧远端", body="old")})["translation_zh"] == "本地全文"


@pytest.mark.parametrize("invalid", [
    {"translation_zh_fp": None}, {"translation_zh_fp": "wrong"},
    {"translation_zh_version": None}, {"translation_zh_version": TRANSLATION_VERSION - 1},
    {"translation_zh": ""}, {"translation_zh": {"text": "malformed"}},
])
def test_legacy_or_invalid_local_body_is_not_revived(invalid):
    assert _merge({**_body(), **invalid}, {"source_metadata": "remote"}) == {"source_metadata": "remote"}


@pytest.mark.parametrize("changed", [{"content": "Changed full body"}, {"content_type": "paper"}, {"source_id": "another-source"}, {"id": "another-id"}])
def test_translation_input_or_identity_changes_drop_local_body(changed):
    assert "translation_zh" not in _merge(_body(), {}, **changed)


def test_paper_summary_is_not_a_substitute_for_fulltext_fingerprint():
    local = _article(content_type="paper", content="Old full paper", extensions_json=json.dumps(_body(body="Old full paper", summary="Same analysis abstract")))
    remote = _article(content_type="paper", content="Updated full paper", extensions_json=json.dumps({"summary": "Same analysis abstract"}))
    assert "translation_zh" not in json.loads(preserve_local_translations(local, remote, "authority-a"))


@pytest.mark.parametrize("authority", ["", "authority-b", None])
def test_authority_takeover_does_not_adopt_local_translation(authority):
    local = _article(analysis_authority_id=authority, extensions_json=json.dumps(_body()))
    assert preserve_local_translations(local, _article(), "authority-a") == "{}"


def test_social_repost_and_quote_snapshots_control_preservation_and_remote_wins():
    source = {"lang": "en", "reposted": {"text": "Full reposted English body", "lang": "en"}, "quoted": {"text": "Quoted English body", "lang": "en"}}
    local_ext = {**source, "translation_zh_social": {**_social(source), "unexpected": "drop"}, "unknown": "drop"}
    local = _article(content_type="social_post", content="RT @user: truncated", extensions_json=json.dumps(local_ext))
    remote = _article(content_type="social_post", content=local.content, title="Changed title", extensions_json=json.dumps(source))
    merged = json.loads(preserve_local_translations(local, remote, "authority-a"))
    assert merged == {**source, "translation_zh_social": _social(source)}
    for key in ("reposted", "quoted"):
        changed = {**source, key: {**source[key], "text": "Changed English source"}}
        remote.extensions_json = json.dumps(changed)
        assert "translation_zh_social" not in json.loads(preserve_local_translations(local, remote, "authority-a"))
    remote_entry = _social(source, translated="远端主推", quoted="远端引用")
    remote.extensions_json = json.dumps({**source, "translation_zh_social": remote_entry})
    assert json.loads(preserve_local_translations(local, remote, "authority-a"))["translation_zh_social"] == remote_entry
    # Snapshot language changes can remove a translatable side with identical text.
    remote.extensions_json = json.dumps({**source, "quoted": {**source["quoted"], "lang": "zh"}})
    assert "translation_zh_social" not in json.loads(preserve_local_translations(local, remote, "authority-a"))


@pytest.mark.parametrize("invalid", [{"fp": None}, {"v": None}, {"v": 0}, {"v": True}, {"text": []}, {"text": "", "quoted": None}])
def test_social_legacy_and_invalid_cache_is_not_preserved(invalid):
    source = {"lang": "en"}
    local = _article(extensions_json=json.dumps({**source, "translation_zh_social": {**_social(source, content="Full body", quoted=None), **invalid}}))
    remote = _article(extensions_json=json.dumps(source))
    assert json.loads(preserve_local_translations(local, remote, "authority-a")) == source


def test_archive_stream_title_update_keeps_replica_body_without_merging_unknown_fields(tmp_path):
    producer = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'producer.db'}")
    consumer = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'consumer.db'}")
    with Session(producer.engine) as session:
        session.add(SourceConfigRecord(source_id="rss_platform", name="Platform", source_type="rss", url="https://example.test/feed", fetcher_id="generic_rss", created_at=datetime.now(timezone.utc).isoformat(), updated_at=datetime.now(timezone.utc).isoformat()))
        session.add(_article(analysis_authority_id=""))
        session.commit()
    for stream in ("sources", "articles"):
        archive_sync_v2.import_page(consumer.engine, archive_sync_v2.export_page(producer.engine, stream), expected_stream=stream)
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.extensions_json = json.dumps(_body(local_unknown="drop"))
        session.add(article)
        session.commit()
    with Session(producer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.title = "New original title"
        article.extensions_json = json.dumps({"translation_zh_title": "新的中文题目", "translation_zh_title_fp": _fp(article.title)})
        session.add(article)
        session.commit()
    result = archive_sync_v2.import_page(consumer.engine, archive_sync_v2.export_page(producer.engine, "articles"), expected_stream="articles")
    assert result["updated"] == 1
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        ext = json.loads(article.extensions_json)
        assert article.title == "New original title"
        assert ext["translation_zh_title"] == "新的中文题目"
        assert ext["translation_zh"] == "本地全文"
        assert "local_unknown" not in ext
