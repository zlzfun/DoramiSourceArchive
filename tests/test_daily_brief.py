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
    dedup_clusters,
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
            extensions_json="{}",
        ))
        session.commit()


def _seed_persisted_analysis(engine, article_id, *, score=4.5, genre="research_paper"):
    from models.db import (
        ArticleAnalysisRecord,
        ArticleTagAssignmentRecord,
        CmsTagRecord,
    )

    now = "2026-06-05T12:00:00+00:00"
    with Session(engine) as session:
        topic = CmsTagRecord(
            code=f"topic-{article_id}", kind="topic", name_zh="智能体", name_en="Agent",
            normalized_name=f"agent-{article_id}", status="active", created_at=now, updated_at=now,
        )
        entity = CmsTagRecord(
            code=f"entity-{article_id}", kind="entity", name_zh="", name_en="OpenAI",
            normalized_name=f"openai-{article_id}", status="active", created_at=now, updated_at=now,
        )
        session.add(topic)
        session.add(entity)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article_id, status="succeeded", tagging_status="succeeded",
            quality_score=score, score_reason="这是文章级评分理由，不能变成公共点评",
            one_sentence_summary="新一句话摘要", summary="- 新摘要第一点\n- 新摘要第二点",
            content_genre=genre, content_hash="hash", model_name="analysis-model",
            prompt_version="article-analysis-v1", scoring_version="content-value-v1",
            analyzed_at=now, tagged_at=now, created_at=now, updated_at=now,
        ))
        session.add(ArticleTagAssignmentRecord(
            article_id=article_id, tag_id=topic.id, tag_kind="topic", is_primary=True,
            relevance=0.9, assignment_source="llm", created_at=now, updated_at=now,
        ))
        session.add(ArticleTagAssignmentRecord(
            article_id=article_id, tag_id=entity.id, tag_kind="entity", is_primary=False,
            relevance=0.99, assignment_source="llm", created_at=now, updated_at=now,
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
        candidates, max_seen, _ = collect_candidates(session, cursor="2026-06-02T00:00:00")
    ids = {c.id for c in candidates}
    assert ids == {"a2"}  # a1 在游标前被排除；日报自身被排除
    assert max_seen == "2026-06-03T00:00:00"


def test_collect_candidates_empty_cursor_takes_all_recent(tmp_path):
    # 空游标（含手动重置）不设时间地板：即便文章是很久以前入库的，也应作为候选重做
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "old1", "src_a", "2026-01-01T00:00:00")
    _seed(sink.engine, "old2", "src_b", "2026-02-01T00:00:00")
    with Session(sink.engine) as session:
        candidates, max_seen, _ = collect_candidates(session, cursor="")
    assert {c.id for c in candidates} == {"old1", "old2"}
    assert max_seen == "2026-02-01T00:00:00"


def test_collect_candidates_empty_cursor_caps_total(tmp_path):
    # 空游标取最新 max_total 篇，受上限兜底，不会全库
    sink = _make_sink(tmp_path)
    for i in range(6):
        _seed(sink.engine, f"n{i}", f"s{i}", f"2026-06-0{i+1}T00:00:00")
    with Session(sink.engine) as session:
        candidates, _, scanned = collect_candidates(session, cursor="", max_total=3)
    assert len(candidates) == 3
    # 取最新的三篇（06-06 / 06-05 / 06-04）
    assert {c.id for c in candidates} == {"n5", "n4", "n3"}
    assert scanned == 6  # 裁剪观测:扫描总数如实上报(6 篇里取用 3)


def test_collect_candidates_per_source_cap(tmp_path):
    sink = _make_sink(tmp_path)
    for i in range(5):
        _seed(sink.engine, f"x{i}", "busy", f"2026-06-1{i}T00:00:00")
    with Session(sink.engine) as session:
        candidates, _, _ = collect_candidates(session, cursor="2026-06-01T00:00:00", per_source_cap=2)
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


def _scored_full(score, *, source="s", classification="行业资讯", source_url="", summary=None,
                 company="", content_type="rss_article", item_id=None):
    cand = BriefCandidate(id=item_id or f"id-{score}-{source}-{classification}", title="t", source_id=source,
                          source_url=source_url, content_type=content_type, publish_date="", fetched_date="",
                          has_content=True, body="")
    return ScoredItem(candidate=cand, score=score, classification=classification,
                      summary=summary or [], company=company)


def test_select_top_paper_cap_limits_and_deprioritizes():
    # 5 篇高分论文 + 5 条低分行业资讯；paper_cap=2、top_n=6 →
    # 即便论文分更高，也只入选 2 篇，腾出名额给行业资讯（有足够其它内容时配额硬生效）
    papers = [_scored_full(9 - i, source="hf", classification="学术论文", item_id=f"p{i}") for i in range(5)]
    industry = [_scored_full(4 - i, source=f"news{i}", classification="行业资讯", item_id=f"n{i}") for i in range(5)]
    selected = select_top(papers + industry, top_n=6, per_source_cap=10, per_realm_cap=10, paper_cap=2)
    assert sum(1 for it in selected if it.classification == "学术论文") == 2  # 论文被配额限制
    assert sum(1 for it in selected if it.classification == "行业资讯") == 4  # 行业资讯占满其余名额


def test_select_top_paper_cap_via_content_type():
    # 即使 classification 不是「学术论文」，content_type=arxiv 也算论文，受同一配额约束
    papers = [_scored_full(9 - i, source="hf", classification="", content_type="arxiv", item_id=f"a{i}")
              for i in range(4)]
    industry = [_scored_full(3 - i, source=f"news{i}", classification="行业资讯", item_id=f"n{i}") for i in range(3)]
    selected = select_top(papers + industry, top_n=4, paper_cap=2)
    assert sum(1 for it in selected if it.candidate.content_type == "arxiv") == 2  # arxiv 计入论文配额


# ---------------- dedup_clusters 同事件去重 ----------------

def test_dedup_clusters_merges_same_event(monkeypatch):
    async def _fake_cluster(*, messages, config, **kwargs):
        return json.dumps({"clusters": [[0, 1]]})  # 前两条是同一事件

    monkeypatch.setattr(db, "chat_completion", _fake_cluster)
    items = [
        _scored_full(7, source="ithome", source_url="https://a.test/1", item_id="x1"),
        _scored_full(9, source="qbit", source_url="https://b.test/2", item_id="x2"),  # 分更高 → 代表
        _scored_full(5, source="other", source_url="https://c.test/3", item_id="x3"),
    ]
    result = asyncio.run(dedup_clusters(items, CONFIGURED))
    ids = {it.candidate.id for it in result}
    assert ids == {"x2", "x3"}  # x1 被合并掉，保留高分代表 x2
    rep = next(it for it in result if it.candidate.id == "x2")
    assert "https://a.test/1" in rep.extra_sources  # 被并入条目的来源链接收集到代表


def test_dedup_clusters_degrades_on_llm_failure(monkeypatch):
    async def _boom(*, messages, config, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(db, "chat_completion", _boom)
    items = [_scored_full(7, item_id="x1"), _scored_full(8, item_id="x2")]
    result = asyncio.run(dedup_clusters(items, CONFIGURED))
    assert {it.candidate.id for it in result} == {"x1", "x2"}  # 失败降级：原样返回，不丢条目


def test_dedup_clusters_ignores_singleton_and_bad_idx(monkeypatch):
    async def _fake(*, messages, config, **kwargs):
        return json.dumps({"clusters": [[0], [99], [1, 2]]})  # 单元素/越界忽略，[1,2] 合并

    monkeypatch.setattr(db, "chat_completion", _fake)
    items = [_scored_full(7, source_url="u0", item_id="x0"),
             _scored_full(6, source_url="u1", item_id="x1"),
             _scored_full(9, source_url="u2", item_id="x2")]
    result = asyncio.run(dedup_clusters(items, CONFIGURED))
    assert {it.candidate.id for it in result} == {"x0", "x2"}  # x1 并入 x2（更高分）
    rep = next(it for it in result if it.candidate.id == "x2")
    assert rep.extra_sources == ["u1"]


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


def test_generate_attributes_usage_to_triggering_admin(tmp_path, monkeypatch):
    """手动触发：map/dedup/reduce 各阶段的用量 usage_meta 归到触发的 admin；默认归 system。"""
    seen_users = []

    async def _capture(*, messages, config, **kwargs):
        meta = kwargs.get("usage_meta")
        if meta is not None:
            seen_users.append(meta.username)
        return await _fake_chat_completion(messages=messages, config=config, **kwargs)

    monkeypatch.setattr(db, "chat_completion", _capture)
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    _seed(sink.engine, "a2", "src_b", "2026-06-05T11:00:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")

    # 手动触发归到 alice。
    asyncio.run(generate_daily_brief(
        storage=sink, llm_config=CONFIGURED, report_date="2026-06-06", triggered_by="alice"
    ))
    assert seen_users  # 至少 map + reduce 各产生一次
    assert set(seen_users) == {"alice"}

    # 定时调度（无 triggered_by）归到 system。
    seen_users.clear()
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    asyncio.run(generate_daily_brief(
        storage=sink, llm_config=CONFIGURED, report_date="2026-06-07", trigger="scheduled"
    ))
    assert set(seen_users) == {"system"}


def test_generate_success_writes_and_advances_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    from services import personal_digest

    notified = []
    monkeypatch.setattr(
        personal_digest,
        "notify_public_daily_brief_ready",
        lambda engine, *, report_date: notified.append((engine, report_date)),
    )
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    _seed(sink.engine, "a2", "src_b", "2026-06-05T11:00:00")
    _seed(sink.engine, "nobody", "src_c", "2026-06-05T12:00:00", has_content=False)
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    result = asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))
    assert result["status"] == "success"
    assert result["article_id"] == "daily_brief_2026-06-06"
    assert notified == [(sink.engine, "2026-06-06")]

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


