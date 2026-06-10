import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import LLMConfig  # noqa: E402
from sqlmodel import Session  # noqa: E402

import services.daily_brief as db  # noqa: E402
from services.daily_brief import (  # noqa: E402
    BriefCandidate,
    ScoredItem,
    collect_candidates,
    generate_daily_brief,
    map_summarize,
    select_top,
)

CONFIGURED = LLMConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test-model")


def _make_sink(tmp_path, name="brief.db"):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _seed(engine, article_id, source_id, fetched_date, *, content="正文内容", content_type="rss_article",
          has_content=True, publish_date="2026-06-05"):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=f"标题-{article_id}", content_type=content_type, source_id=source_id,
            source_url=f"https://example.test/{article_id}", publish_date=publish_date,
            fetched_date=fetched_date, has_content=has_content, content=content if has_content else None,
            extensions_json="{}", is_vectorized=False,
        ))
        session.commit()


async def _fake_chat_completion(*, messages, config, **kwargs):
    system = messages[0].content
    if "title_cn" in system and "score" in system:  # MAP
        return json.dumps({
            "title_cn": "中文标题", "classification": "产业资讯", "source": "某来源",
            "company": "OpenAI", "realm": "基础大模型", "summary": ["**X**：细节"],
            "comment": "点评", "tags": ["标签"], "score": 8,
        })
    return "# 🤖 哆啦美 AI 资讯日报 · 2026-06-06\n\n正文\n\n*由哆啦美·归档中枢生成*"


# ---------------- collect_candidates ----------------

def test_collect_candidates_strict_cursor_and_excludes_self(tmp_path):
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "a1", "src_a", "2026-06-01T00:00:00")
    _seed(sink.engine, "a2", "src_a", "2026-06-03T00:00:00")
    _seed(sink.engine, "old_brief", db.DAILY_BRIEF_SOURCE_ID, "2026-06-04T00:00:00")
    with Session(sink.engine) as session:
        candidates, max_seen = collect_candidates(session, cursor="2026-06-02T00:00:00")
    ids = {c.id for c in candidates}
    assert ids == {"a2"}  # a1 在游标前被排除；日报自身被排除
    assert max_seen == "2026-06-03T00:00:00"


def test_collect_candidates_empty_cursor_takes_all_recent(tmp_path):
    # 空游标（含手动重置）不设时间地板：即便文章是很久以前入库的，也应作为候选重做
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "old1", "src_a", "2026-01-01T00:00:00")
    _seed(sink.engine, "old2", "src_b", "2026-02-01T00:00:00")
    with Session(sink.engine) as session:
        candidates, max_seen = collect_candidates(session, cursor="")
    assert {c.id for c in candidates} == {"old1", "old2"}
    assert max_seen == "2026-02-01T00:00:00"


def test_collect_candidates_empty_cursor_caps_total(tmp_path):
    # 空游标取最新 max_total 篇，受上限兜底，不会全库
    sink = _make_sink(tmp_path)
    for i in range(6):
        _seed(sink.engine, f"n{i}", f"s{i}", f"2026-06-0{i+1}T00:00:00")
    with Session(sink.engine) as session:
        candidates, _ = collect_candidates(session, cursor="", max_total=3)
    assert len(candidates) == 3
    # 取最新的三篇（06-06 / 06-05 / 06-04）
    assert {c.id for c in candidates} == {"n5", "n4", "n3"}


def test_collect_candidates_per_source_cap(tmp_path):
    sink = _make_sink(tmp_path)
    for i in range(5):
        _seed(sink.engine, f"x{i}", "busy", f"2026-06-1{i}T00:00:00")
    with Session(sink.engine) as session:
        candidates, _ = collect_candidates(session, cursor="2026-06-01T00:00:00", per_source_cap=2)
    assert len([c for c in candidates if c.source_id == "busy"]) == 2


# ---------------- select_top ----------------

def _scored(score, source, realm="r"):
    cand = BriefCandidate(id=f"id{score}{source}", title="t", source_id=source, source_url="",
                          content_type="rss_article", publish_date="", fetched_date="", has_content=True, body="")
    return ScoredItem(candidate=cand, score=score, realm=realm)


