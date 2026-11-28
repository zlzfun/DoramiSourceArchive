"""生成流程的外壳与接口(issue #72):登记表、公共日报的薄适配、泳道一行要的数据、往期、调度。

守的事:

- 登记的每个流程都写进一个系统源,阶段、旋钮与配置对得上;
- 公共日报经外壳读写的就是它原来的那些 KV 键,校验文案与 ``/api/daily-brief/config`` 相同;
- 「上次结果」规整成 成功 / 跳过 / 失败,跳过的原因出现在「取材」一格;
- 接口只对管理员开放,改了开关或 cron 即重排调度;日报的 job id 仍是 ``daily_brief``;
- 个人早报不等日报以外的系统源,知识台账能一次排除全部系统源。
"""

import datetime as dt
import json
import os
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import ArticleRecord, SourceConfigRecord  # noqa: E402
from services import daily_brief, generation_flows as gf, source_catalog, trending_picks  # noqa: E402
from tests.conftest import seed_default_accounts  # noqa: E402

TODAY = dt.date(2026, 11, 27)
BRIEF, PICKS = gf.DAILY_BRIEF, gf.TRENDING_PICKS


def _sink(tmp_path, name="flows.db"):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _period(session, flow, day, *, extensions, fetched=None):
    session.add(ArticleRecord(
        id=f"{flow.id}_{day}", title=f"{flow.name} · {day}", content_type="x", source_id=flow.source_id,
        source_url="", publish_date=day, fetched_date=fetched or f"{day}T08:31:00", has_content=True,
        content="正文", extensions_json=json.dumps(extensions, ensure_ascii=False),
    ))


# ── 登记表 ────────────────────────────────────────────────────────────────────


def test_registry_invariants(tmp_path):
    flows = gf.all_flows()
    assert [flow.id for flow in flows] == ["daily_brief", "trending_picks"]
    assert len({flow.id for flow in flows}) == len(flows) == len({flow.source_id for flow in flows})
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        for flow in flows:
            # 产出写进系统源:不回流的规则才管得到它
            assert source_catalog.is_system_source(flow.source_id), flow.id
            assert gf.get_flow(flow.id) is flow
            assert 1 <= len(flow.phases) <= 6 and all(flow.phases)
            assert len(flow.default_cron.split()) == 5
            config = flow.read_config(session)
            for knob in flow.knobs:
                assert knob.field in config and knob.type in {"number", "toggle"}, (flow.id, knob.field)
                assert config[knob.field] == knob.default            # 没存过配置时读到的就是声明的缺省值
    assert gf.get_flow("nope") is None
    assert BRIEF.source_id == "dorami_daily_brief" and PICKS.source_id == "dorami_trending_picks"
    assert BRIEF.phases == ("收集", "评分", "精选", "点评", "成稿") and PICKS.phases == ("读榜", "挑选", "成稿")


# ── 一行要的数据 ──────────────────────────────────────────────────────────────