def test_generate_empty_content_fails_no_write_no_cursor_move(tmp_path, monkeypatch):
    """正文空产必须失败:不写库、不推游标(_persist_brief 断言护栏)。

    2026-08 生产事故回归:空正文曾以 status=success 落库成 NULL。v3.34 起
    正文由 render_brief_markdown 确定性渲染、LLM 空产已无从发生,此处直接
    模拟渲染层回空,验证护栏仍在(防未来回归)。
    """
    import pytest

    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(db, "render_brief_markdown", lambda *a, **k: "")
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    with pytest.raises(RuntimeError, match="日报正文为空"):
        asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))
    assert asyncio.run(sink.get("daily_brief_2026-06-06")) is None  # 未写库
    with Session(sink.engine) as session:
        assert db.read_cursor(session) == "2026-06-01T00:00:00"  # 未推进,候选下轮重来


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


def test_collect_candidates_respects_source_scope(tmp_path):
    """源范围名单只圈定扫描面:名单外文章不进候选、不推进游标;None=全部。"""
    from sqlmodel import Session
    from storage.impl.db_storage import DatabaseStorage
    from models.db import ArticleRecord
    import services.daily_brief as db

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'scope.db'}")
    with Session(sink.engine) as session:
        for i, sid in enumerate(["src_a", "src_a", "src_b"]):
            session.add(ArticleRecord(
                id=f"a{i}", title=f"t{i}", content_type="rss_article", source_id=sid,
                source_url=f"https://x.test/{i}", publish_date="2026-07-17T00:00:00",
                fetched_date=f"2026-07-17T0{i}:00:00", has_content=True,
                content="正文" * 50, extensions_json="{}",
            ))
        session.commit()

        all_cands, seen_all, _ = db.collect_candidates(session, cursor="")
        assert {c.source_id for c in all_cands} == {"src_a", "src_b"}
        assert seen_all == "2026-07-17T02:00:00"

        scoped, seen_scoped, _ = db.collect_candidates(session, cursor="", source_ids=["src_a"])
        assert {c.source_id for c in scoped} == {"src_a"} and len(scoped) == 2
        # 游标只由名单内文章推进(src_b 的 02:00 不计)
        assert seen_scoped == "2026-07-17T01:00:00"


