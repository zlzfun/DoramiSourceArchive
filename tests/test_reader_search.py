"""订阅域问答检索管线单测（v3.30 检索扶正波,services/reader_search）。

覆盖:查询规划的 JSON 解析与清洗/失败降级、FTS 召回的关键词并集×订阅域×日期窗、
选篇的少候选短路/索引校验/失败回退、编排层的降级链(规划失败→原词检索→时序窗口)
与「日报即索引」作用域切换。全程不触网:chat_completion 以桩替换,FTS 用
DatabaseStorage 自建的 SQLite FTS5 索引(trigger 同步,seed 即可检索)。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session  # noqa: E402

from config import LLMConfig  # noqa: E402
from models.db import ArticleRecord  # noqa: E402
from services import reader_search  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def make_sink():
    return DatabaseStorage(db_url="sqlite:///:memory:")


def seed(engine, article_id, source_id, title, content, publish_date="2026-08-01"):
    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id,
            title=title,
            content_type="rss_article",
            source_id=source_id,
            source_url=f"https://example.test/{article_id}",
            publish_date=publish_date,
            fetched_date=f"{publish_date}T08:00:00",
            has_content=True,
            content=content,
            extensions_json="{}",
        ))
        session.commit()


_LLM = LLMConfig(base_url="https://llm.test/v1", api_key="sk-test", model="m")


def _patch_chat(monkeypatch, reply):
    """把 reader_search.chat_completion 换成返回固定文本/抛错的桩,返回 calls 列表。"""
    calls = []

    async def fake(*, messages, config, **kwargs):
        calls.append([m.content for m in messages])
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(reader_search, "chat_completion", fake)
    return calls


# ──────────────── 查询规划 ────────────────

def test_plan_query_parses_and_cleans(monkeypatch):
    _patch_chat(monkeypatch, (
        '{"keywords": [" Claude ", "anthropic 模型", 42, "a", "k1", "k2", "k3", "k4", "k5"],'
        ' "date_gte": "2026-08-01", "date_lte": null, "temporal": false, "use_brief": 1}'
    ))
    plan = run(reader_search.plan_query("q", _LLM))
    # 非字符串与过短项剔除、strip、封顶 6 组
    assert plan["keywords"][:2] == ["Claude", "anthropic 模型"]
    assert "a" not in plan["keywords"] and 42 not in plan["keywords"]
    assert len(plan["keywords"]) <= 6
    assert plan["date_gte"] == "2026-08-01"
    assert plan["date_lte"] is None
    assert plan["temporal"] is False
    assert plan["use_brief"] is True


def test_plan_query_failure_returns_none(monkeypatch):
    _patch_chat(monkeypatch, "这不是 JSON")
    assert run(reader_search.plan_query("q", _LLM)) is None
    _patch_chat(monkeypatch, RuntimeError("网络失败"))
    assert run(reader_search.plan_query("q", _LLM)) is None


# ──────────────── FTS 召回 ────────────────

def test_fetch_candidates_union_and_scope():
    sink = make_sink()
    seed(sink.engine, "a1", "src_a", "Alpha", "quantum computing breakthrough")
    seed(sink.engine, "a2", "src_a", "Beta", "tokenizer internals explained")
    seed(sink.engine, "a3", "src_b", "Gamma", "quantum computing elsewhere")

    hits = reader_search.fetch_candidates(
        sink.engine, keywords=["quantum", "tokenizer"], source_ids=["src_a"])
    assert {r.id for r in hits} == {"a1", "a2"}  # 关键词并集,src_b 被订阅域排除

    none = reader_search.fetch_candidates(
        sink.engine, keywords=["quantum"], source_ids=["src_missing"])
    assert none == []


def test_fetch_candidates_date_window():
    sink = make_sink()
    seed(sink.engine, "old", "src_a", "Old", "framework release note", publish_date="2026-06-01")
    seed(sink.engine, "new", "src_a", "New", "framework release note", publish_date="2026-08-05")
    hits = reader_search.fetch_candidates(
        sink.engine, keywords=["framework"], source_ids=["src_a"], date_gte="2026-07-01")
    assert [r.id for r in hits] == ["new"]


def test_fetch_candidates_empty_keywords():
    sink = make_sink()
    seed(sink.engine, "a1", "src_a", "Alpha", "some body text")
    assert reader_search.fetch_candidates(sink.engine, keywords=[], source_ids=["src_a"]) == []


def test_fetch_recent_window_orders_by_fetched_desc():
    sink = make_sink()
    seed(sink.engine, "a1", "src_a", "Older", "body", publish_date="2026-08-01")
    seed(sink.engine, "a2", "src_a", "Newer", "body", publish_date="2026-08-05")
    hits = reader_search.fetch_recent_window(sink.engine, ["src_a"])
    assert [r.id for r in hits] == ["a2", "a1"]


# ──────────────── 选篇 ────────────────

def _fake_records(n):
    return [
        ArticleRecord(
            id=f"r{i}", title=f"标题{i}", content_type="rss_article", source_id="s",
            source_url=f"u{i}", publish_date="2026-08-01", fetched_date="2026-08-01",
            has_content=True, content=f"正文{i}", extensions_json="{}",
        )
        for i in range(n)
    ]


def test_select_articles_skips_llm_when_few(monkeypatch):
    calls = _patch_chat(monkeypatch, '{"selected": [0]}')
    candidates = _fake_records(reader_search.SELECT_MAX)
    chosen = run(reader_search.select_articles("q", candidates, _LLM))
    assert chosen == candidates
    assert calls == []  # 候选 ≤ SELECT_MAX,不调用 LLM


def test_select_articles_picks_valid_indices(monkeypatch):
    _patch_chat(monkeypatch, '{"selected": [3, 99, 3, "x", 1]}')
    candidates = _fake_records(12)
    chosen = run(reader_search.select_articles("q", candidates, _LLM))
    # 越界/非整数/重复剔除,保序
    assert [r.id for r in chosen] == ["r3", "r1"]


def test_select_articles_honest_empty(monkeypatch):
    _patch_chat(monkeypatch, '{"selected": []}')
    chosen = run(reader_search.select_articles("q", _fake_records(12), _LLM))
    assert chosen == []  # LLM 判定无一相关时如实为空,问答侧诚实回答资料不足


def test_select_articles_failure_falls_back(monkeypatch):
    _patch_chat(monkeypatch, "not json at all")
    candidates = _fake_records(12)
    chosen = run(reader_search.select_articles("q", candidates, _LLM))
    assert chosen == candidates[: reader_search.SELECT_MAX]


# ──────────────── 编排(降级链) ────────────────

def test_subscription_context_empty_subscription():
    sink = make_sink()
    ctx, sources = run(reader_search.subscription_context(
        "q", engine=sink.engine, source_ids=[], llm_config=_LLM))
    assert ctx == "" and sources == []


def test_subscription_context_planned_search(monkeypatch):
    sink = make_sink()
    seed(sink.engine, "a1", "src_a", "量子突破", "quantum computing breakthrough body")
    seed(sink.engine, "a2", "src_a", "无关文章", "totally unrelated body")

    async def fake_plan(question, llm_config, usage_meta=None):
        return {"keywords": ["quantum"], "date_gte": None, "date_lte": None,
                "temporal": False, "use_brief": False}

    monkeypatch.setattr(reader_search, "plan_query", fake_plan)
    ctx, sources = run(reader_search.subscription_context(
        "量子计算有什么进展?", engine=sink.engine, source_ids=["src_a"], llm_config=_LLM))
    assert "量子突破" in ctx and "无关文章" not in ctx
    assert [s["title"] for s in sources] == ["量子突破"]
    assert sources[0]["source_url"] == "https://example.test/a1"


def test_subscription_context_temporal_uses_window(monkeypatch):
    sink = make_sink()
    seed(sink.engine, "a1", "src_a", "最新文章", "fresh body text")

    async def fake_plan(question, llm_config, usage_meta=None):
        return {"keywords": [], "date_gte": None, "date_lte": None,
                "temporal": True, "use_brief": False}

    monkeypatch.setattr(reader_search, "plan_query", fake_plan)
    ctx, sources = run(reader_search.subscription_context(
        "最近有什么?", engine=sink.engine, source_ids=["src_a"], llm_config=_LLM))
    assert "最新文章" in ctx
    assert sources[0]["title"] == "最新文章"


def test_subscription_context_plan_failure_falls_back_to_raw_query(monkeypatch):
    sink = make_sink()
    seed(sink.engine, "a1", "src_a", "Kimi 发布", "Kimi new model announcement body")

    async def fake_plan(question, llm_config, usage_meta=None):
        return None  # 规划失败

    monkeypatch.setattr(reader_search, "plan_query", fake_plan)
    ctx, sources = run(reader_search.subscription_context(
        "Kimi", engine=sink.engine, source_ids=["src_a"], llm_config=_LLM))
    assert "Kimi 发布" in ctx


def test_subscription_context_zero_hits_falls_back_to_window(monkeypatch):
    sink = make_sink()
    seed(sink.engine, "a1", "src_a", "兜底文章", "window fallback body")

    async def fake_plan(question, llm_config, usage_meta=None):
        return {"keywords": ["绝无此词组合"], "date_gte": None, "date_lte": None,
                "temporal": False, "use_brief": False}

    monkeypatch.setattr(reader_search, "plan_query", fake_plan)
    ctx, sources = run(reader_search.subscription_context(
        "问题", engine=sink.engine, source_ids=["src_a"], llm_config=_LLM))
    assert "兜底文章" in ctx  # FTS 零命中 → 时序窗口兜底


def test_subscription_context_use_brief_scopes_to_daily_brief(monkeypatch):
    sink = make_sink()
    seed(sink.engine, "b1", reader_search.BRIEF_SOURCE_ID, "八月第一周日报", "weekly recap of AI events")
    seed(sink.engine, "a1", "src_a", "原文报道", "weekly recap mentioned here too")

    async def fake_plan(question, llm_config, usage_meta=None):
        return {"keywords": ["recap"], "date_gte": None, "date_lte": None,
                "temporal": False, "use_brief": True}

    monkeypatch.setattr(reader_search, "plan_query", fake_plan)
    # 已订阅日报源:检索收敛到日报
    ctx, sources = run(reader_search.subscription_context(
        "本周盘点", engine=sink.engine,
        source_ids=["src_a", reader_search.BRIEF_SOURCE_ID], llm_config=_LLM))
    assert [s["title"] for s in sources] == ["八月第一周日报"]

    # 未订阅日报源:use_brief 忽略,照常在订阅域检索
    ctx2, sources2 = run(reader_search.subscription_context(
        "本周盘点", engine=sink.engine, source_ids=["src_a"], llm_config=_LLM))
    assert [s["title"] for s in sources2] == ["原文报道"]