def test_describe_a_flow_that_never_ran(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        for flow, enabled in ((BRIEF, False), (PICKS, False)):
            row = gf.describe(session, flow, today=TODAY)
            assert (row["id"], row["name"], row["source_id"]) == (flow.id, flow.name, flow.source_id)
            assert row["enabled"] is enabled and row["cron"] == flow.default_cron == row["default_cron"]
            assert row["last_run"] is None and row["latest"] is None and row["total"] == 0
            assert row["running"] is False and row["next_run_at"] is None
            assert [day["date"] for day in row["recent_days"]] == [f"2026-11-{d}" for d in range(21, 28)]
            assert not any(day["produced"] for day in row["recent_days"])
            assert [knob["field"] for knob in row["knobs"]] == [knob.field for knob in flow.knobs]
            assert row["phases"] == list(flow.phases)
        assert gf.describe(session, BRIEF, today=TODAY)["input"] == {"label": "全部来源", "sub": "", "warn": ""}
        assert gf.describe(session, PICKS, today=TODAY)["input"] == {"label": "GitHub Trending 日榜", "sub": "", "warn": ""}


def test_describe_reports_latest_period_recent_days_and_total(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        for day, count in (("2026-11-20", 9), ("2026-11-25", 18), ("2026-11-27", 33)):
            _period(session, BRIEF, day, extensions={"articles_count": count, "llm_model": "deepseek-chat"})
        _period(session, PICKS, "2026-11-26", extensions={"picks_count": 6, "repo_count": 25}, fetched="2026-11-26T09:30:02")
        session.commit()

        brief = gf.describe(session, BRIEF, today=TODAY, next_run_at="2026-11-28T08:30:00+08:00")
        assert brief["latest"] == {
            "article_id": "daily_brief_2026-11-27", "date": "2026-11-27", "generated_at": "2026-11-27T08:31:00",
            "summary": "收录 33 条", "meta": "收录 33 条 · deepseek-chat",
        }
        assert brief["total"] == 3 and brief["next_run_at"] == "2026-11-28T08:30:00+08:00"
        assert [day["produced"] for day in brief["recent_days"]] == [False, False, False, False, True, False, True]

        picks = gf.describe(session, PICKS, today=TODAY)
        assert picks["latest"]["summary"] == picks["latest"]["meta"] == "选 6 个 · 榜单 25 个"
        assert [day["produced"] for day in picks["recent_days"]] == [False] * 5 + [True, False]


def test_period_summary_tolerates_missing_fields():
    assert BRIEF.period_summary({}) == ("", "")
    assert BRIEF.period_summary({"articles_count": 0}) == ("收录 0 条", "收录 0 条")
    assert PICKS.period_summary({}) == ("", "")
    assert PICKS.period_summary({"picks_count": 3}) == ("选 3 个", "选 3 个")


def test_brief_period_meta_counts_recommendations_only_when_there_are_some():
    base = {"articles_count": 33, "llm_model": "deepseek-chat"}
    assert BRIEF.period_summary({**base, "content_recommendations": [{"id": "a"}, {"id": "b"}]}) == (
        "收录 33 条", "收录 33 条 · 推荐 2 条 · deepseek-chat")
    # 旧的往期没有这个键;空表与不是表的值都不写「推荐 0 条」
    for value in ([], None, {"a": 1}, "x"):
        assert BRIEF.period_summary({**base, "content_recommendations": value})[1] == "收录 33 条 · deepseek-chat"


def test_form_rows_are_declared_by_the_flow_and_carry_the_saved_values(tmp_path):
    """抽屉里「配置」有哪些行由后端按流程声明;每一行带着当前取值,前端不另取一次配置。"""
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        BRIEF.update(session, config={"top_n": 9, "min_score": 6.5, "source_ids": ["rss_a", "rss_b"]})
        PICKS.update(session, config={"pick_count": 4, "ai_only": False})

        form = gf.describe(session, BRIEF, today=TODAY)["form"]
        assert [(row["type"], row["label"]) for row in form] == [
            ("source_scope", "取材"), ("cron", "定时"), ("number", "每期条数"), ("number", "入选门槛"),
            ("number", "正文保底条数"), ("number", "论文门槛"), ("cursor", "增量游标"),
        ]
        by_field = {row["field"]: row for row in form if row.get("field")}
        assert by_field["source_ids"]["value"] == ["rss_a", "rss_b"]
        assert (by_field["top_n"]["value"], by_field["min_score"]["value"]) == (9, 6.5)
        assert (by_field["top_n"]["min"], by_field["top_n"]["max"], by_field["min_score"]["step"]) == (
            daily_brief.TOP_N_MIN, daily_brief.TOP_N_MAX, 0.5)
        assert "cursor" in by_field and "确认重置" in by_field["cursor"]["reset_confirm"]
        assert "field" not in form[1]                       # 定时那一行不是配置里的键,跟着 cron 走

        form = gf.describe(session, PICKS, today=TODAY)["form"]
        assert [(row["type"], row["label"]) for row in form] == [
            ("source_link", "取材"), ("cron", "定时"), ("number", "挑选个数"), ("toggle", "只看 AI 相关"),
        ]
        assert (form[0]["source_id"], form[0]["text"]) == (trending_picks.INPUT_SOURCE_ID, trending_picks.INPUT_SOURCE_NAME)
        assert "value" not in form[0]                       # 取材入口只是一个去源管理的链接,没有可改的值
        assert (form[2]["value"], form[3]["value"]) == (4, False)

        # 没存过的旋钮给缺省值,不给 None(前端的输入框直接拿去显示)
        fresh = _sink(tmp_path, "fresh.db")
        with Session(fresh.engine) as other:
            values = {row["field"]: row["value"] for row in gf.describe(other, PICKS, today=TODAY)["form"] if row.get("field")}
            assert values == {"pick_count": trending_picks.PICK_COUNT_DEFAULT, "ai_only": trending_picks.AI_ONLY_DEFAULT}


def test_notes_that_only_the_brief_has(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        brief, picks = gf.describe(session, BRIEF, today=TODAY), gf.describe(session, PICKS, today=TODAY)
    assert "同日同事件合并" in brief["scope_note"] and "增量游标会自动回退" in brief["delete_note"]
    assert (picks["scope_note"], picks["delete_note"], picks["notices"]) == ("", "", [])
    assert brief["notices"] == []                           # 从没跑过:没有评分来源可说


@pytest.mark.parametrize("last_run, expected", [
    ({"status": "success", "scored_stored": 30, "scored_inline": 3}, None),                      # 补评是少数:正常
    ({"status": "success", "scored_stored": 0, "scored_inline": 0}, None),                       # 没有候选
    ({"status": "failed", "scored_stored": 0, "scored_inline": 12}, None),                       # 失败的那次不拿来判断
    ({"status": "empty", "scored_inline": 12}, None),
    ({"status": "success", "scored_stored": 0, "scored_inline": 12},
     "最近一次 12 篇候选全部由日报就地补评（复用分析 0 篇）：入库分析总闸可能未开启，或 worker 尚未追平"),
    ({"status": "success", "scored_stored": 5, "scored_inline": 5}, "最近一次补评占比 50%（复用 5 / 补评 5）"),
    ({"status": "success", "scored_stored": 1, "scored_inline": 2, "scored_inline_pending": 2},
     "最近一次补评占比 67%（复用 1 / 补评 2）；其中 2 篇当时仍在分析队列，建议把 cron 排在 worker 追平之后"),
    ({"status": "success", "scored_stored": "x", "scored_inline": None}, None),                  # 脏值按 0
])
def test_brief_notice_only_when_inline_scoring_dominates(tmp_path, last_run, expected):
    """日报对入库分析是软依赖:补评占到一半以上才提示(原来在前端算,搬到后端后逐字不变)。"""
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        daily_brief.set_json_setting(session, daily_brief.KEY_LAST_RUN, last_run)
        assert gf.describe(session, BRIEF, today=TODAY)["notices"] == ([expected] if expected else [])


def test_last_run_is_normalised_and_a_skip_reason_surfaces_in_the_input_cell(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        daily_brief.set_json_setting(session, daily_brief.KEY_LAST_RUN, {
            "status": "empty", "ended_at": "2026-11-27T08:30:09", "report_date": "2026-11-27",
        })
        row = gf.describe(session, BRIEF, today=TODAY)
        assert row["last_run"] == {"status": "skipped", "reason": "暂无新增内容", "ended_at": "2026-11-27T08:30:09", "report_date": "2026-11-27"}
        assert row["input"]["warn"] == "暂无新增内容"

        daily_brief.set_json_setting(session, daily_brief.KEY_LAST_RUN, {"status": "failed", "error_message": "LLM 请求超时"})
        row = gf.describe(session, BRIEF, today=TODAY)
        assert (row["last_run"]["status"], row["last_run"]["reason"], row["input"]["warn"]) == ("failed", "LLM 请求超时", "")

        daily_brief.set_json_setting(session, daily_brief.KEY_LAST_RUN, {"status": "success", "report_date": "2026-11-27"})
        assert gf.describe(session, BRIEF, today=TODAY)["last_run"]["status"] == "success"

        daily_brief.set_json_setting(session, trending_picks.KEY_LAST_RUN, {"status": "skipped", "reason": "没有新榜单"})
        row = gf.describe(session, PICKS, today=TODAY)
        assert (row["last_run"]["status"], row["input"]["warn"]) == ("skipped", "没有新榜单")
        daily_brief.set_json_setting(session, trending_picks.KEY_LAST_RUN, {"status": "failed", "error_message": "上游 502"})
        assert gf.describe(session, PICKS, today=TODAY)["last_run"] == {"status": "failed", "reason": "上游 502", "ended_at": "", "report_date": ""}


def test_input_summary_follows_the_source_scope_and_the_latest_ranking(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        daily_brief.write_source_scope(session, ["rss_a", "rss_b"])
        assert gf.describe(session, BRIEF, today=TODAY)["input"] == {"label": "自定名单", "sub": "2 个来源", "warn": ""}
        session.add(ArticleRecord(
            id="github_trending_daily_20261126", title="日榜", content_type="github_trending",
            source_id="github_trending_daily", source_url="", publish_date="2026-11-26T23:10:00+00:00",
            fetched_date="2026-11-27T07:10:05", has_content=True, content="x", extensions_json="{}",
        ))
        session.commit()
        assert gf.describe(session, PICKS, today=TODAY)["input"]["sub"] == "11-27 07:10"


def test_progress_is_normalised_to_a_phase_index(monkeypatch):
    daily_brief.set_progress("scoring", "评分 12/33（已有分析 5 篇）", done=12, total=33)
    assert BRIEF.progress() == {"state": "running", "phase_index": 1, "message": "评分 12/33（已有分析 5 篇）", "done": 12, "total": 33}
    for phase, index in (("collecting", 0), ("selecting", 2), ("editing", 3), ("reducing", 4), ("persisting", 4)):
        daily_brief.set_progress(phase)
        assert (BRIEF.progress()["state"], BRIEF.progress()["phase_index"]) == ("running", index)
    for phase, state in (("done", "done"), ("empty", "skipped"), ("error", "error"), ("idle", "idle")):
        daily_brief.set_progress(phase)
        assert (BRIEF.progress()["state"], BRIEF.progress()["phase_index"]) == (state, None)

    trending_picks.set_progress("picking", "从 25 个仓库里挑 6 个")
    assert PICKS.progress() == {"state": "running", "phase_index": 1, "message": "从 25 个仓库里挑 6 个", "done": 0, "total": 0}
    for phase, state in (("done", "done"), ("skipped", "skipped"), ("error", "error"), ("idle", "idle")):
        trending_picks.set_progress(phase)
        assert PICKS.progress()["state"] == state
    daily_brief.set_progress("idle")


def test_periods_are_paged_newest_first(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        for day in range(1, 26):
            _period(session, BRIEF, f"2026-11-{day:02d}", extensions={"articles_count": day})
        _period(session, PICKS, "2026-11-25", extensions={"picks_count": 6})
        session.commit()
        first = gf.list_periods(session, BRIEF, page=1, page_size=10)
        assert (first["total"], first["page"], first["page_size"]) == (25, 1, 10)
        assert [item["date"] for item in first["items"]][:3] == ["2026-11-25", "2026-11-24", "2026-11-23"]
        last = gf.list_periods(session, BRIEF, page=3, page_size=10)
        assert [item["date"] for item in last["items"]] == [f"2026-11-0{d}" for d in (5, 4, 3, 2, 1)]
        assert gf.list_periods(session, BRIEF, page=9, page_size=10)["items"] == []
        assert gf.list_periods(session, BRIEF, page=0, page_size=999)["page_size"] == gf.PERIOD_PAGE_SIZE_MAX
        assert gf.list_periods(session, PICKS)["total"] == 1


# ── 公共日报经外壳读写的就是原来的键 ───────────────────────────────────────────


def test_daily_brief_update_writes_the_same_settings_as_its_own_endpoint(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        BRIEF.update(session, enabled=True, cron=" 15 7 * * * ", config={
            "top_n": 20, "min_score": 6.5, "min_items": 5, "paper_min_content_value": 8, "source_ids": ["rss_a"],
        })
        assert daily_brief.daily_brief_enabled(session) is True
        assert daily_brief.daily_brief_cron(session) == "15 7 * * *"
        assert (daily_brief.daily_brief_top_n(session), daily_brief.daily_brief_min_score(session)) == (20, 6.5)
        assert (daily_brief.daily_brief_min_items(session), daily_brief.daily_brief_paper_min_content_value(session)) == (5, 8.0)
        assert daily_brief.read_source_scope(session) == ["rss_a"]
        assert BRIEF.read_config(session) == {
            "top_n": 20, "min_score": 6.5, "min_items": 5, "paper_min_content_value": 8.0,
            "source_ids": ["rss_a"], "cursor": "",
        }
        # 只动给了的键;空名单 = 回到全部来源;游标可以重置成空串
        BRIEF.update(session, config={"source_ids": [], "cursor": ""})
        assert daily_brief.read_source_scope(session) is None and daily_brief.daily_brief_top_n(session) == 20
        BRIEF.update(session, enabled=False)
        assert daily_brief.daily_brief_enabled(session) is False and daily_brief.daily_brief_cron(session) == "15 7 * * *"


@pytest.mark.parametrize("kwargs, message", [
    ({"cron": "bad cron"}, "cron 表达式必须是 5 段"),
    ({"config": {"top_n": 999}}, "精选条数需在"),
    ({"config": {"top_n": 2.5}}, "精选条数必须是整数"),
    ({"config": {"top_n": "12"}}, "精选条数必须是整数"),
    ({"config": {"min_score": 11}}, "入选门槛需在 0–10 之间"),
    ({"config": {"min_score": True}}, "入选门槛必须是数字"),
    ({"config": {"min_items": -1}}, "正文保底条数需在"),
    ({"config": {"paper_min_content_value": 10.5}}, "论文门槛需在 0–10 之间"),
    ({"config": {"source_ids": "rss_a"}}, "来源名单必须是"),
    ({"config": {"source_ids": [1]}}, "来源名单必须是"),
    ({"config": {"cursor": 5}}, "增量游标必须是字符串"),
    ({"config": {"pick_count": 3}}, "未知的配置项"),
    ({"config": [1]}, "配置必须是对象"),
])
def test_daily_brief_update_rejects_bad_input_without_writing(tmp_path, kwargs, message):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        before = BRIEF.read_config(session)
        with pytest.raises(ValueError, match=message):
            BRIEF.update(session, enabled=True, **kwargs)
        # 先全部校验再写:一个键不合法,别的键(这里是开关)也不落
        assert BRIEF.read_config(session) == before and daily_brief.daily_brief_enabled(session) is False


def test_trending_picks_update_validates_everything_before_writing(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        PICKS.update(session, enabled=True, cron="0 10 * * *", config={"pick_count": 4})
        assert (PICKS.is_enabled(session), PICKS.cron(session), PICKS.read_config(session)) == (
            True, "0 10 * * *", {"pick_count": 4, "ai_only": True},
        )
        for kwargs, message in [
            ({"cron": "nope"}, "cron 表达式必须是 5 段"),
            ({"config": {"pick_count": 0}}, "挑选个数需在"),
            ({"config": {"top_n": 1}}, "未知的配置项"),
        ]:
            with pytest.raises(ValueError, match=message):
                PICKS.update(session, enabled=False, **kwargs)
            assert PICKS.is_enabled(session) is True            # 开关没有被写成关
        PICKS.update(session, enabled=False)
        assert (PICKS.is_enabled(session), PICKS.cron(session)) == (False, "0 10 * * *")


def test_a_failed_manual_brief_run_through_the_shell_is_recorded(tmp_path, monkeypatch):
    import asyncio

    sink = _sink(tmp_path)

    async def boom(**kwargs):
        raise RuntimeError("LLM 请求超时")

    monkeypatch.setattr(daily_brief, "generate_daily_brief", boom)
    with pytest.raises(RuntimeError, match="LLM 请求超时"):
        asyncio.run(BRIEF.generate(storage=sink, trigger="manual", triggered_by="admin"))
    with Session(sink.engine) as session:
        last = BRIEF.last_run(session)
    assert (last["status"], last["reason"]) == ("failed", "LLM 请求超时")
    assert BRIEF.progress()["state"] == "error"
    daily_brief.set_progress("idle")


# ── 接口 ──────────────────────────────────────────────────────────────────────


def _setup(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _sink(tmp_path, "api.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    seed_default_accounts(sink.engine)
    return app_module, sink


def _login(client, username="admin", password="admin"):
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200


def _poll_job(client, job_id, tries=200):
    for _ in range(tries):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed"):
            return body
    raise AssertionError("任务未到达终态")


def test_list_endpoint_is_admin_only_and_returns_every_flow(monkeypatch, tmp_path):
    app_module, _sink_ = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert client.get("/api/generation-flows").status_code in (401, 403)
        _login(client, "user", "user")
        assert client.get("/api/generation-flows").status_code == 403
        assert client.post("/api/generation-flows/trending_picks/config", json={"enabled": True}).status_code == 403
        assert client.post("/api/generation-flows/trending_picks/generate").status_code == 403
    with TestClient(app_module.app) as client:
        _login(client)
        body = client.get("/api/generation-flows").json()
        assert [flow["id"] for flow in body["flows"]] == ["daily_brief", "trending_picks"]
        picks = body["flows"][1]
        assert set(picks) >= {"name", "source_id", "phases", "input", "enabled", "cron", "next_run_at", "running",
                              "progress", "last_run", "latest", "recent_days", "total", "knobs", "config"}
        assert picks["enabled"] is False and len(picks["recent_days"]) == 7
        assert client.get("/api/generation-flows/nope/progress").status_code == 404
        assert client.post("/api/generation-flows/nope/config", json={}).status_code == 404
        assert client.post("/api/generation-flows/nope/generate").status_code == 404
        assert client.get("/api/generation-flows/nope/periods").status_code == 404


def test_config_endpoint_updates_validates_and_reschedules(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    reloaded = []
    monkeypatch.setattr(app_module, "reload_generation_flow_schedule", lambda flow_id=None: reloaded.append(flow_id))
    with TestClient(app_module.app) as client:
        _login(client)
        ok = client.post("/api/generation-flows/trending_picks/config",
                         json={"enabled": True, "cron": "0 10 * * *", "config": {"pick_count": 4, "ai_only": False}})
        assert ok.status_code == 200
        body = ok.json()
        assert (body["id"], body["enabled"], body["cron"], body["config"]) == (
            "trending_picks", True, "0 10 * * *", {"pick_count": 4, "ai_only": False},
        )
        assert [knob["value"] for knob in body["knobs"]] == [4, False]
        bad = client.post("/api/generation-flows/trending_picks/config", json={"config": {"pick_count": 99}})
        assert bad.status_code == 400 and "挑选个数" in bad.json()["detail"]
        assert client.post("/api/generation-flows/trending_picks/config", json={"cron": "x"}).status_code == 400

        brief = client.post("/api/generation-flows/daily_brief/config", json={"config": {"top_n": 15}})
        assert brief.status_code == 200 and brief.json()["config"]["top_n"] == 15
        # 日报自己的接口读到的是同一份配置
        assert client.get("/api/daily-brief/config").json()["top_n"] == 15
        assert client.post("/api/generation-flows/daily_brief/config", json={"config": {"top_n": 0}}).status_code == 400
    # 只有成功的更新才重排调度,带的是流程 id(开头那个 None 是应用启动时的全量登记)
    assert reloaded == [None, "trending_picks", "daily_brief"]


def test_generate_endpoint_submits_a_job_attributed_to_the_admin(monkeypatch, tmp_path):
    app_module, _sink_ = _setup(monkeypatch, tmp_path)
    captured = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        return {"status": "skipped", "reason": "没有新榜单"}

    monkeypatch.setattr(gf, "get_flow", lambda flow_id: replace(PICKS, generate=fake_generate) if flow_id == PICKS.id else None)
    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/generation-flows/trending_picks/generate")
        assert resp.status_code == 200 and resp.json()["status"] == "accepted"
        done = _poll_job(client, resp.json()["job_id"])
        assert done["status"] == "succeeded" and done["type"] == "trending_picks"
        assert done["result"] == {"status": "skipped", "reason": "没有新榜单"}
    assert captured["trigger"] == "manual" and captured["triggered_by"] == "admin" and captured["storage"] is not None


def test_generate_endpoint_reports_a_failed_job(monkeypatch, tmp_path):
    app_module, _sink_ = _setup(monkeypatch, tmp_path)

    async def boom(**kwargs):
        raise RuntimeError("上游 502")

    monkeypatch.setattr(gf, "get_flow", lambda flow_id: replace(PICKS, generate=boom))
    with TestClient(app_module.app) as client:
        _login(client)
        done = _poll_job(client, client.post("/api/generation-flows/trending_picks/generate").json()["job_id"])
        assert done["status"] == "failed" and "上游 502" in (done["error"] or "")


def test_progress_and_periods_endpoints(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        for day in range(1, 13):
            _period(session, PICKS, f"2026-11-{day:02d}", extensions={"picks_count": 6, "repo_count": 25})
        session.commit()
    trending_picks.set_progress("reading", "读取最新一期日榜")
    with TestClient(app_module.app) as client:
        _login(client)
        assert client.get("/api/generation-flows/trending_picks/progress").json() == {
            "state": "running", "phase_index": 0, "message": "读取最新一期日榜", "done": 0, "total": 0,
        }
        page = client.get("/api/generation-flows/trending_picks/periods", params={"page": 2, "page_size": 10}).json()
        assert (page["total"], page["page"]) == (12, 2)
        assert [item["date"] for item in page["items"]] == ["2026-11-02", "2026-11-01"]
        assert page["items"][0]["meta"] == "选 6 个 · 榜单 25 个"
        assert client.get("/api/generation-flows/trending_picks/periods", params={"page_size": 999}).status_code == 422
        assert client.get("/api/generation-flows/daily_brief/periods").json() == {"total": 0, "page": 1, "page_size": 10, "items": []}
    trending_picks.set_progress("idle")


def test_writes_to_the_flows_api_are_audited_and_gated():
    import api.app as app_module
    from services import admin_audit

    assert admin_audit.should_audit("/api/generation-flows/trending_picks/config", "POST")
    assert admin_audit.should_audit("/api/generation-flows/trending_picks/generate", "POST")
    assert not admin_audit.should_audit("/api/generation-flows", "GET")
    assert "/api/generation-flows" in app_module.COLLECTOR_API_PREFIXES
    # 日报自己的接口原样保留
    assert "/api/daily-brief" in app_module.COLLECTOR_API_PREFIXES


# ── 调度 ──────────────────────────────────────────────────────────────────────


def test_schedule_follows_the_switch_and_the_cron(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    scheduler = app_module.scheduler
    for job_id in ("trending_picks", "daily_brief"):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    try:
        app_module.reload_generation_flow_schedule()
        assert scheduler.get_job("trending_picks") is None              # 缺省关:不登记

        with Session(sink.engine) as session:
            PICKS.update(session, enabled=True, cron="0 10 * * *")
        app_module.reload_generation_flow_schedule("trending_picks")
        job = scheduler.get_job("trending_picks")
        assert job is not None and list(job.args) == ["trending_picks"]
        assert job.misfire_grace_time == app_module.CRON_MISFIRE_GRACE_SECONDS and job.coalesce is True
        assert "hour='10'" in str(job.trigger) and "minute='0'" in str(job.trigger)

        with Session(sink.engine) as session:
            daily_brief.set_setting(session, trending_picks.KEY_CRON, "61 25 * * *")     # 字段越界:登记不上就摘掉旧的
        app_module.reload_generation_flow_schedule()
        assert scheduler.get_job("trending_picks") is None

        with Session(sink.engine) as session:
            PICKS.update(session, cron="0 10 * * *")
        app_module.reload_generation_flow_schedule()
        assert scheduler.get_job("trending_picks") is not None
        with Session(sink.engine) as session:
            PICKS.update(session, enabled=False)
        app_module.reload_generation_flow_schedule("trending_picks")
        assert scheduler.get_job("trending_picks") is None

        # 日报:转给它自己的函数,job id 仍是 daily_brief;不带 id 的全量重排不碰它
        with Session(sink.engine) as session:
            BRIEF.update(session, enabled=True)
        app_module.reload_generation_flow_schedule()
        assert scheduler.get_job("daily_brief") is None
        app_module.reload_generation_flow_schedule("daily_brief")
        assert scheduler.get_job("daily_brief") is not None and list(scheduler.get_job("daily_brief").args) == []
    finally:
        for job_id in ("trending_picks", "daily_brief"):
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)


def test_scheduled_callback_runs_the_flow_and_swallows_its_failure(monkeypatch, tmp_path):
    import asyncio

    app_module, sink = _setup(monkeypatch, tmp_path)
    seen = []

    async def fake_generate(**kwargs):
        seen.append(kwargs)
        if len(seen) == 2:
            raise RuntimeError("上游 502")
        return {"status": "success", "article_id": "trending_picks_2026-11-27"}

    monkeypatch.setattr(gf, "get_flow", lambda flow_id: replace(PICKS, generate=fake_generate) if flow_id == PICKS.id else None)
    asyncio.run(app_module.execute_generation_flow_job("trending_picks"))
    asyncio.run(app_module.execute_generation_flow_job("trending_picks"))        # 抛错不外溢:调度引擎不受影响
    asyncio.run(app_module.execute_generation_flow_job("gone"))                  # 登记表里没有的 id:什么都不做
    assert [(call["trigger"], call["triggered_by"]) for call in seen] == [("scheduled", None)] * 2
    assert seen[0]["storage"] is sink


# ── 相邻的两处:个人早报不等它,知识台账能排除它 ─────────────────────────────────


def test_personal_digest_does_not_wait_for_a_system_source_other_than_the_brief(tmp_path):
    from services.personal_digest import calculate_due_source_ids

    sink = _sink(tmp_path)
    now = "2026-11-27T00:00:00"
    with Session(sink.engine) as session:
        for source_id in (PICKS.source_id, "rss_synced"):
            session.add(SourceConfigRecord(
                source_id=source_id, name=source_id, source_type="preset", origin="catalog", shape="article",
                collection_authority_id="authority-1", is_active=True, created_at=now, updated_at=now,
            ))
        session.commit()
        due = calculate_due_source_ids(
            session, [PICKS.source_id, "rss_synced"],
            as_of=dt.datetime(2026, 11, 27, 8, 0, tzinfo=dt.timezone.utc), scheduled_source_ids=[PICKS.source_id],
        )
    # 带同步印记的普通公共源照旧要等;生成流程的产出没有源状态可等,哪怕带着印记、哪怕被点了名
    assert due == ["rss_synced"]


def test_articles_api_can_exclude_every_system_source(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        for article_id, source_id in (("a1", "rss_openai_news"), ("b1", BRIEF.source_id), ("p1", PICKS.source_id)):
            session.add(ArticleRecord(
                id=article_id, title=article_id, content_type="rss_article", source_id=source_id, source_url="",
                publish_date="2026-11-27", fetched_date="2026-11-27T08:00:00", has_content=True, content="x",
                extensions_json="{}",
            ))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        ids = lambda params: sorted(item["id"] for item in client.get("/api/articles", params=params).json())  # noqa: E731
        assert ids({}) == ["a1", "b1", "p1"]
        assert ids({"exclude_system_sources": "true"}) == ["a1"]
        assert ids({"exclude_source_ids": "rss_openai_news", "exclude_system_sources": "true"}) == []
        # 旧写法(只按 id 排除日报)照旧可用
        assert ids({"exclude_source_ids": "dorami_daily_brief"}) == ["a1", "p1"]
        facets = client.get("/api/articles/facets", params={"exclude_system_sources": "true"}).json()
        assert json.dumps(facets, ensure_ascii=False).count("dorami_") == 0
        assert "dorami_trending_picks" in json.dumps(client.get("/api/articles/facets").json())
