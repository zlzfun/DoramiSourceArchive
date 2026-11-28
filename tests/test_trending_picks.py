"""生成流程「GitHub Trending 精读」(issue #72):取材、挑选、落库、跳过与失败。

模型调用一律打桩(monkeypatch 本模块的 ``chat_completion``),不出网。守的事:

- 取材认的是「最新一条日榜有没有被写过」,不按日期对(日榜的 id 与日期按 UTC 记);
- 模型只给结构化条目,落库的正文由代码渲染;榜单里没有的仓库、缺说明的、重复的都不收;
- 跳过不是失败:没有榜单、没有新榜单、只看 AI 时榜单里没有 AI 仓库;
- 失败不落库,原因记进上次结果;
- 产出不回流:源行建出来就不送分析,条目进不了公共日报候选。
"""

import asyncio
import json
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import LLMConfig  # noqa: E402
from llm.client import LLMError, LLMNotConfigured  # noqa: E402
from models.db import ArticleRecord, SourceConfigRecord  # noqa: E402
from services import trending_picks as tp  # noqa: E402

CONFIGURED = LLMConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test-model")
UNCONFIGURED = LLMConfig(base_url="", api_key="", model="")

ITEMS = [
    {"rank": 1, "repo": "acme/agent-kit", "url": "https://github.com/acme/agent-kit",
     "description": "Build agents fast", "language": "Python", "stars": 12345, "stars_today": "678 stars today"},
    {"rank": 2, "repo": "acme/tiny-llm", "url": "https://github.com/acme/tiny-llm",
     "description": "A small language model", "language": "Rust", "stars": 900, "stars_today": "120 stars today"},
    {"rank": 3, "repo": "tools/fastgrep", "url": "https://github.com/tools/fastgrep",
     "description": "grep, but faster", "language": "Go", "stars": 4000, "stars_today": "55 stars today"},
    {"rank": 4, "repo": "tools/dotfiles", "url": "https://github.com/tools/dotfiles",
     "description": "", "language": "", "stars": 10, "stars_today": ""},
]
GOOD = {"picks": [
    {"repo": "acme/agent-kit", "group": "ai", "summary": "搭智能体的工具包", "why": "今天涨得最快"},
    {"repo": "tools/fastgrep", "group": "tool", "summary": "更快的 grep", "why": "命令行常用"},
]}


def _sink(tmp_path, name="picks.db"):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _ranking(engine, day="2026-11-26", *, items=ITEMS, fetched=None, extensions=None):
    """一条日榜记录,形状与 ``GitHubTrendingDailyFetcher`` 写的一致(id / 日期按 UTC 的那一天)。"""
    article_id = f"github_trending_daily_{day.replace('-', '')}"
    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=f"GitHub Trending 日榜 · {day}", content_type="github_trending",
            source_id="github_trending_daily", source_url="https://github.com/trending?since=daily",
            publish_date=f"{day}T23:10:00+00:00", fetched_date=fetched or f"{day}T23:10:05",
            has_content=True, content="榜单正文",
            extensions_json=json.dumps(
                extensions if extensions is not None else {"items_json": json.dumps(items), "repo_count": len(items)},
                ensure_ascii=False,
            ),
        ))
        session.commit()
    return article_id


@pytest.fixture
def llm(monkeypatch):
    """打桩模型调用:``llm.reply`` 是下一次要回的东西(dict → JSON;str 原样;异常就抛),``llm.calls`` 记每次的参数。"""

    class Stub:
        reply = GOOD
        calls = []

    stub = Stub()
    stub.calls = []

    async def fake_chat_completion(**kwargs):
        stub.calls.append(kwargs)
        if isinstance(stub.reply, Exception):
            raise stub.reply
        return stub.reply if isinstance(stub.reply, str) else json.dumps(stub.reply, ensure_ascii=False)

    monkeypatch.setattr(tp, "chat_completion", fake_chat_completion)
    return stub


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(tp, "_today", lambda: "2026-11-27")
    tp.set_progress("idle")


def _run(sink, **kwargs):
    kwargs.setdefault("llm_config", CONFIGURED)
    kwargs.setdefault("trigger", "scheduled")
    return asyncio.run(tp.generate(storage=sink, **kwargs))


def _periods(engine):
    with Session(engine) as session:
        return session.exec(
            select(ArticleRecord).where(ArticleRecord.source_id == tp.SOURCE_ID).order_by(ArticleRecord.publish_date)
        ).all()


def _last_run(engine):
    with Session(engine) as session:
        return tp.last_run(session)