def test_source_scope_setting_roundtrip(tmp_path):
    from sqlmodel import Session
    from storage.impl.db_storage import DatabaseStorage
    import services.daily_brief as db

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'scope_kv.db'}")
    with Session(sink.engine) as session:
        assert db.read_source_scope(session) is None
        db.write_source_scope(session, ["b", "a", "a", " "])
        assert db.read_source_scope(session) == ["a", "b"]
        db.write_source_scope(session, [])
        assert db.read_source_scope(session) is None


# ---------------- 确定性渲染(v3.34) ----------------

def test_render_brief_markdown_sections_and_entries():
    items = [
        _scored_full(9, source="qbit", classification="模型发布", source_url="https://a.test/1",
                     summary=["**新模型**：细节一", "**上下文**：128K"], item_id="m1"),
        _scored_full(7, source="ithome", classification="行业资讯", source_url="https://b.test/2",
                     summary=["**融资**：金额"], item_id="n1"),
    ]
    items[0].title_cn = "重磅模型发布"
    items[0].comment = "为什么重要的判断"
    items[0].extra_sources = ["https://mirror.test/path"]
    items[1].title_cn = "行业新闻"
    title_only = [BriefCandidate(id="t1", title="仅标题条目", source_id="s", source_url="https://c.test/3",
                                 content_type="rss_article", publish_date="", fetched_date="", has_content=False, body="")]
    md = db.render_brief_markdown(items, title_only, report_date="2026-08-16")
    # 报头/报尾与收录计数
    assert md.startswith("# 🤖 哆啦美 AI 资讯日报 · 2026-08-16")
    assert "共收录 3 条资讯" in md and md.rstrip().endswith("*由哆啦美·归档中枢生成*")
    # 分节按序:模型发布在行业资讯之前
    assert md.index("## 🚀 模型发布（1 篇）") < md.index("## 📱 行业资讯（1 篇）")
    # 条目四件套:标题链接/来源行(含 extra_sources 域名)/总结/点评
    assert "### [重磅模型发布](https://a.test/1)" in md
    assert "[mirror.test](https://mirror.test/path)" in md
    assert "- **新模型**：细节一" in md
    assert "> 💡 点评：为什么重要的判断" in md
    # 无 comment 的条目不渲染点评行
    entry2 = md[md.index("### [行业新闻]"):]
    assert "点评" not in entry2.split("---")[0]
    # 仅标题附录
    assert "## 📎 其它收录" in md and "- [仅标题条目](https://c.test/3)" in md