def test_select_top_respects_source_cap():
    items = [_scored(s, "same") for s in [9, 8, 7, 6, 5, 4]]
    selected = select_top(items, top_n=3, per_source_cap=2, per_realm_cap=10)
    # per_source_cap=2 限制，但 top_n=3 需补满 → overflow 补 1
    assert len(selected) == 3
    # 最高两分先入选
    assert selected[0].score == 9 and selected[1].score == 8


def test_select_top_orders_by_score():
    items = [_scored(3, "a"), _scored(9, "b"), _scored(6, "c")]
    selected = select_top(items, top_n=3)
    assert [it.score for it in selected] == [9, 6, 3]


def test_select_top_final_order_is_score_desc_after_diversity():
    # per_source_cap=1：高分的 9 会因来源配额被丢进 overflow、晚补入，
    # 但最终顺序必须按重要性降序，9 应回到第 2 位（在 5 之前）。
    items = [_scored(10, "a"), _scored(9, "a"), _scored(5, "b")]
    selected = select_top(items, top_n=3, per_source_cap=1, per_realm_cap=10)
    assert [it.score for it in selected] == [10, 9, 5]


# ---------------- top_n 配置 ----------------

def test_daily_brief_top_n_default_and_clamp(tmp_path):
    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        assert db.daily_brief_top_n(session) == db.DEFAULT_TOP_N  # 未设置用默认
        db.set_setting(session, db.KEY_TOP_N, "8")
        assert db.daily_brief_top_n(session) == 8
        db.set_setting(session, db.KEY_TOP_N, "999")  # 越界夹到上限
        assert db.daily_brief_top_n(session) == db.TOP_N_MAX
        db.set_setting(session, db.KEY_TOP_N, "abc")  # 非法值回落默认
        assert db.daily_brief_top_n(session) == db.DEFAULT_TOP_N


# ---------------- map_summarize 降级 ----------------

def test_map_failure_degrades(tmp_path, monkeypatch):
    async def _boom(*, messages, config, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(db, "chat_completion", _boom)
    cand = BriefCandidate(id="c1", title="T", source_id="s", source_url="", content_type="rss_article",
                          publish_date="", fetched_date="", has_content=True, body="body")
    scored = asyncio.run(map_summarize([cand], CONFIGURED))
    assert len(scored) == 1
    assert scored[0].map_ok is False
    assert scored[0].score == 3.0
    assert scored[0].title_cn == "T"


# ---------------- generate_daily_brief ----------------

def test_generate_empty_no_write_no_cursor_move(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    sink = _make_sink(tmp_path)
    # 游标设在未来，无候选
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2099-01-01T00:00:00")
    result = asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))
    assert result["status"] == "empty"
    with Session(sink.engine) as session:
        assert db.read_cursor(session) == "2099-01-01T00:00:00"  # 未推进
    assert asyncio.run(sink.get("daily_brief_2026-06-06")) is None  # 未写库


def test_generate_success_writes_and_advances_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    _seed(sink.engine, "a2", "src_b", "2026-06-05T11:00:00")
    _seed(sink.engine, "nobody", "src_c", "2026-06-05T12:00:00", has_content=False)
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    result = asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))
    assert result["status"] == "success"
    assert result["article_id"] == "daily_brief_2026-06-06"

    record = asyncio.run(sink.get("daily_brief_2026-06-06"))
    assert record is not None
    assert record.source_id == db.DAILY_BRIEF_SOURCE_ID
    assert record.content_type == "daily_brief"
    assert "资讯日报" in record.content
    ext = json.loads(record.extensions_json)
    assert ext["report_date"] == "2026-06-06"
    assert "a1" in ext["included_article_ids"]
    assert "nobody" in ext["included_article_ids"]  # 无正文条目也纳入附录

    with Session(sink.engine) as session:
        assert db.read_cursor(session) == "2026-06-05T12:00:00"  # 推进到最大 fetched_date
        last = db.get_json_setting(session, db.KEY_LAST_RUN, None)
        assert last["status"] == "success"