# ── 正常一期 ──────────────────────────────────────────────────────────────────


def test_a_period_is_written_from_the_latest_ranking(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine, "2026-11-25")
    ranking_id = _ranking(sink.engine, "2026-11-26")           # 北京时间 11-27 早上采到的是这一条(UTC 还在 26 日)

    result = _run(sink, trigger="manual", triggered_by="admin")

    assert result == {
        "status": "success", "report_date": "2026-11-27", "article_id": "trending_picks_2026-11-27",
        "picks_count": 1, "repo_count": 4, "trigger": "manual",      # 缺省「只看 AI 相关」:模型标成工具的那一个不收
    }
    [period] = _periods(sink.engine)
    assert (period.id, period.source_id, period.content_type) == ("trending_picks_2026-11-27", tp.SOURCE_ID, "trending_picks")
    assert (period.title, period.publish_date, period.has_content) == ("GitHub Trending 精读 · 2026-11-27", "2026-11-27", True)
    extensions = json.loads(period.extensions_json)
    assert extensions["trending_article_id"] == ranking_id          # 用的是最新那条,不是「日期等于今天」的那条
    assert (extensions["picks_count"], extensions["repo_count"], extensions["report_date"]) == (1, 4, "2026-11-27")
    assert [item["repo"] for item in extensions["items"]] == ["acme/agent-kit"]
    assert "acme/agent-kit" in period.content and "### 榜单其余" in period.content
    first = extensions["items"][0]
    assert (first["group"], first["summary"], first["why"], first["stars"], first["language"]) == (
        "ai", "搭智能体的工具包", "今天涨得最快", 12345, "Python",
    )
    assert extensions["llm_model"] == CONFIGURED.for_aux().model and extensions["generated_at"]

    run = _last_run(sink.engine)
    assert (run["status"], run["article_id"], run["picks_count"], run["error_message"]) == (
        "success", "trending_picks_2026-11-27", 1, None,
    )
    assert tp.get_progress()["phase"] == "done"

    # 一次辅助档调用;用量记在 trending_picks 上,手动归触发的管理员
    [call] = llm.calls
    assert call["usage_meta"].purpose == "trending_picks" and call["usage_meta"].username == "admin"
    assert call["config"] == CONFIGURED.for_aux() and call["response_json"] is True
    board = json.loads(call["messages"][1].content)
    assert [row["repo"] for row in board] == [item["repo"] for item in ITEMS]
    assert set(board[0]) == {"rank", "repo", "description", "language", "stars", "stars_today"}
    assert "最多挑 6 个" in call["messages"][0].content and "只挑与 AI 相关的仓库" in call["messages"][0].content