def test_render_brief_unknown_classification_falls_back():
    item = _scored_full(5, classification="产业资讯", content_type="rss_article", item_id="u1")
    md = db.render_brief_markdown([item], [], report_date="2026-08-16")
    assert "## 🌐 资讯聚合（1 篇）" in md  # 非法分类回落 content_type 映射


def test_render_brief_followup_note_rendered():
    item = _scored_full(8, classification="模型发布", source_url="https://a.test/1", item_id="f1")
    item.followup_note = "新增 API 定价与开放注册"
    md = db.render_brief_markdown([item], [], report_date="2026-08-16")
    assert "*（接前报）新增 API 定价与开放注册*" in md


# ---------------- 跨天查重(v3.34) ----------------

def _recent_days():
    return [{"date": "2026-08-15", "titles": ["昨日已报的模型发布"]}]


def test_cross_day_dedup_drops_and_annotates(monkeypatch):
    async def _fake(*, messages, config, **kwargs):
        return json.dumps({"drop": [0], "followups": [{"idx": 1, "note": "开放了 API"}]})

    monkeypatch.setattr(db, "chat_completion", _fake)
    items = [_scored_full(9, item_id="dup"), _scored_full(8, item_id="follow"), _scored_full(7, item_id="fresh")]
    result = asyncio.run(db.cross_day_dedup(items, _recent_days(), CONFIGURED))
    assert [it.candidate.id for it in result] == ["follow", "fresh"]
    assert result[0].followup_note == "开放了 API"
    assert result[1].followup_note == ""


