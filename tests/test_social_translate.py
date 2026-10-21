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


def _units_reply(messages, transform=lambda text: f"译：{text}"):
    """单元协议(#206)的桩回包:逐单元把原文变成「译：原文」(占位符原样保留),原样回 id。"""
    units = json.loads(messages[-1].content)["units"]
    return units, json.dumps({"units": [{"id": u["id"], "text": transform(u["text"])} for u in units]}, ensure_ascii=False)


def _patch_social_llm(monkeypatch, *, transform=lambda text: f"译：{text}", on_call=None):
    """JSON 进、JSON 出的桩,记录每次调用送给模型的行单元原文(texts)与参数。"""
    import services.reader_ai as rai

    calls = []

    async def fake_chat_completion(*, messages, config, **kwargs):
        units, reply = _units_reply(messages, transform)
        calls.append({"texts": [u["text"] for u in units], "kwargs": kwargs, "system": messages[0].content})
        if on_call is not None:
            on_call(len(calls))
        return reply

    monkeypatch.setattr(rai, "chat_completion", fake_chat_completion)
    return calls


# ── 判定规则(与前端共用同一份用例)───────────────────────────

@pytest.mark.parametrize("text,lang,expected", CASES["needs_translation"])
def test_social_text_needs_translation_matches_shared_cases(text, lang, expected):
    from services.reader_ai import social_text_needs_translation

    assert social_text_needs_translation(text, lang) is expected


@pytest.mark.parametrize("case", CASES["generated"])
def test_social_text_needs_translation_matches_generated_cases(case):
    from services.reader_ai import social_text_needs_translation

    text = "".join(piece * count for piece, count in case["parts"])
    assert social_text_needs_translation(text, case["lang"]) is case["expected"]


@pytest.mark.parametrize("text,lang", [(t, l) for t, l, expected in CASES["needs_translation"] if expected])
def test_translatable_judgement_always_leaves_something_for_the_model(text, lang):
    """判定说要译 ⇒ 实际分段计划里必有交给模型的片段:两侧的链接分隔规则漂移时,这里先失败(检视复检)。"""
    from services.reader_ai import _TRANSLATE_SEGMENT_CHARS
    from services.translation_markdown import plan_translation

    plan = plan_translation(text, segment_chars=_TRANSLATE_SEGMENT_CHARS)
    assert any(chunk.needs_translation for chunk in plan.chunks)


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
        texts = calls[0]["texts"]
        assert len(texts) == 2 and texts[0].startswith("it is good now!") and texts[1].startswith("throughout june")
        assert "https://t.co" not in json.dumps(texts)  # 送给模型的是占位符
        assert calls[0]["kwargs"]["response_json"] is True
        assert calls[0]["kwargs"]["require_complete"] is True

        again = client.post("/api/reader/ai/social/p1/translate").json()
        assert again["cached"] is True and again["text"] == body["text"] and again["quoted"] == body["quoted"]
        assert len(calls) == 1

    ext = _ext_of(sink.engine, "p1")
    assert ext["translation_zh_social"]["text"] == body["text"]
    assert ext["quoted"]["text"] == QUOTE_POST_EXT["quoted"]["text"]  # 原文一字不动
    assert "translation_zh" not in ext  # 与文章译文缓存分开


def test_multi_paragraph_post_keeps_its_blank_lines(monkeypatch, tmp_path):
    """单元协议(#206):空行不交给模型、原地保留,逐行译文对位还原;模型不必复现换行。"""
    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_paragraphs.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    content = "ChatGPT Voice is now in the desktop app.\n\nControl your computer.\n\nRolling out today."
    _seed_social(sink.engine, "p1", content, {"lang": "en"})
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        body = client.post("/api/reader/ai/social/p1/translate").json()
    assert calls[0]["texts"] == ["ChatGPT Voice is now in the desktop app.", "Control your computer.", "Rolling out today."]
    assert body["text"] == "译：ChatGPT Voice is now in the desktop app.\n\n译：Control your computer.\n\n译：Rolling out today."


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
    assert calls[0]["texts"] == ["Day 0 on Qoder: Qwen3.8-Max-Preview is live."]


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


def test_unreachable_posts_share_one_404_shape(monkeypatch, tmp_path):
    """隐藏源、未订阅私有源、不存在的 ID:状态码与响应正文完全一致,零模型调用(检视 R1-01)。"""
    from services import source_visibility
    from services.user_sources import USER_SOURCE_PREFIX

    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_hidden.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "hidden", "it is good now!", QUOTE_POST_EXT, source_id="x_hidden")
    _seed_social(sink.engine, "private", "it is good now!", QUOTE_POST_EXT, source_id=f"{USER_SOURCE_PREFIX}someone")
    with Session(sink.engine) as session:
        source_visibility.set_source_hidden(session, "x_hidden", True)
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        responses = [client.post(f"/api/reader/ai/social/{article_id}/translate") for article_id in ("hidden", "private", "missing")]
    assert [(r.status_code, r.json()) for r in responses] == [(404, {"detail": "文章不存在"})] * 3
    assert calls == []


