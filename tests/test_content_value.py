"""issue #178 第二波:内容价值(第二个分)、模型给的内容类型、主分规则与「补内容价值」回填。"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.articles_view import serialize_article_list_item  # noqa: E402
from config import LLMConfig  # noqa: E402
from llm import article_analysis_prompt as prompts  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisAttemptRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    SourceConfigRecord,
    TagRetagJobItemRecord,
    TaxonomyVersionRecord,
)
from services import analysis_backfill  # noqa: E402
from services import content_classification as cc  # noqa: E402
from services.article_analysis import (  # noqa: E402
    PENDING_SCOPE_CONTENT_VALUE,
    analysis_input_from_article,
    analyze_article_with_llm,
    claim_analysis_tasks,
    compute_content_hash,
    process_claimed_analysis,
    queue_article_analysis,
    queue_content_value_fill,
    validate_analysis_payload,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime(2026, 10, 12, 1, 0, tzinfo=dt.timezone.utc)
NOW_ISO = NOW.isoformat(timespec="seconds")
LLM_CONFIG = LLMConfig(base_url="https://llm.invalid/v1", api_key="test", model="fake")
# v6 原文(issue #178 第二波拍板:播客提示词在 #97 播客黄金集完成前逐字不动)。
V6_ARTICLE_SHA256 = "0a2b081c79520ed58c4b3533a835b448f1709f5b6277ca473ac6bc02911e29cc"
V6_PODCAST_SHOW_NOTES_SHA256 = "a94aa8b31c387110e740ebdb675189ae0f2fe673d9ba452b7a898ff2172ab4ad"
V6_PODCAST_TRANSCRIPT_SHA256 = "64d5a3a3229bd9d3b5e6cf75ca710146ca46158dd3cda7a888e55a71a8df2e71"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def storage():
    value = DatabaseStorage(db_url="sqlite:///:memory:")
    with Session(value.engine) as session:
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=NOW_ISO))
        session.commit()
    try:
        yield value
    finally:
        value.engine.dispose()


def _article(article_id: str, **overrides) -> ArticleRecord:
    values = {
        "id": article_id,
        "title": f"Title {article_id}",
        "content_type": "rss_article",
        "source_id": "plain_blog",
        "source_url": f"https://blog.test/{article_id}",
        "publish_date": NOW_ISO,
        "fetched_date": NOW_ISO,
        "has_content": True,
        "content": f"Body of {article_id} with enough detail.",
        "extensions_json": "{}",
    }
    values.update(overrides)
    return ArticleRecord(**values)


def _payload(**overrides) -> dict:
    value = {
        "score_reason": "常规更新,影响有限。",
        "quality_score": 5.4,
        "content_kind": "tutorial",
        "content_value_reason": "步骤与代码完整,可照着做。",
        "content_value": 7.3,
        "summary": "文章给出了从安装到部署的完整步骤。",
        "content_genre": "tutorial",
        "primary_tag_code": None,
        "tag_assignments": [],
        "tag_candidates": [],
        "content_features": [],
        "entities": [],
    }
    value.update(overrides)
    return value


def _run_worker(storage, article_id: str, payload: dict) -> None:
    with Session(storage.engine) as session:
        task = claim_analysis_tasks(session, worker_id="w", now=NOW)[0]
        assert task.article_id == article_id

    async def fake(_input, _tags, _config):
        return payload

    result = asyncio.run(process_claimed_analysis(
        storage.engine, task, llm_config=LLM_CONFIG, analyzer=fake,
        now_fn=lambda: NOW + dt.timedelta(seconds=2),
    ))
    assert result.status == "succeeded"


# ── 提示词契约 ────────────────────────────────────────────────────────────────

def test_news_ruler_unchanged_and_podcast_prompts_are_byte_identical_to_v6():
    assert prompts.ARTICLE_ANALYSIS_SCORING_VERSION == "news-value-v2"
    assert prompts.ARTICLE_ANALYSIS_PROMPT_VERSION == "article-analysis-v7"
    assert prompts.CONTENT_VALUE_SCORING_VERSION == "content-value-v2"
    assert _sha(prompts.NEWS_VALUE_ONLY_SYSTEM_PROMPT) == V6_ARTICLE_SHA256
    assert _sha(prompts.PODCAST_ANALYSIS_SYSTEM_PROMPT) == V6_PODCAST_SHOW_NOTES_SHA256
    assert _sha(prompts.PODCAST_TRANSCRIPT_ANALYSIS_SYSTEM_PROMPT) == V6_PODCAST_TRANSCRIPT_SHA256
    article = prompts.ARTICLE_ANALYSIS_SYSTEM_PROMPT
    # 新闻尺子原文整段保留,内容价值尺子插在它之后
    assert article.startswith(prompts._NEWS_VALUE_RULER + prompts.CONTENT_VALUE_RULER_PROMPT)
    assert "content_kind、content_value_reason、\ncontent_value" in article
    assert prompts.content_value_contract_version("podcast_episode") == ""
    assert prompts.content_value_contract_version("rss_article") == "content-value-v2"


def test_envelope_carries_kind_hint_and_signals_only_for_articles():
    article = prompts.build_article_analysis_user_prompt(
        title="t", body="b", content_type="web_article", source_id="s",
        kind_hint="paper", community_signals={"hf_upvotes": 42},
    )
    assert '"kind_hint":"paper"' in article
    assert '"community_signals":{"hf_upvotes":42}' in article
    plain = prompts.build_article_analysis_user_prompt(title="t", body="b", content_type="web_article", source_id="s")
    assert "kind_hint" not in plain and "community_signals" not in plain
    podcast = prompts.build_article_analysis_user_prompt(
        title="t", body="b", content_type="podcast_episode", source_id="s",
        kind_hint="opinion", community_signals={"hf_upvotes": 1},
    )
    assert "kind_hint" not in podcast and "community_signals" not in podcast


def test_input_derives_rule_kind_hint_and_hf_upvotes():
    hf = _article(
        "hf", source_id="web_huggingface_daily_papers", content_type="web_article",
        extensions_json=json.dumps({"raw_data": {"listing_source": "huggingface_daily_papers", "upvotes": 344}}),
    )
    hf_input = analysis_input_from_article(hf, None)
    assert hf_input.kind_hint == "paper"
    assert hf_input.community_signals == {"hf_upvotes": 344}
    arxiv = analysis_input_from_article(_article("ax", source_url="https://arxiv.org/abs/2610.1"), None)
    assert arxiv.kind_hint == "paper" and arxiv.community_signals is None
    changelog = analysis_input_from_article(_article("cl", source_id="docs_claude_code_changelog"), None)
    assert changelog.kind_hint == "release"
    plain = analysis_input_from_article(_article("pl"), None)
    assert plain.kind_hint == "" and plain.community_signals is None
    # 别的源的 raw_data.upvotes 不算社区信号
    other = _article("o", extensions_json=json.dumps({"raw_data": {"upvotes": 9}}))
    assert analysis_input_from_article(other, None).community_signals is None


# ── 校验 ──────────────────────────────────────────────────────────────────────

def test_validation_requires_content_value_for_article_contract():
    ok = validate_analysis_payload(_payload(), active_tags=[], require_content_value=True)
    assert (ok.result.content_kind, ok.result.content_value) == ("tutorial", 7.3)
    for broken in (
        {"content_value": None},
        {"content_value": 11},
        {"content_value_reason": ""},
        {"content_kind": "listicle"},
    ):
        with pytest.raises(ValueError):
            validate_analysis_payload(_payload(**broken), active_tags=[], require_content_value=True)
    # 不要求时(公共日报现场补评、播客契约)无效就丢掉,新闻分照常
    lenient = validate_analysis_payload(_payload(content_value=None), active_tags=[])
    assert lenient.result.content_value is None and lenient.result.quality_score == 5.4


def test_validation_rule_kind_wins_and_genre_valued_kind_folds_back():
    hinted = validate_analysis_payload(
        _payload(content_kind="news"), active_tags=[], require_content_value=True, kind_hint="paper",
    )
    assert hinted.result.content_kind == "paper"
    assert "content_kind_overridden_by_rule" in hinted.warnings
    folded = validate_analysis_payload(
        _payload(content_kind="social_discussion"), active_tags=[], require_content_value=True,
    )
    assert folded.result.content_kind == "news"
    assert "content_kind_from_genre" in folded.warnings


def test_brief_inline_scoring_does_not_repair_for_content_value(monkeypatch):
    calls = []

    async def fake_chat(**kwargs):
        calls.append(kwargs)
        return json.dumps(_payload(content_value=None, content_value_reason=""), ensure_ascii=False)

    monkeypatch.setattr("services.article_analysis.chat_completion", fake_chat)
    inp = analysis_input_from_article(_article("brief"), None)
    result = asyncio.run(analyze_article_with_llm(inp, [], LLM_CONFIG, require_content_value=False))
    assert result["quality_score"] == 5.4 and len(calls) == 1
    # worker 默认按契约要求:同样的输出会走一次结构修复
    calls.clear()
    with pytest.raises(ValueError):
        asyncio.run(analyze_article_with_llm(inp, [], LLM_CONFIG))
    assert len(calls) == 2


# ── worker 提交 ───────────────────────────────────────────────────────────────

def test_full_analysis_persists_content_value_with_its_own_version(storage):
    with Session(storage.engine) as session:
        session.add(_article("full"))
        session.commit()
        assert queue_article_analysis(session, "full", now=NOW) == "created"
        session.commit()
    _run_worker(storage, "full", _payload())
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "full")
        assert (record.quality_score, record.prompt_version, record.scoring_version) == (
            5.4, "article-analysis-v7", "news-value-v2",
        )
        assert (record.content_kind, record.content_value, record.content_value_version) == (
            "tutorial", 7.3, "content-value-v2",
        )
        assert record.content_value_reason == "步骤与代码完整,可照着做。"
        assert record.pending_scope == ""


def test_podcast_keeps_its_contract_and_projects_score_as_content_value(storage):
    with Session(storage.engine) as session:
        session.add(_article("pod", content_type="podcast_episode"))
        session.commit()
        queue_article_analysis(session, "pod", now=NOW)
        session.commit()
    _run_worker(storage, "pod", _payload(quality_score=8.4, score_reason="一手长访谈", content_value=None))
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "pod")
        article = session.get(ArticleRecord, "pod")
        assert record.scoring_version == prompts.PODCAST_ANALYSIS_SCORING_VERSION
        assert record.content_value is None and record.content_value_version == ""
        assert record.podcast_initial_score == 8.4   # 门控照旧读它(第三波才改)
        item = serialize_article_list_item(article, analysis=record)
    assert item["news_value"] is None
    assert (item["content_value"], item["content_value_reason"]) == (8.4, "一手长访谈")
    assert (item["primary_dimension"], item["primary_score"]) == ("content", 8.4)
    assert item["quality_score"] == 8.4   # 旧字段原样保留


def test_content_value_fill_writes_only_content_fields(storage):
    with Session(storage.engine) as session:
        article = _article("fill")
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id="fill", status="succeeded", tagging_status="succeeded",
            quality_score=6.1, score_reason="旧依据", summary="旧摘要", content_genre="research_paper",
            content_hash=compute_content_hash(article), prompt_version="article-analysis-v6",
            scoring_version="news-value-v2", taxonomy_version=1, analyzed_at=NOW_ISO,
            created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        session.commit()
        assert queue_content_value_fill(session, "fill", now=NOW) == "queued"
        session.commit()
        record = session.get(ArticleAnalysisRecord, "fill")
        assert (record.status, record.pending_scope) == ("pending", PENDING_SCOPE_CONTENT_VALUE)
        assert record.quality_score == 6.1   # 排队期间旧结果照常可读
    _run_worker(storage, "fill", _payload(
        quality_score=9.9, score_reason="新依据", summary="新摘要", content_genre="opinion",
        content_kind="paper", content_value=7.8, content_value_reason="方法新、消融完整。",
    ))
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "fill")
        assert (record.status, record.pending_scope) == ("succeeded", "")
        assert (record.quality_score, record.score_reason, record.summary, record.content_genre) == (
            6.1, "旧依据", "旧摘要", "research_paper",
        )
        assert record.prompt_version == "article-analysis-v6"
        assert (record.content_kind, record.content_value, record.content_value_version) == (
            "paper", 7.8, "content-value-v2",
        )
        attempt = session.exec(select(ArticleAnalysisAttemptRecord)).one()
        assert json.loads(attempt.result_summary_json)["scope"] == "content_value"
        assert queue_content_value_fill(session, "fill", now=NOW) == "unchanged"


def test_content_value_fill_guards(storage):
    with Session(storage.engine) as session:
        for article in (_article("never"), _article("stale-news"), _article("pod", content_type="podcast_episode")):
            session.add(article)
        session.flush()
        for article_id, scoring in (("stale-news", "news-value-v1"), ("pod", prompts.PODCAST_ANALYSIS_SCORING_VERSION)):
            article = session.get(ArticleRecord, article_id)
            session.add(ArticleAnalysisRecord(
                article_id=article_id, status="succeeded", quality_score=6.0, content_hash=compute_content_hash(article),
                scoring_version=scoring, analyzed_at=NOW_ISO, created_at=NOW_ISO, updated_at=NOW_ISO,
            ))
        session.commit()
        assert queue_content_value_fill(session, "never", now=NOW) == "ineligible"
        assert queue_content_value_fill(session, "stale-news", now=NOW) == "ineligible"
        assert queue_content_value_fill(session, "pod", now=NOW) == "ineligible"
        assert queue_content_value_fill(session, "missing", now=NOW) == "ineligible"


def test_content_change_during_fill_switches_back_to_full_analysis(storage):
    with Session(storage.engine) as session:
        article = _article("edit")
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id="edit", status="succeeded", quality_score=6.0, content_hash=compute_content_hash(article),
            scoring_version="news-value-v2", prompt_version="article-analysis-v6", analyzed_at=NOW_ISO,
            created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        session.commit()
        assert queue_content_value_fill(session, "edit", now=NOW) == "queued"
        session.commit()
        article = session.get(ArticleRecord, "edit")
        article.content = "Rewritten body."
        session.add(article)
        session.commit()
        assert queue_article_analysis(session, "edit", now=NOW) == "invalidated"
        session.commit()
        record = session.get(ArticleAnalysisRecord, "edit")
        assert (record.status, record.pending_scope, record.quality_score) == ("pending", "", None)


# ── 读取规则 ──────────────────────────────────────────────────────────────────

def test_primary_dimension_rule():
    assert cc.primary_dimension("paper", 5.2, 7.6) == "content"
    assert cc.primary_dimension("news", 7.2, 5.6) == "news"
    assert cc.primary_dimension("release", 5.5, 3.5) == "news"
    assert cc.primary_dimension(None, 6.0, 7.0) == "news"
    # 新闻价值 ≥ 8.5 一律以新闻价值为主
    assert cc.primary_dimension("opinion", 8.5, 9.4) == "news"
    assert cc.primary_dimension("opinion", 8.4, 6.8) == "content"
    # 只有一个分时显示它
    assert cc.primary_dimension("paper", 5.2, None) == "news"
    assert cc.primary_dimension("news", None, 6.0) == "content"
    assert cc.primary_dimension("news", None, None) is None


def test_score_projection_hides_outdated_content_value():
    current = ArticleAnalysisRecord(
        article_id="a", quality_score=5.2, score_reason="常规论文", content_value=7.6,
        content_value_reason="方法新", content_value_version="content-value-v2",
    )
    projected = cc.score_projection(content_type="web_article", kind="paper", analysis=current)
    assert projected == {
        "news_value": 5.2, "news_value_reason": "常规论文",
        "content_value": 7.6, "content_value_reason": "方法新",
        "primary_dimension": "content", "primary_score": 7.6,
    }
    stale = ArticleAnalysisRecord(
        article_id="b", quality_score=5.2, content_value=7.6, content_value_version="content-value-v1",
    )
    projected = cc.score_projection(content_type="web_article", kind="paper", analysis=stale)
    assert projected["content_value"] is None and projected["primary_dimension"] == "news"
    assert cc.score_projection(content_type="web_article", kind=None, analysis=None)["primary_dimension"] is None


def test_list_item_uses_model_kind_after_rules():
    article = _article("k")
    analysis = ArticleAnalysisRecord(
        article_id="k", status="succeeded", quality_score=6.0, content_genre="product_update",
        content_kind="tutorial", content_value=7.0, content_value_reason="可照做",
        content_value_version="content-value-v2",
    )
    item = serialize_article_list_item(article, analysis=analysis)
    assert (item["kind"], item["kind_basis"]) == ("tutorial", "llm")
    assert item["event_type"] is None   # 教程不带事件类型
    assert item["primary_dimension"] == "content"
    hf = _article("h", source_id="web_huggingface_daily_papers", content_type="web_article")
    item = serialize_article_list_item(hf, analysis=analysis)
    assert (item["kind"], item["kind_basis"]) == ("paper", "source")


# ── 回填策略 ──────────────────────────────────────────────────────────────────

def _seed_backfill(storage):
    rows = {
        "never": None,
        "paper-old": ("research_paper", None, None),
        "tutorial-old": ("tutorial", None, None),
        "news-old": ("model_release", None, None),
        "tutorial-done": ("tutorial", "tutorial", "content-value-v2"),
        "pod-old": ("opinion", None, None),
    }
    with Session(storage.engine) as session:
        session.add(SourceConfigRecord(
            source_id="plain_blog", name="plain_blog", ai_analysis_enabled=True,
            created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        for article_id, spec in rows.items():
            article = _article(
                article_id,
                content_type="podcast_episode" if article_id.startswith("pod") else "rss_article",
            )
            session.add(article)
            session.flush()
            if spec is None:
                continue
            genre, kind, version = spec
            session.add(ArticleAnalysisRecord(
                article_id=article_id, status="succeeded", tagging_status="succeeded", quality_score=6.0,
                content_genre=genre, content_kind=kind, content_value=7.0 if version else None,
                content_value_version=version or "", content_hash=compute_content_hash(article),
                prompt_version=prompts.analysis_contract_versions(article.content_type)[0],
                scoring_version=prompts.analysis_contract_versions(article.content_type)[1],
                taxonomy_version=1, analyzed_at=NOW_ISO, created_at=NOW_ISO, updated_at=NOW_ISO,
            ))
        session.commit()


def test_missing_selection_never_rescores_existing_results(storage):
    _seed_backfill(storage)
    with Session(storage.engine) as session:
        estimate = analysis_backfill.estimate_full_analysis_backfill(
            session, days=None, selection="missing", now=NOW + dt.timedelta(hours=1),
        )
    assert estimate["article_count"] == 1


def test_content_value_selection_targets_deep_reads_and_commits_only_content(storage):
    _seed_backfill(storage)
    later = NOW + dt.timedelta(hours=1)
    with Session(storage.engine) as session:
        job = analysis_backfill.create_full_analysis_backfill(
            session, days=None, selection="content_value", actor_id="admin",
            confirmation=analysis_backfill.FULL_ANALYSIS_CONFIRMATION, now=later,
        )
        targets = sorted(item.article_id for item in session.exec(select(TagRetagJobItemRecord)).all())
        # 体裁「学术论文」的资讯与教程入选;已有当前内容价值的、新闻、从未分析的、播客都不选
        assert targets == ["paper-old", "tutorial-old"]
        job = analysis_backfill.claim_full_analysis_backfill(session, lease_owner="w", now=later)
        job_id = int(job.id)
        assert analysis_backfill.dispatch_full_analysis_backfill(session, job, lease_owner="w", now=later) == 2
        for article_id in targets:
            record = session.get(ArticleAnalysisRecord, article_id)
            assert (record.status, record.pending_scope) == ("pending", "content_value")
    with Session(storage.engine) as session:
        tasks = claim_analysis_tasks(session, worker_id="w", now=later)
    assert sorted(task.article_id for task in tasks) == targets

    async def fake(_input, _tags, _config):
        return _payload(quality_score=9.0)

    for task in tasks:
        asyncio.run(process_claimed_analysis(
            storage.engine, task, llm_config=LLM_CONFIG, analyzer=fake,
            now_fn=lambda: later + dt.timedelta(seconds=2),
        ))
    with Session(storage.engine) as session:
        for article_id in targets:
            record = session.get(ArticleAnalysisRecord, article_id)
            assert record.quality_score == 6.0 and record.content_value == 7.3
        job = analysis_backfill.get_full_analysis_backfill(session, job_id)
        job = analysis_backfill.reconcile_full_analysis_backfill(session, job, lease_owner="w", now=later)
        assert (job.status, job.succeeded_count) == ("succeeded", 2)


# ── 黄金集与门禁脚本(不调用模型)────────────────────────────────────────────────

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_eval_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "eval_content_value_golden", os.path.join(ROOT, "scripts", "eval_content_value_golden.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_golden_fixture_is_well_formed():
    items = json.load(open(os.path.join(ROOT, "tests", "fixtures", "golden_content_value.json"), encoding="utf-8"))["items"]
    assert len(items) == 74
    assert len({it["id"] for it in items}) == 74
    for it in items:
        assert 1.0 <= it["gold"]["content_value"] <= 10.0
        assert it["gold"]["kind"] in set(cc.ITEM_KINDS) | {None}
        if it["kind_hint"]:
            assert it["gold"]["kind"] in (None, it["kind_hint"])
        assert set(it["raters"]) == {"claude_opus", "codex_gpt6astra"}
    by_id = {it["id"]: it for it in items}
    for it in items:
        if it["pair_with"]:
            assert it["pair_with"] in by_id


def test_eval_gates():
    script = _load_eval_script()
    items = [
        {"n": 1, "id": "a", "group": "news_official", "pair_with": "b", "kind_hint": None, "gold": {"kind": "news", "content_value": 6.0}},
        {"n": 2, "id": "b", "group": "news_media", "pair_with": None, "kind_hint": None, "gold": {"kind": "news", "content_value": 4.0}},
        {"n": 3, "id": "c", "group": "off_domain", "pair_with": None, "kind_hint": None, "gold": {"kind": "opinion", "content_value": 3.0}},
        {"n": 4, "id": "d", "group": "research_report", "pair_with": None, "kind_hint": None, "gold": {"kind": "news", "content_value": 5.0}},
    ]

    def results(**overrides):
        base = {
            1: {"values": [6.2, 6.0, 6.4], "news": [8.0, 8.5, 7.9], "kind": "news", "errors": []},
            2: {"values": [4.1, 4.3, 3.9], "news": [7.0, 7.4, 7.2], "kind": "news", "errors": []},
            3: {"values": [2.5, 3.0, 2.8], "news": [2.0, 2.4, 2.1], "kind": "opinion", "errors": []},
            4: {"values": [5.0, 5.4, 4.8], "news": [6.0, 6.2, 5.9], "kind": "news", "errors": []},
        }
        for key, patch in overrides.items():
            base[int(key[1:])].update(patch)
        return base

    failures, readings = script.check_gates(items, results())
    assert failures == [] and readings["kind_accuracy"] == 1.0
    failures, _ = script.check_gates(items, results(n3={"values": [2.5, 5.2, 2.8]}))
    assert any("领域外" in item for item in failures)
    failures, _ = script.check_gates(items, results(n4={"kind": "paper"}))
    assert any("研究报道被判成论文" in item for item in failures)
    failures, _ = script.check_gates(items, results(n2={"values": [6.9, 7.0, 7.1]}))
    assert any("成对检验" in item for item in failures)
    # 内容价值的波动不得高于同批调用里的新闻分
    failures, _ = script.check_gates(items, results(n1={"values": [5.4, 6.0, 6.3]}))
    assert any("极差" in item for item in failures)