def test_cross_day_dedup_degrades_on_failure(monkeypatch):
    async def _boom(*, messages, config, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(db, "chat_completion", _boom)
    items = [_scored_full(9, item_id="a"), _scored_full(8, item_id="b")]
    result = asyncio.run(db.cross_day_dedup(items, _recent_days(), CONFIGURED))
    assert [it.candidate.id for it in result] == ["a", "b"]  # 失败降级不丢条目


def test_cross_day_dedup_all_drop_safety_valve(monkeypatch):
    async def _fake(*, messages, config, **kwargs):
        return json.dumps({"drop": [0, 1], "followups": []})

    monkeypatch.setattr(db, "chat_completion", _fake)
    items = [_scored_full(9, item_id="a"), _scored_full(8, item_id="b")]
    result = asyncio.run(db.cross_day_dedup(items, _recent_days(), CONFIGURED))
    assert len(result) == 2  # 要求丢弃全部视为误判,安全阀忽略 drop


def test_cross_day_dedup_skips_without_recent(monkeypatch):
    async def _boom(*, messages, config, **kwargs):
        raise AssertionError("无近期日报时不应调用 LLM")

    monkeypatch.setattr(db, "chat_completion", _boom)
    items = [_scored_full(9, item_id="a")]
    result = asyncio.run(db.cross_day_dedup(items, [], CONFIGURED))
    assert [it.candidate.id for it in result] == ["a"]


def test_fetch_recent_brief_items_reads_ext_and_falls_back(tmp_path):
    sink = _make_sink(tmp_path)
    # 有 extensions.items 的日报:读 title_cn
    _seed(sink.engine, "daily_brief_2026-08-15", db.DAILY_BRIEF_SOURCE_ID, "2026-08-15T08:30:00",
          content_type="daily_brief", publish_date="2026-08-15")
    from models.db import ArticleRecord
    with Session(sink.engine) as session:
        rec = session.get(ArticleRecord, "daily_brief_2026-08-15")
        rec.extensions_json = json.dumps({"items": [{"title_cn": "结构化条目标题"}]})
        session.add(rec)
        session.commit()
    # 无 items 的旧日报:从正文 ### 标题行回退提取
    _seed(sink.engine, "daily_brief_2026-08-14", db.DAILY_BRIEF_SOURCE_ID, "2026-08-14T08:30:00",
          content_type="daily_brief", publish_date="2026-08-14",
          content="# 报头\n\n### [正文提取的标题](https://x.test/1)\n内容\n")
    with Session(sink.engine) as session:
        days = db.fetch_recent_brief_items(session, days=3)
    by_date = {d["date"]: d["titles"] for d in days}
    assert by_date["2026-08-15"] == ["结构化条目标题"]
    assert by_date["2026-08-14"] == ["正文提取的标题"]


# ---------------- map 失败条目降级(v3.34) ----------------

def test_generate_map_failed_items_fall_to_appendix(tmp_path, monkeypatch):
    """map 失败的条目不得进正选(曾以默认 3 分混入,渲染出无总结残条目),
    降入「📎 其它收录」附录保标题与链接。"""
    async def _fail_one(*, messages, config, **kwargs):
        user = messages[1].content
        if "标题-bad" in user:
            raise RuntimeError("llm down for this one")
        return await _fake_chat_completion(messages=messages, config=config, **kwargs)

    monkeypatch.setattr(db, "chat_completion", _fail_one)
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "good", "src_a", "2026-06-05T10:00:00")
    _seed(sink.engine, "bad", "src_b", "2026-06-05T11:00:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    result = asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))
    assert result["status"] == "success"
    record = asyncio.run(sink.get("daily_brief_2026-06-06"))
    ext = json.loads(record.extensions_json)
    # 正选 items 只有 map 成功的一条;失败条目以附录形式仍在收录名单里
    assert len(ext["items"]) == 1
    assert set(ext["included_article_ids"]) == {"good", "bad"}
    assert "## 📎 其它收录" in record.content and "标题-bad" in record.content


def test_generate_last_run_reports_scan_and_use(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    sink = _make_sink(tmp_path)
    for i in range(4):
        _seed(sink.engine, f"c{i}", f"s{i}", f"2026-06-05T0{i}:00:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED,
                                     report_date="2026-06-06", max_total=2))
    with Session(sink.engine) as session:
        last = db.get_json_setting(session, db.KEY_LAST_RUN, None)
    assert last["candidates_scanned"] == 4
    assert last["candidates_used"] == 2  # 裁剪不再静默:两个读数都进 last_run


# ---------------- 权威机械层(v3.35) ----------------

def _role(item, role):
    item.candidate.source_role = role
    return item


def test_source_role_backend_mirror():
    """后端 source_role 与前端 sourceTaxonomy 同判定序;注册表查不到默认 media。"""
    from services.source_naming import source_role

    assert source_role("rss_openai_news") == "official"
    assert source_role("x_karpathy") == "personal"       # scope 判个人,压过基类 tier0
    assert source_role("web_qbitai") == "media"
    assert source_role("docs_arena_leaderboard_changelog") == "leaderboard"
    assert source_role("podcast_nvidia_ai") == "official"
    assert source_role("podcast_dwarkesh") == "personal"
    assert source_role(
        "runtime_config_source",
        source_scope="research_lab",
        provenance_tier="tier0_primary",
    ) == "official"
    assert source_role("some_config_source_not_in_registry") == "media"
    assert source_role("") == "media"


def test_select_top_official_bonus_breaks_ties_not_bands():
    media_8 = _role(_scored(8, "media_a"), "media")
    official_8 = _role(_scored(8, "official_a"), "official")
    media_9 = _role(_scored(9, "media_b"), "media")
    official_7 = _role(_scored(7, "official_b"), "official")
    selected = select_top([media_8, official_8, media_9, official_7], top_n=4)
    # 同分段官方置顶;+0.5 有界加成绝不跨分数段(官方 7→7.5 仍在媒体 8 之下)
    assert [it.candidate.source_id for it in selected] == [
        "media_b", "official_a", "media_a", "official_b",
    ]


def test_pick_cluster_representative_authority_gap():
    official_8 = _role(_scored(8, "off"), "official")
    media_9 = _role(_scored(9, "med"), "media")
    media_7 = _role(_scored(7, "med2"), "media")
    # 分差 1.0 内官方优先当代表
    items = [media_9, official_8, media_7]
    assert db._pick_cluster_representative(items, [0, 1, 2]) == 1
    # 分差超门限(官方一行推文 vs 媒体深度整理)回归最高分
    official_7 = _role(_scored(7, "off2"), "official")
    items2 = [media_9, official_7]
    assert db._pick_cluster_representative(items2, [0, 1]) == 0
    # 多官方并列取分高者(官博赢过官推)
    official_85 = _role(_scored(8.5, "off3"), "official")
    items3 = [media_9, official_8, official_85]
    assert db._pick_cluster_representative(items3, [0, 1, 2]) == 2


def test_cross_day_dedup_official_drop_downgraded(monkeypatch):
    """官方条目被判 drop 时机械降级为 followup 保留;媒体照删。"""
    async def _fake(*, messages, config, **kwargs):
        return json.dumps({"drop": [0, 1], "followups": []})

    monkeypatch.setattr(db, "chat_completion", _fake)
    official = _role(_scored_full(8, item_id="off"), "official")
    media = _role(_scored_full(9, item_id="med"), "media")
    fresh = _scored_full(7, item_id="fresh")
    result = asyncio.run(db.cross_day_dedup([official, media, fresh], _recent_days(), CONFIGURED))
    ids = [it.candidate.id for it in result]
    assert "off" in ids and "med" not in ids and "fresh" in ids
    kept = next(it for it in result if it.candidate.id == "off")
    assert kept.followup_note == "官方一手确认"


def test_collect_candidates_fills_source_role(tmp_path):
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "a1", "src_unknown", "2026-06-05T00:00:00")
    with Session(sink.engine) as session:
        candidates, _, _ = collect_candidates(session, cursor="2026-06-01T00:00:00")
    assert candidates[0].source_role == "media"  # 注册表查不到 → media,无官方待遇