def test_scheduled_usage_is_attributed_to_system(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    _run(sink)
    assert llm.calls[0]["usage_meta"].username == "system"


def test_the_body_is_rendered_by_code_from_the_items():
    picks = tp.validate_picks(GOOD, ITEMS, pick_count=6, ai_only=False)
    body = tp.render_markdown(picks, ITEMS, ranking_title="GitHub Trending 日榜 · 2026-11-26")
    assert body == "\n\n".join([
        "> 选自 GitHub Trending 日榜 · 2026-11-26(共 4 个仓库)",
        "### AI 相关",
        "**[acme/agent-kit](https://github.com/acme/agent-kit)** — ⭐ 12,345(+678 today) · Python\n\n搭智能体的工具包\n\n**为什么值得看**:今天涨得最快",
        "### 开发工具与项目",
        "**[tools/fastgrep](https://github.com/tools/fastgrep)** — ⭐ 4,000(+55 today) · Go\n\n更快的 grep\n\n**为什么值得看**:命令行常用",
        "### 榜单其余",
        "[acme/tiny-llm](https://github.com/acme/tiny-llm) · [tools/dotfiles](https://github.com/tools/dotfiles)",
    ])


# ── 模型的回答对到榜单上 ───────────────────────────────────────────────────────


def test_picks_outside_the_ranking_or_without_a_summary_are_dropped():
    payload = {"picks": [
        {"repo": "ACME/Agent-Kit", "group": "AI", "summary": "  大小写不同也认  ", "why": ""},
        {"repo": "ghost/not-on-the-board", "group": "ai", "summary": "编出来的", "why": "x"},
        {"repo": "acme/agent-kit", "group": "ai", "summary": "重复的一条", "why": "x"},
        {"repo": "acme/tiny-llm", "group": "ai", "summary": "", "why": "没有说明"},
        {"repo": "tools/fastgrep", "group": "whatever", "summary": "组名不认识就算工具", "why": "x" * 500},
        "不是对象",
    ]}
    picks = tp.validate_picks(payload, ITEMS, pick_count=6, ai_only=False)
    assert [(pick["repo"], pick["group"], pick["summary"]) for pick in picks] == [
        ("acme/agent-kit", "ai", "大小写不同也认"),
        ("tools/fastgrep", "tool", "组名不认识就算工具"),
    ]
    assert len(picks[1]["why"]) == 160                      # 跑飞的输出截断


@pytest.mark.parametrize("payload, message", [
    ({}, "没有 picks 列表"),
    ([], "没有 picks 列表"),
    ({"picks": "nope"}, "没有 picks 列表"),
    ({"picks": None}, "没有 picks 列表"),
    ({"picks": [{"repo": "ghost/a", "group": "ai", "summary": "榜外"}, {"repo": "ghost/b", "summary": "榜外"}]}, "都不在榜单里"),
    ({"picks": [{"repo": "acme/agent-kit", "group": "ai", "summary": ""}, {"repo": "acme/tiny-llm", "group": "ai"}]}, "都不在榜单里"),
    ({"picks": ["不是对象", 3]}, "都不在榜单里"),
])
@pytest.mark.parametrize("ai_only", [True, False])
def test_an_answer_that_is_not_in_shape_is_an_error_not_an_empty_pick(payload, message, ai_only):
    """答得不成样子是模型出错:不能当成「挑不出」——否则「只看 AI」时会被记成「榜单里没有 AI 相关的仓库」。"""
    with pytest.raises(LLMError, match=message):
        tp.validate_picks(payload, ITEMS, pick_count=6, ai_only=ai_only)


def test_an_empty_answer_in_shape_is_just_empty():
    assert tp.validate_picks({"picks": []}, ITEMS, pick_count=6, ai_only=True) == []
    assert tp.validate_picks({"picks": []}, ITEMS, pick_count=6, ai_only=False) == []
    # 对得上榜单、也写了说明,只是都不是 AI 一组:「只看 AI」时是合规的空,不是错
    tools = {"picks": [{"repo": "tools/fastgrep", "group": "tool", "summary": "工具"}, {"repo": "ghost/x", "summary": "榜外"}]}
    assert tp.validate_picks(tools, ITEMS, pick_count=6, ai_only=True) == []


def test_pick_count_caps_and_ai_only_keeps_the_ai_group():
    payload = {"picks": [
        {"repo": "tools/fastgrep", "group": "tool", "summary": "工具"},
        {"repo": "acme/agent-kit", "group": "ai", "summary": "智能体"},
        {"repo": "acme/tiny-llm", "group": "ai", "summary": "小模型"},
    ]}
    assert [p["repo"] for p in tp.validate_picks(payload, ITEMS, pick_count=2, ai_only=False)] == ["tools/fastgrep", "acme/agent-kit"]
    assert [p["repo"] for p in tp.validate_picks(payload, ITEMS, pick_count=6, ai_only=True)] == ["acme/agent-kit", "acme/tiny-llm"]


def test_knobs_reach_the_prompt(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    with Session(sink.engine) as session:
        tp.write_config(session, {"pick_count": 3, "ai_only": False})
    _run(sink)
    system = llm.calls[0]["messages"][0].content
    assert "最多挑 3 个" in system and "只挑与 AI 相关的仓库" not in system and "其余有分量的开发工具" in system


# ── 跳过:不是失败 ─────────────────────────────────────────────────────────────


def test_no_ranking_at_all_is_a_skip(tmp_path, llm):
    sink = _sink(tmp_path)
    result = _run(sink)
    assert (result["status"], result["reason"]) == ("skipped", tp.SKIP_NO_TRENDING)
    assert llm.calls == [] and _periods(sink.engine) == []
    assert _last_run(sink.engine)["status"] == "skipped" and tp.get_progress()["phase"] == "skipped"


def test_the_same_ranking_is_not_written_twice_by_the_schedule(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    assert _run(sink)["status"] == "success"
    again = _run(sink)
    assert (again["status"], again["reason"]) == ("skipped", tp.SKIP_NO_NEW_TRENDING)
    assert len(llm.calls) == 1 and len(_periods(sink.engine)) == 1
    assert _last_run(sink.engine) | {"started_at": "", "ended_at": ""} == {
        "status": "skipped", "reason": "没有新榜单", "started_at": "", "ended_at": "",
        "report_date": "2026-11-27", "article_id": None, "error_message": None,
    }


def test_manual_rerun_rewrites_todays_period_but_not_an_older_one(tmp_path, llm, monkeypatch):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    assert _run(sink)["status"] == "success"

    # 今天这一期用的就是这条榜单:手动再点一次是重写(改了旋钮想重出一版),仍只有一期
    llm.reply = {"picks": [{"repo": "acme/tiny-llm", "group": "ai", "summary": "换了一个"}]}
    assert _run(sink, trigger="manual")["status"] == "success"
    [period] = _periods(sink.engine)
    assert [item["repo"] for item in json.loads(period.extensions_json)["items"]] == ["acme/tiny-llm"]

    # 到了第二天,榜单还是那一条:手动也不再写——否则同一条榜单会落成两期
    monkeypatch.setattr(tp, "_today", lambda: "2026-11-28")
    late = _run(sink, trigger="manual")
    assert (late["status"], late["reason"]) == ("skipped", tp.SKIP_NO_NEW_TRENDING)
    assert len(_periods(sink.engine)) == 1 and len(llm.calls) == 2

    # 新榜单到了:照常出新的一期
    _ranking(sink.engine, "2026-11-27")
    llm.reply = GOOD
    assert _run(sink)["article_id"] == "trending_picks_2026-11-28"
    assert [row.id for row in _periods(sink.engine)] == ["trending_picks_2026-11-27", "trending_picks_2026-11-28"]


def test_ai_only_with_no_ai_repo_is_a_skip_but_an_empty_answer_otherwise_fails(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    llm.reply = {"picks": []}
    result = _run(sink)
    assert (result["status"], result["reason"]) == ("skipped", tp.SKIP_NO_AI_REPOS)
    assert _periods(sink.engine) == []

    with Session(sink.engine) as session:
        tp.write_config(session, {"ai_only": False})
    with pytest.raises(LLMError, match="没有挑出任何仓库"):
        _run(sink)
    assert _periods(sink.engine) == [] and _last_run(sink.engine)["status"] == "failed"


@pytest.mark.parametrize("reply", [
    {},
    {"picks": "not a list"},
    {"picks": [{"repo": "ghost/not-on-the-board", "group": "ai", "summary": "编出来的"}]},
    {"picks": [{"repo": "acme/agent-kit", "group": "ai", "summary": ""}]},
])
def test_a_malformed_answer_fails_even_with_ai_only(tmp_path, llm, reply):
    """缺省「只看 AI」:模型答得不成样子记失败,不记成「榜单里没有 AI 相关的仓库」(检视 R1 第 4 条)。"""
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    llm.reply = reply
    with pytest.raises(LLMError):
        _run(sink)
    run = _last_run(sink.engine)
    assert run["status"] == "failed" and run["error_message"] and tp.SKIP_NO_AI_REPOS not in run["error_message"]
    assert _periods(sink.engine) == []


def test_ai_only_with_only_tool_picks_is_a_skip(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    llm.reply = {"picks": [{"repo": "tools/fastgrep", "group": "tool", "summary": "更快的 grep", "why": "x"}]}
    result = _run(sink)
    assert (result["status"], result["reason"]) == ("skipped", tp.SKIP_NO_AI_REPOS)


# ── 失败:不落库,原因记下来 ────────────────────────────────────────────────────


@pytest.mark.parametrize("reply, message", [
    ("", "模型返回为空"),
    ("   ", "模型返回为空"),
    (LLMError("上游 502"), "上游 502"),
    ("这不是 JSON", "未找到完整 JSON 对象"),
])
def test_a_failed_call_writes_nothing_and_records_why(tmp_path, llm, reply, message):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    llm.reply = reply
    with pytest.raises(LLMError, match=message):
        _run(sink)
    assert _periods(sink.engine) == []
    run = _last_run(sink.engine)
    assert run["status"] == "failed" and run["error_message"] and run["article_id"] is None
    assert tp.get_progress()["phase"] == "error"
    # 失败不占住锁:下一次照常能跑
    llm.reply = GOOD
    assert _run(sink)["status"] == "success"


def test_unconfigured_model_fails_before_any_call(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    with pytest.raises(LLMNotConfigured):
        _run(sink, llm_config=UNCONFIGURED)
    assert llm.calls == [] and _last_run(sink.engine)["status"] == "failed"


def test_no_new_ranking_is_reported_even_when_the_model_is_not_configured(tmp_path, llm):
    """跳过的判断在前:没有可取的素材时不必先配好模型。"""
    sink = _sink(tmp_path)
    assert _run(sink, llm_config=UNCONFIGURED)["status"] == "skipped"


@pytest.mark.parametrize("extensions", [{}, {"items_json": "不是 JSON"}, {"items_json": "[]"}, {"items_json": json.dumps([{"rank": 1}])}])
def test_a_ranking_without_usable_items_fails(tmp_path, llm, extensions):
    sink = _sink(tmp_path)
    _ranking(sink.engine, extensions=extensions)
    with pytest.raises(RuntimeError, match="没有可用的条目"):
        _run(sink)
    assert llm.calls == [] and _periods(sink.engine) == []


def test_only_one_run_at_a_time(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine)
    assert tp._LOCK.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="正在生成"):
            _run(sink)
    finally:
        tp._LOCK.release()
    assert _last_run(sink.engine) is None           # 没抢到锁的那一次不改上次结果


# ── 配置 ──────────────────────────────────────────────────────────────────────


def test_config_defaults_and_validation(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        assert tp.is_enabled(session) is False              # 新流程缺省关
        assert tp.cron(session) == "30 9 * * *"
        assert tp.read_config(session) == {"pick_count": 6, "ai_only": True}
        tp.write_config(session, {"pick_count": 10})
        assert tp.read_config(session) == {"pick_count": 10, "ai_only": True}
        tp.write_config(session, {"ai_only": False})
        assert tp.read_config(session) == {"pick_count": 10, "ai_only": False}
        for bad, message in [
            ({"pick_count": 0}, "1–10"), ({"pick_count": 11}, "1–10"), ({"pick_count": "6"}, "1–10"),
            ({"pick_count": True}, "1–10"), ({"pick_count": 2.5}, "1–10"),
            ({"ai_only": 1}, "开或关"), ({"top_n": 5}, "未知的配置项"),
        ]:
            with pytest.raises(ValueError, match=message):
                tp.write_config(session, bad)
        with pytest.raises(ValueError, match="必须是对象"):
            tp.validate_config([1])
        assert tp.read_config(session) == {"pick_count": 10, "ai_only": False}


def test_a_corrupt_stored_config_falls_back_to_defaults(tmp_path):
    from services import daily_brief as kv

    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        kv.set_setting(session, tp.KEY_CONFIG, json.dumps({"pick_count": "many", "ai_only": 0}))
        assert tp.read_config(session) == {"pick_count": 6, "ai_only": False}
        kv.set_setting(session, tp.KEY_CONFIG, json.dumps({"pick_count": 99}))
        assert tp.read_config(session)["pick_count"] == 10
        kv.set_setting(session, tp.KEY_CONFIG, "[1, 2]")
        assert tp.read_config(session) == {"pick_count": 6, "ai_only": True}


# ── 不回流 ────────────────────────────────────────────────────────────────────


def test_the_period_is_not_analysed_and_never_becomes_brief_material(tmp_path, llm):
    from services import article_analysis
    from services.daily_brief import collect_candidates

    sink = _sink(tmp_path)
    _ranking(sink.engine)
    _run(sink)
    with Session(sink.engine) as session:
        row = session.get(SourceConfigRecord, tp.SOURCE_ID)
        # 落库时同一事务里建了系统源那一行;目录条目 analysis = false,所以建出来就不送分析
        assert (row.origin, row.ai_analysis_enabled, row.name) == ("system", False, "GitHub Trending 精读")
        assert article_analysis.source_allows_analysis(session, tp.SOURCE_ID) is False
        candidates, _seen, _scanned = collect_candidates(session, cursor="")
        assert "trending_picks_2026-11-27" not in {candidate.id for candidate in candidates}
        assert tp.has_new_ranking(session) is False


def _content(body="正文", title="GitHub Trending 精读 · 2026-11-27"):
    from models.content import TrendingPicksContent

    content = TrendingPicksContent(
        id="trending_picks_2026-11-27", title=title, source_url="", publish_date="2026-11-27",
        content=body, has_content=True, report_date="2026-11-27", items=[], picks_count=0, repo_count=0,
        trending_article_id="github_trending_daily_20261126", llm_model="m", generated_at="2026-11-27T09:30:00",
    )
    content.source_id = tp.SOURCE_ID
    return content


def test_persisting_a_period_refuses_an_empty_body_and_overwrites_the_same_id(tmp_path, monkeypatch):
    from services import generated_content, sync_consumer_policy

    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        # 空正文不写:写进去读者看到的是「暂无正文」,而运行记录是成功
        for body in ("", "   \n"):
            with pytest.raises(RuntimeError, match="正文为空"):
                generated_content.persist_generated_article(session, _content(body))
        assert session.get(ArticleRecord, "trending_picks_2026-11-27") is None
        assert session.get(SourceConfigRecord, tp.SOURCE_ID) is None          # 连源行也没有顺手建出来

        generated_content.persist_generated_article(session, _content("第一版"))
        session.commit()
        generated_content.persist_generated_article(session, _content("第二版", title="改过的标题"))
        session.commit()
        row = session.get(ArticleRecord, "trending_picks_2026-11-27")
        assert (row.content, row.title, row.has_content, row.content_type) == ("第二版", "改过的标题", True, "trending_picks")
        assert len(_periods(sink.engine)) == 1

        # 节点规则不许本机写这个源时不写(与公共日报的落库同一道检查)
        seen = []
        monkeypatch.setattr(sync_consumer_policy, "local_source_operation_allowed",
                            lambda _session, source_id, *, operation: seen.append((source_id, operation)) or False)
        with pytest.raises(RuntimeError, match="不允许写入"):
            generated_content.persist_generated_article(session, _content("第三版"))
        assert seen == [(tp.SOURCE_ID, "collection")]
        session.rollback()
        assert session.get(ArticleRecord, "trending_picks_2026-11-27").content == "第二版"


def _upstream_period(engine, day, ranking_id, body="上游的正文"):
    """一条从上游同步来的精读:带着上游的版本印记(``analysis_authority_id``)。"""
    with Session(engine) as session:
        session.add(ArticleRecord(
            id=f"trending_picks_{day}", title=f"GitHub Trending 精读 · {day}", content_type="trending_picks",
            source_id=tp.SOURCE_ID, source_url="", publish_date=day, fetched_date=f"{day}T09:30:00",
            archive_updated_at=f"{day}T09:30:00", analysis_authority_id="upstream-node", has_content=True,
            content=body, extensions_json=json.dumps({"trending_article_id": ranking_id, "picks_count": 6, "repo_count": 25}),
        ))
        session.commit()


@pytest.mark.parametrize("trigger", ["scheduled", "manual"])
def test_a_period_synced_from_upstream_is_never_rewritten(tmp_path, llm, trigger):
    """接了上游的节点:今天这一期是同步来的,本机不重写(检视 R1 第 2 条)。

    同步来的行带上游印记,本机改它的正文时修订号不会推进——改动对同步流不可见,上游重放又会盖回去。
    """
    from services import generated_content

    sink = _sink(tmp_path)
    _ranking(sink.engine)
    _upstream_period(sink.engine, "2026-11-27", "github_trending_daily_20261126")

    result = _run(sink, trigger=trigger, triggered_by="admin")
    assert (result["status"], result["reason"]) == ("skipped", tp.SKIP_UPSTREAM_PERIOD)
    assert llm.calls == []                                                  # 没有白花一次模型调用
    with Session(sink.engine) as session:
        row = session.get(ArticleRecord, "trending_picks_2026-11-27")
        assert (row.content, row.archive_updated_at, row.analysis_authority_id) == (
            "上游的正文", "2026-11-27T09:30:00", "upstream-node")
        # 落库函数自己也拦(兜底:任何流程都不许盖掉上游的版本)
        with pytest.raises(RuntimeError, match="上游同步"):
            generated_content.persist_generated_article(session, _content("本机写的"))
        session.rollback()
        assert session.get(ArticleRecord, "trending_picks_2026-11-27").content == "上游的正文"


def test_an_upstream_period_of_another_day_does_not_block_today(tmp_path, llm):
    sink = _sink(tmp_path)
    _ranking(sink.engine, "2026-11-25")
    _upstream_period(sink.engine, "2026-11-26", "github_trending_daily_20261125")
    _ranking(sink.engine, "2026-11-26")                                     # 新的一条日榜,还没有被写过
    result = _run(sink)
    assert result["status"] == "success" and result["article_id"] == "trending_picks_2026-11-27"
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "trending_picks_2026-11-26").content == "上游的正文"
        assert not (session.get(ArticleRecord, "trending_picks_2026-11-27").analysis_authority_id or "")


def test_has_new_ranking(tmp_path, llm):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        assert tp.has_new_ranking(session) is False             # 一条日榜都没有
    _ranking(sink.engine)
    with Session(sink.engine) as session:
        assert tp.has_new_ranking(session) is True
