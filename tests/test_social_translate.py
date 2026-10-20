"""推文翻译(issue #84):判定规则、原文选取、一次调用、缓存与失效、门控与失败不落缓存。"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

CASES = json.loads((Path(__file__).parent / "fixtures" / "social_translate_cases.json").read_text("utf-8"))


# ── 建库 / 登录 / 门控(与 test_reader_ai 同形)────────────────

def _base_setup(monkeypatch, tmp_path, name):
    import datetime

    import api.app as app_module
    from models.db import UserRecord
    from services import accounts as accounts_service
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        for username, role in (("admin", "admin"), ("user", "user")):
            existing = session.get(UserRecord, username)
            if existing is not None:
                session.delete(existing)
                session.commit()
            session.add(UserRecord(
                username=username, password_hash=accounts_service.hash_password(username),
                role=role, is_active=True, created_at=now, updated_at=now,
            ))
        session.commit()
    return app_module, sink


def _login(client, username="user"):
    response = client.post("/api/auth/login", json={"username": username, "password": username})
    assert response.status_code == 200


def _configure_llm(engine):
    from services import daily_brief as db

    with Session(engine) as session:
        db.set_setting(session, db.KEY_LLM_BASE_URL, "https://llm.test/v1")
        db.set_setting(session, db.KEY_LLM_API_KEY, "sk-test")
        db.set_setting(session, db.KEY_LLM_MODEL, "test-model")


def _set_ai_beta(engine, enabled, username="user"):
    from services import accounts as accounts_service

    with Session(engine) as session:
        accounts_service.set_ai_beta_enabled(session, username, enabled)


def _seed_article(engine, article_id, source_id, title, content):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=title, content_type="rss_article", source_id=source_id,
            source_url=f"https://example.test/{article_id}", publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-21T00:00:00", has_content=True, content=content, extensions_json="{}",
        ))
        session.commit()

QUOTE_POST_EXT = {
    "platform": "x",
    "lang": "en",
    "author_name": "Sam Altman",
    "author_handle": "sama",
    "quoted": {
        "author_name": "samir",
        "author_handle": "_samirism",
        "text": "throughout june we rolled out major updates to chatgpt memory https://t.co/KkzOE9rGIW",
        "url": "https://x.com/_samirism/status/2079199143868960823",
        "media_urls": [],
        "lang": "en",
    },
}


def _seed_social(engine, article_id, content, ext, source_id="x_sama"):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id,
            title=content.splitlines()[0][:80] if content else article_id,
            content_type="social_post",
            source_id=source_id,
            source_url=f"https://x.com/i/web/status/{article_id}",
            publish_date="2026-07-20T17:34:35Z",
            fetched_date="2026-07-21T00:00:00",
            has_content=bool(content),
            content=content,
            content_format="txt",
            extensions_json=json.dumps(ext, ensure_ascii=False),
        ))
        session.commit()


def _ext_of(engine, article_id):
    from models.db import ArticleRecord

    with Session(engine) as session:
        return json.loads(session.get(ArticleRecord, article_id).extensions_json)


def _patch_social_llm(monkeypatch, *, transform=lambda key, text: f"译：{text}", on_call=None):
    """JSON 进、JSON 出的桩:按键把原文变成「译：原文」(占位符原样保留),记录每次调用的入参。"""
    import services.reader_ai as rai

    calls = []

    async def fake_chat_completion(*, messages, config, **kwargs):
        payload = json.loads(messages[-1].content)
        calls.append({"payload": payload, "kwargs": kwargs, "system": messages[0].content})
        if on_call is not None:
            on_call(len(calls))
        return json.dumps({key: transform(key, text) for key, text in payload.items()}, ensure_ascii=False)

    monkeypatch.setattr(rai, "chat_completion", fake_chat_completion)
    return calls


# ── 判定规则(与前端共用同一份用例)───────────────────────────

@pytest.mark.parametrize("text,lang,expected", CASES["needs_translation"])
def test_social_text_needs_translation_matches_shared_cases(text, lang, expected):
    from services.reader_ai import social_text_needs_translation

    assert social_text_needs_translation(text, lang) is expected


def test_sources_use_full_repost_text_and_skip_untranslatable_pieces():
    from services.reader_ai import social_translation_sources

    # 转推:取 reposted.text,不取顶层 RT 截断;语言缺失时退回顶层 lang
    repost = {"lang": "en", "reposted": {"text": "ChatGPT Voice is now in the desktop app.\n\nControl your computer."}}
    assert social_translation_sources("RT @OpenAI: ChatGPT Voice is now in…", repost) == {
        "text": "ChatGPT Voice is now in the desktop app.\n\nControl your computer.",
    }
    # 只有一个链接的转推(zxx):没有可译内容
    assert social_translation_sources("RT @trq212: https://t.co/R6exTuF7P8", {
        "lang": "zxx", "reposted": {"text": "https://t.co/R6exTuF7P8"},
    }) == {}
    # 主推只有表情(art)、引用推英文且没存 lang(存量)→ 只译引用推
    assert social_translation_sources("🚀🚀 https://t.co/NlNLasmvAf", {
        "lang": "art", "quoted": {"text": "Day 0 on Qoder: Qwen3.8-Max-Preview is live."},
    }) == {"quoted": "Day 0 on Qoder: Qwen3.8-Max-Preview is live."}
    # 中文主推 + 英文引用推
    assert social_translation_sources("这个更新不错", {
        "lang": "zh", "quoted": {"text": "big memory update", "lang": "en"},
    }) == {"quoted": "big memory update"}


def test_normalizer_keeps_lang_on_quoted_and_reposted_posts():
    from services.x_social_normalizer import normalize_x_post_extensions

    post = {
        "id": "1", "author_id": "u1", "text": "RT @b: hello", "lang": "en",
        "referenced_tweets": [{"type": "retweeted", "id": "2"}, {"type": "quoted", "id": "3"}],
    }
    payload = {"includes": {
        "users": [{"id": "u1", "username": "a", "name": "A"}, {"id": "u2", "username": "b", "name": "B"}],
        "tweets": [
            {"id": "2", "author_id": "u2", "text": "hello", "lang": "en"},
            {"id": "3", "author_id": "u2", "text": "こんにちは", "lang": "ja"},
        ],
    }}
    ext = normalize_x_post_extensions(post, payload)
    assert ext["reposted"]["lang"] == "en"
    assert ext["quoted"]["lang"] == "ja"


# ── 端点:一次调用、缓存、失效 ─────────────────────────────

def test_translate_social_post_translates_main_and_quote_in_one_call_and_caches(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "p1", "it is good now! https://t.co/eiG0VDwrTe", QUOTE_POST_EXT)
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/social/p1/translate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cached"] is False
        # 链接经占位符保护后原样还原,不被模型改写
        assert body["text"] == "译：it is good now! https://t.co/eiG0VDwrTe"
        assert body["quoted"] == "译：throughout june we rolled out major updates to chatgpt memory https://t.co/KkzOE9rGIW"
        assert len(calls) == 1
        assert sorted(calls[0]["payload"]) == ["quoted.0", "text.0"]
        assert "https://t.co" not in json.dumps(calls[0]["payload"])  # 送给模型的是占位符
        assert calls[0]["kwargs"]["response_json"] is True
        assert calls[0]["kwargs"]["require_complete"] is True

        again = client.post("/api/reader/ai/social/p1/translate").json()
        assert again["cached"] is True and again["text"] == body["text"] and again["quoted"] == body["quoted"]
        assert len(calls) == 1

    ext = _ext_of(sink.engine, "p1")
    assert ext["translation_zh_social"]["text"] == body["text"]
    assert ext["quoted"]["text"] == QUOTE_POST_EXT["quoted"]["text"]  # 原文一字不动
    assert "translation_zh" not in ext  # 与文章译文缓存分开


def test_translate_social_post_retranslates_when_source_text_changes(monkeypatch, tmp_path):
    from models.db import ArticleRecord

    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_fp.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "p1", "it is good now!", QUOTE_POST_EXT)
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/ai/social/p1/translate").status_code == 200
        with Session(sink.engine) as session:
            record = session.get(ArticleRecord, "p1")
            ext = json.loads(record.extensions_json)
            ext["quoted"]["text"] = "the memory update is live for everyone"
            record.extensions_json = json.dumps(ext)
            session.add(record)
            session.commit()
        body = client.post("/api/reader/ai/social/p1/translate").json()
        assert body["cached"] is False
        assert body["quoted"] == "译：the memory update is live for everyone"
        assert len(calls) == 2


def test_translate_social_post_only_sends_translatable_piece(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_quote_only.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "p1", "🚀🚀 https://t.co/NlNLasmvAf", {
        "lang": "art", "quoted": {"text": "Day 0 on Qoder: Qwen3.8-Max-Preview is live."},
    }, source_id="x_alibaba_qwen")
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        body = client.post("/api/reader/ai/social/p1/translate").json()
    assert body["text"] is None
    assert body["quoted"] == "译：Day 0 on Qoder: Qwen3.8-Max-Preview is live."
    assert list(calls[0]["payload"]) == ["quoted.0"]


def test_translate_social_post_rejects_non_social_and_untranslatable(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_reject.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_article(sink.engine, "a1", "rss_x", "Title", "Hello world")
    _seed_social(sink.engine, "p1", "RT @trq212: https://t.co/R6exTuF7P8", {
        "lang": "zxx", "reposted": {"text": "https://t.co/R6exTuF7P8"},
    }, source_id="x_karpathy")
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/ai/social/a1/translate").status_code == 400
        assert client.post("/api/reader/ai/social/p1/translate").status_code == 400
        assert client.post("/api/reader/ai/social/missing/translate").status_code == 404
    assert calls == []


def test_translate_social_post_403_when_ai_beta_disabled(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_off.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, False)
    _seed_social(sink.engine, "p1", "it is good now!", QUOTE_POST_EXT)
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/ai/social/p1/translate").status_code == 403
    assert calls == []


def test_translate_social_post_hidden_source_is_404_for_readers(monkeypatch, tmp_path):
    from services import source_visibility

    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_hidden.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "p1", "it is good now!", QUOTE_POST_EXT, source_id="x_hidden")
    with Session(sink.engine) as session:
        source_visibility.set_source_hidden(session, "x_hidden", True)
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/social/p1/translate")
        assert response.status_code == 404
        assert response.json()["detail"] == "文章不存在"
    assert calls == []


# ── 失败不落缓存 ───────────────────────────────────────────

def test_incomplete_or_malformed_output_is_not_cached(monkeypatch, tmp_path):
    import services.reader_ai as rai
    from llm.client import LLMIncompleteOutput

    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_fail.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "p1", "it is good now! https://t.co/eiG0VDwrTe", QUOTE_POST_EXT)

    async def truncated(*, messages, config, **kwargs):
        raise LLMIncompleteOutput("模型未确认完整输出，译文未保存；请缩短输入或调整模型输出上限后重试")

    async def missing_key(*, messages, config, **kwargs):
        return json.dumps({"text.0": "译文"})

    async def dropped_link(*, messages, config, **kwargs):
        return json.dumps({"text.0": "现在好了！", "quoted.0": "六月更新"})

    with TestClient(app_module.app) as client:
        _login(client)
        for fake in (truncated, missing_key, dropped_link):
            monkeypatch.setattr(rai, "chat_completion", fake)
            response = client.post("/api/reader/ai/social/p1/translate")
            assert response.status_code == 502, fake.__name__
    assert "translation_zh_social" not in _ext_of(sink.engine, "p1")


def test_source_change_during_translation_is_409_and_not_cached(monkeypatch, tmp_path):
    from models.db import ArticleRecord

    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_race.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "p1", "it is good now!", QUOTE_POST_EXT)

    def edit_quote(_count):
        with Session(sink.engine) as session:
            record = session.get(ArticleRecord, "p1")
            ext = json.loads(record.extensions_json)
            ext["quoted"]["text"] = "edited while translating"
            record.extensions_json = json.dumps(ext)
            session.add(record)
            session.commit()

    _patch_social_llm(monkeypatch, on_call=edit_quote)
    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/ai/social/p1/translate").status_code == 409
    assert "translation_zh_social" not in _ext_of(sink.engine, "p1")


def test_cost_gate_runs_only_on_cache_miss():
    import services.reader_ai as rai
    from config import LLMConfig
    from types import SimpleNamespace

    record = SimpleNamespace(
        id="p1", title="it is good now!", content="it is good now!", content_type="social_post",
        source_id="x_sama", extensions_json=json.dumps(QUOTE_POST_EXT),
    )

    class FakeSink:
        async def get(self, _id):
            return record

        async def update(self, _id, values):
            record.extensions_json = values["extensions_json"]

    gates = []

    async def fake_chat_completion(*, messages, config, **kwargs):
        payload = json.loads(messages[-1].content)
        return json.dumps({key: f"译：{text}" for key, text in payload.items()}, ensure_ascii=False)

    cfg = LLMConfig(base_url="https://llm.test/v1", api_key="sk", model="m")
    original = rai.chat_completion
    rai.chat_completion = fake_chat_completion
    try:
        first = asyncio.run(rai.translate_social_post(FakeSink(), "p1", cfg, pre_llm_check=lambda: gates.append(1)))
        second = asyncio.run(rai.translate_social_post(FakeSink(), "p1", cfg, pre_llm_check=lambda: gates.append(1)))
    finally:
        rai.chat_completion = original
    assert first["cached"] is False and second["cached"] is True
    assert gates == [1]