def test_collect_candidates_honors_runtime_source_config_role(tmp_path):
    from models.db import SourceConfigRecord

    sink = _make_sink(tmp_path)
    _seed(sink.engine, "podcast-1", "podcast_runtime", "2026-06-05T00:00:00")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast_runtime",
            name="Runtime Podcast",
            source_type="podcast",
            source_scope="expert_commentary",
            provenance_tier="tier2_commentary",
            created_at="2026-06-01T00:00:00",
            updated_at="2026-06-01T00:00:00",
        ))
        session.commit()
        candidates, _, _ = collect_candidates(
            session,
            cursor="2026-06-01T00:00:00",
        )

    assert candidates[0].source_role == "personal"


# ---------------- 同日重跑合并 / 查重回补 / map 重试(v3.35) ----------------

def test_fetch_recent_brief_items_excludes_date(tmp_path):
    sink = _make_sink(tmp_path)
    from models.db import ArticleRecord

    for date in ("2026-06-05", "2026-06-06"):
        with Session(sink.engine) as session:
            session.add(ArticleRecord(
                id=f"daily_brief_{date}", title=f"日报{date}", content_type="daily_brief",
                source_id=db.DAILY_BRIEF_SOURCE_ID, source_url="", publish_date=date,
                fetched_date=f"{date}T09:00:00", has_content=True, content="### 条目",
                extensions_json=json.dumps({"items": [{"title_cn": f"条目{date}"}]}),
            ))
            session.commit()
    with Session(sink.engine) as session:
        out = db.fetch_recent_brief_items(session, days=3, exclude_date="2026-06-06")
    assert [d["date"] for d in out] == ["2026-06-05"]  # 当日自身不进对照物