def test_generate_idempotent_rerun_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))

    # 第二次：再加一篇并回退游标，使同一 report_date 重跑走 update 覆盖
    _seed(sink.engine, "a2", "src_b", "2026-06-05T13:00:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    result = asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))
    assert result["status"] == "success"

    # 仍只有一条日报（update 而非新增）
    from models.db import ArticleRecord
    from sqlmodel import select
    with Session(sink.engine) as session:
        briefs = session.exec(select(ArticleRecord).where(ArticleRecord.source_id == db.DAILY_BRIEF_SOURCE_ID)).all()
    assert len(briefs) == 1


# ---------------- /api/articles exclude_source_ids ----------------

def test_apply_filters_exclude_source_ids(tmp_path):
    from api import app as app_module
    from models.db import ArticleRecord
    from sqlmodel import select

    sink = _make_sink(tmp_path, "exc.db")
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    _seed(sink.engine, "b1", db.DAILY_BRIEF_SOURCE_ID, "2026-06-06T00:00:00", content_type="daily_brief")
    with Session(sink.engine) as session:
        # 不排除（阅读器/订阅侧）：日报与采集内容都在
        all_rows = session.exec(app_module.apply_article_query_filters(select(ArticleRecord))).all()
        assert {r.id for r in all_rows} == {"a1", "b1"}
        # 排除日报源（知识台账）：仅剩采集内容
        kept = session.exec(app_module.apply_article_query_filters(
            select(ArticleRecord), exclude_source_ids=db.DAILY_BRIEF_SOURCE_ID)).all()
        assert {r.id for r in kept} == {"a1"}


# ---------------- 删除最新一期回退游标 ----------------

def test_delete_latest_brief_rewinds_cursor(tmp_path, monkeypatch):
    import api.app as app_module
    monkeypatch.setattr(app_module, "chat_completion", _fake_chat_completion, raising=False)
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    sink = _make_sink(tmp_path)
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "vector_sink", None)

    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))

    with Session(sink.engine) as session:
        assert db.read_cursor(session) == "2026-06-05T10:00:00"  # 已推进

    record = asyncio.run(sink.get("daily_brief_2026-06-06"))
    app_module._maybe_rewind_daily_brief_cursor(record)  # 模拟删除最新一期

    with Session(sink.engine) as session:
        assert db.read_cursor(session) == "2026-06-01T00:00:00"  # 回退到 cursor_before


def test_rewind_skips_when_not_latest(tmp_path, monkeypatch):
    import api.app as app_module
    sink = _make_sink(tmp_path)
    monkeypatch.setattr(app_module, "db_sink", sink)
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-09T00:00:00")  # 当前游标更靠后
    # 构造一条「旧」日报记录：其 cursor_after 不等于当前游标
    _seed(sink.engine, "daily_brief_2026-06-06", db.DAILY_BRIEF_SOURCE_ID, "2026-06-06T00:00:00",
          content_type="daily_brief")
    with Session(sink.engine) as session:
        from models.db import ArticleRecord
        rec = session.get(ArticleRecord, "daily_brief_2026-06-06")
        rec.extensions_json = json.dumps({"cursor_before": "2026-06-05T00:00:00", "cursor_after": "2026-06-06T00:00:00"})
        session.add(rec)
        session.commit()
    record = asyncio.run(sink.get("daily_brief_2026-06-06"))
    app_module._maybe_rewind_daily_brief_cursor(record)
    with Session(sink.engine) as session:
        assert db.read_cursor(session) == "2026-06-09T00:00:00"  # 不动


# ---------------- resolve_llm_config KV 覆盖 ----------------

def test_resolve_llm_config_kv_override(tmp_path, monkeypatch):
    import config as config_module
    from dataclasses import replace
    monkeypatch.setattr(config_module, "settings",
                        replace(config_module.settings,
                                llm=LLMConfig(base_url="ini-url", api_key="ini-key", model="ini-model")))
    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_LLM_MODEL, "kv-model")
        cfg = db.resolve_llm_config(session)
    assert cfg.model == "kv-model"      # KV 覆盖
    assert cfg.base_url == "ini-url"    # 未覆盖回退 ini
    assert cfg.api_key == "ini-key"