def test_text_after_zero_width_no_break_space_reaches_the_model(monkeypatch, tmp_path):
    """U+FEFF 紧跟链接:链接仍受保护,后面的文字真的交给模型翻译(复检 R1-04 回归)。"""
    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_feff.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    content = "https://t.co/abc" + chr(0xFEFF) + "Hello"
    _seed_social(sink.engine, "p1", content, {"lang": "en"})
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        body = client.post("/api/reader/ai/social/p1/translate").json()
    assert len(calls) == 1
    sent = calls[0]["texts"][0]
    assert "Hello" in sent and "https://t.co" not in sent
    assert body["text"] == "译：https://t.co/abc" + chr(0xFEFF) + "Hello"


def test_piece_with_nothing_for_the_model_is_never_cached_as_translation(monkeypatch, tmp_path):
    """兜底:判定说要译、分段计划却全是受保护字面量(如行内公式)时,这段不出译文,整条都没有就 400,不落缓存。"""
    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_literal.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "only_math", "$Hello$", {"lang": "en"})
    _seed_social(sink.engine, "math_and_quote", "$Hello$", {
        "lang": "en", "quoted": {"text": "big memory update", "lang": "en"},
    })
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/ai/social/only_math/translate").status_code == 400
        assert calls == []
        body = client.post("/api/reader/ai/social/math_and_quote/translate").json()
    assert body["text"] is None and body["quoted"] == "译：big memory update"
    assert calls[0]["texts"] == ["big memory update"]
    assert "translation_zh_social" not in _ext_of(sink.engine, "only_math")


def test_quota_429_tells_the_client_when_it_resets(monkeypatch, tmp_path):
    """读者 AI 日额度的 429 带 Retry-After(距服务端下一个零点的秒数),前端据此恢复入口(复检 R1-03)。"""
    import datetime

    from models.db import AiUsageRecord

    app_module, sink = _base_setup(monkeypatch, tmp_path, "social_tr_quota.db")
    _configure_llm(sink.engine)
    _set_ai_beta(sink.engine, True)
    _seed_social(sink.engine, "p1", "it is good now!", QUOTE_POST_EXT)
    with Session(sink.engine) as session:
        session.add(AiUsageRecord(
            day=datetime.date.today().isoformat(), username="user", purpose="translate", model="m",
            calls=50, prompt_tokens=0, completion_tokens=0, total_tokens=0,
            updated_at=datetime.datetime.now().isoformat(),
        ))
        session.commit()
    calls = _patch_social_llm(monkeypatch)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/social/p1/translate")
    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 86401
    assert calls == []


def test_retry_after_is_bound_to_the_day_the_quota_was_judged():
    """额度查询跨过零点:旧日的拒绝不能配上新一天的恢复时刻,恢复点已过给 1 秒(检视终审)。"""
    import datetime

    from api.routers.reader import _daily_reset_headers

    old_day = datetime.date(2026, 10, 21)
    just_after_midnight = datetime.datetime(2026, 10, 22, 0, 0, 0, 1000)
    assert _daily_reset_headers(old_day, now=just_after_midnight) == {"Retry-After": "1"}
    one_hour_before = datetime.datetime(2026, 10, 21, 23, 0, 0)
    assert _daily_reset_headers(old_day, now=one_hour_before) == {"Retry-After": "3601"}


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

    async def missing_unit(*, messages, config, **kwargs):
        units = json.loads(messages[-1].content)["units"]
        return json.dumps({"units": [{"id": units[0]["id"], "text": "译文"}]})

    async def dropped_link(*, messages, config, **kwargs):
        units = json.loads(messages[-1].content)["units"]
        return json.dumps({"units": [{"id": u["id"], "text": "丢了链接的译文"} for u in units]})

    async def extra_newline(*, messages, config, **kwargs):
        return _units_reply(messages, lambda text: f"译：{text}\n多出来的一行")[1]

    async def not_units(*, messages, config, **kwargs):
        return json.dumps({"text.0": "译文"})

    with TestClient(app_module.app) as client:
        _login(client)
        for fake in (truncated, missing_unit, dropped_link, extra_newline, not_units):
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
        return _units_reply(messages)[1]

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