def test_generate_same_day_rerun_merges_not_shrinks(tmp_path, monkeypatch):
    """同日二跑=增量合并:早间条目保留、新条目并入,不再整篇覆盖成残报。"""
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    sink = _make_sink(tmp_path)
    _seed(sink.engine, "morning1", "src_a", "2026-06-05T08:00:00")
    _seed(sink.engine, "morning2", "src_b", "2026-06-05T08:30:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    r1 = asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))
    assert r1["articles_count"] == 2
    # 午后新文章入库,游标已推进,二跑只有它一条增量
    _seed(sink.engine, "noon1", "src_c", "2026-06-06T12:00:00")
    r2 = asyncio.run(generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"))
    assert r2["status"] == "success"
    record = asyncio.run(sink.get("daily_brief_2026-06-06"))
    ext = json.loads(record.extensions_json)
    ids = {e["id"] for e in ext["items"]}
    assert ids == {"morning1", "morning2", "noon1"}  # 合并而非覆盖
    assert set(ext["included_article_ids"]) == {"morning1", "morning2", "noon1"}


def test_generate_refills_after_cross_day_drop(tmp_path, monkeypatch):
    """跨天查重剔条后从回补池补足:成品仍达 top_n,不再缺斤短两。"""
    async def _fake(*, messages, config, **kwargs):
        system = messages[0].content
        if "title_cn" in system and "score" in system:  # MAP
            return await _fake_chat_completion(messages=messages, config=config, **kwargs)
        if "跨天查重" in system:
            return json.dumps({"drop": [0, 1], "followups": []})  # 剔掉预选前两条
        return json.dumps({"clusters": []})

    monkeypatch.setattr(db, "chat_completion", _fake)
    sink = _make_sink(tmp_path)
    from models.db import ArticleRecord

    # 先放一篇昨日日报,让跨天查重有对照物
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="daily_brief_2026-06-05", title="昨日日报", content_type="daily_brief",
            source_id=db.DAILY_BRIEF_SOURCE_ID, source_url="", publish_date="2026-06-05",
            fetched_date="2026-06-05T09:00:00", has_content=True, content="### 旧条目",
            extensions_json=json.dumps({"items": [{"title_cn": "旧条目"}]}),
        ))
        session.commit()
    for i in range(6):
        _seed(sink.engine, f"n{i}", f"s{i}", f"2026-06-05T1{i}:00:00")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
    result = asyncio.run(generate_daily_brief(
        storage=sink, llm_config=CONFIGURED, report_date="2026-06-06", top_n=3))
    # 预选 3+buffer,剔 2 后幸存者裁回 top_n=3——回补条目天然过了跨天检查
    record = asyncio.run(sink.get("daily_brief_2026-06-06"))
    ext = json.loads(record.extensions_json)
    assert len(ext["items"]) == 3
    assert result["status"] == "success"


def test_map_summarize_retries_transient_failure(monkeypatch):
    """map 单篇瞬时失败在整轮后串行重试一次,端点恢复即救回、不降附录。"""
    calls = {"n": 0}

    async def _flaky(*, messages, config, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return await _fake_chat_completion(messages=messages, config=config, **kwargs)

    monkeypatch.setattr(db, "chat_completion", _flaky)
    cand = BriefCandidate(id="c1", title="t", source_id="s", source_url="", content_type="rss_article",
                          publish_date="", fetched_date="", has_content=True, body="正文")
    results = asyncio.run(map_summarize([cand], CONFIGURED))
    assert calls["n"] == 2
    assert results[0].map_ok is True  # 重试救回,不再定格失败


# ---------------- 公共日报 analysis shadow / adapter (WP-5) ----------------

def test_content_genre_mapping_covers_controlled_enum():
    expected = {
        "model_release": "模型发布", "product_update": "行业资讯",
        "open_source_update": "开源动态", "research_paper": "学术论文",
        "tutorial": "行业资讯", "opinion": "行业资讯",
        "industry_news": "行业资讯", "conference": "技术大会",
        "social_discussion": "社交动态", "aggregation": "资讯聚合",
        "security_incident": "行业资讯", "regulation": "行业资讯",
        "other": "资讯聚合",
    }
    assert {
        genre: db.content_genre_to_legacy_classification(genre)
        for genre in expected
    } == expected
    assert db.content_genre_to_legacy_classification("") == ""
    assert db.content_genre_to_legacy_classification("future_genre") == ""


def test_analysis_adapter_batch_loads_canonical_tags_and_preserves_comment(tmp_path):
    sink = _make_sink(tmp_path, "adapter_unit.db")
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    _seed_persisted_analysis(sink.engine, "a1")
    legacy = _scored_full(8, item_id="a1", classification="行业资讯", summary=["旧摘要"])
    legacy.comment = "旧公共点评"
    legacy.tags = ["旧自由标签"]

    with Session(sink.engine) as session:
        persisted = db.load_persisted_analysis_compat(session, ["a1"])
    assert persisted["a1"].canonical_tags == ("智能体", "OpenAI")

    [adapted] = db.apply_persisted_analysis_adapter([legacy], persisted)
    assert adapted.score == 4.5
    assert adapted.classification == "学术论文"
    assert adapted.summary == ["新摘要第一点", "新摘要第二点"]
    assert adapted.tags == ["智能体", "OpenAI"]
    assert adapted.comment == "旧公共点评"  # 绝不替换为 score_reason
    assert legacy.score == 8 and legacy.summary == ["旧摘要"]  # shadow 输入未被原地改写


def test_analysis_adapter_never_promotes_a_failed_legacy_map_item(tmp_path):
    sink = _make_sink(tmp_path, "adapter_failed_map.db")
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    _seed_persisted_analysis(sink.engine, "a1")
    failed = _scored_full(1, item_id="a1", classification="资讯聚合", summary=[])
    failed.map_ok = False

    with Session(sink.engine) as session:
        persisted = db.load_persisted_analysis_compat(session, ["a1"])
    [adapted] = db.apply_persisted_analysis_adapter([failed], persisted)

    assert adapted.map_ok is False
    assert adapted.score == 4.5


def test_adapter_flag_off_is_byte_compatible_and_records_shadow(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)

    def _run(name, explicit_false):
        sink = _make_sink(tmp_path, name)
        _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
        _seed_persisted_analysis(sink.engine, "a1")
        with Session(sink.engine) as session:
            db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
            if explicit_false:
                db.set_setting(session, db.KEY_ANALYSIS_ADAPTER_ENABLED, "false")
        asyncio.run(generate_daily_brief(
            storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"
        ))
        record = asyncio.run(sink.get("daily_brief_2026-06-06"))
        with Session(sink.engine) as session:
            metrics = db.get_json_setting(session, db.KEY_ANALYSIS_SHADOW_METRICS, {})
        return record.content, json.loads(record.extensions_json)["items"], metrics

    implicit = _run("adapter_default_off.db", False)
    explicit = _run("adapter_explicit_off.db", True)
    assert implicit[:2] == explicit[:2]  # 正文与结构化输出逐值一致
    content, [item], metrics = implicit
    assert "## 🌐 资讯聚合" in content  # legacy 的未知「产业资讯」照旧回落
    assert item["score"] == 8
    assert item["classification"] == "产业资讯"
    assert item["summary"] == ["**X**：细节"]
    assert item["tags"] == ["标签"]
    assert item["comment"] == "点评"
    assert metrics["adapter_enabled"] is False
    assert metrics["comparable_count"] == 1
    assert metrics["score_mean_abs_delta"] == 3.5


def test_adapter_flag_on_reads_analysis_without_adding_seven_point_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "chat_completion", _fake_chat_completion)
    sink = _make_sink(tmp_path, "adapter_on.db")
    _seed(sink.engine, "a1", "src_a", "2026-06-05T10:00:00")
    _seed_persisted_analysis(sink.engine, "a1", score=4.5, genre="research_paper")
    with Session(sink.engine) as session:
        db.set_setting(session, db.KEY_CURSOR, "2026-06-01T00:00:00")
        db.set_setting(session, db.KEY_ANALYSIS_ADAPTER_ENABLED, "true")

    result = asyncio.run(generate_daily_brief(
        storage=sink, llm_config=CONFIGURED, report_date="2026-06-06"
    ))
    assert result["articles_count"] == 1  # 4.5 分仍入选：未擅自启用个人日报 7 分门槛
    record = asyncio.run(sink.get("daily_brief_2026-06-06"))
    [item] = json.loads(record.extensions_json)["items"]
    assert item["score"] == 4.5
    assert item["classification"] == "学术论文"
    assert item["summary"] == ["新摘要第一点", "新摘要第二点"]
    assert item["tags"] == ["智能体", "OpenAI"]
    assert item["comment"] == "点评"
    assert "score_reason" not in item
    assert "## 📄 学术论文（1 篇）" in record.content
