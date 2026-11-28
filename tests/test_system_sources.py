"""系统源从一个变成名单(issue #72 第一批)。

系统源 = 由本系统自己产出内容的源,登记在 ``src/sources/catalog/system.toml`` 的 ``[[system]]``。
以前代码里只认公共日报一个 id;现在「是不是系统源」各处都问 ``source_catalog.system_source_ids()``。
这一批名单里仍只有日报,线上行为不变。守三件事:

- 日报没有被搬错:目录元数据对照的是搬迁前 ``api.sources.DAILY_BRIEF_SOURCE_META`` 的字面量;
- 「系统源的条目不回流」对名单里的每一个源都成立(用夹具 ``second_system_source`` 加第二个来验:
  公共日报候选、推送拒收、兴趣取证、Taxonomy bootstrap、建源行;个人早报、榜单、候选治理的对照
  写在各自的测试文件里,用它们现成的造数据函数);
- 日报专属的口子没有被顺手放宽:管理员「手工补一期日报」只对日报成立,读者目录只预先列出日报。
"""

import os
import sys
import textwrap
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers import catalog_files  # noqa: E402
from models.db import ArticleRecord, SourceConfigRecord  # noqa: E402
from services import ingest_policy, source_catalog  # noqa: E402

BRIEF = "dorami_daily_brief"
PICKS = "dorami_trending_picks"       # 第二批登记的系统源:生成流程 GitHub Trending 精读
# 搬迁前 api.sources.DAILY_BRIEF_SOURCE_META 的字面量(基线 7e0aab5b),逐字抄在这里
BRIEF_META_AT_MIGRATION = {
    "name": "哆啦美·AI资讯日报",
    "icon": "🤖",
    "desc": "由后端大模型每日自动生成的 AI 资讯日报，汇总择优近期归档内容。",
    "content_type": "daily_brief",
    "source_owner": "dorami",
}


def _make_sink(tmp_path, name="system.db"):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _seed(engine, article_id, source_id, fetched_date="2026-06-05T00:00:00", *, content_type="rss_article"):
    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=f"标题-{article_id}", content_type=content_type, source_id=source_id,
            source_url=f"https://example.test/{article_id}", publish_date="2026-06-05",
            fetched_date=fetched_date, has_content=True, content="正文内容", extensions_json="{}",
        ))
        session.commit()


# ── 目录:名单、元数据、新键 ────────────────────────────────────────────────────


def test_repo_catalog_lists_the_two_system_sources_and_the_brief_meta_is_unchanged():
    assert source_catalog.system_source_ids() == frozenset({BRIEF, PICKS})
    assert source_catalog.system_content_types() == frozenset({"daily_brief", "trending_picks"})
    # 精读:目录里登记的展示元数据;条目默认不送入库分析(对站内已有内容的二次加工)
    assert source_catalog.system_source_meta(PICKS) == {
        "name": "GitHub Trending 精读", "icon": "📈",
        "desc": "从当天的 GitHub Trending 日榜里挑出值得看的仓库，各配一句中文说明与理由。",
        "content_type": "trending_picks", "source_owner": "dorami",
    }
    assert source_catalog.system_source_analysis_default(PICKS) is False
    assert source_catalog.is_system_source(BRIEF)
    assert not source_catalog.is_system_source("rss_openai_news")
    assert not source_catalog.is_system_source("") and not source_catalog.is_system_source(None)
    assert source_catalog.system_source_meta(BRIEF) == BRIEF_META_AT_MIGRATION
    assert source_catalog.system_source_meta("rss_openai_news") is None
    # 日报照旧默认送分析:个人早报靠它的分析行把日报条目带进版面
    assert source_catalog.system_source_analysis_default(BRIEF) is True

    from api import sources as api_sources

    assert api_sources.system_source_meta(BRIEF) == BRIEF_META_AT_MIGRATION
    assert api_sources.system_source_name(BRIEF) == "哆啦美·AI资讯日报"
    assert api_sources.system_source_name("rss_openai_news") == ""


def test_second_system_source_is_in_the_list(second_system_source):
    assert source_catalog.system_source_ids() == frozenset({BRIEF, PICKS, second_system_source})
    assert source_catalog.system_source_meta(second_system_source) == {
        "name": "测试流程", "icon": "🧪", "desc": "测试用的第二个系统源。",
        "content_type": "",          # 条目类型是代码里的事实(_SYSTEM_CONTENT_TYPES),没登记就是空
        "source_owner": "dorami",
    }
    assert source_catalog.system_source_analysis_default(second_system_source) is False
    view = source_catalog._builtin_view(second_system_source)
    assert (view.origin, view.name, view.shape, view.fetcher_id) == ("system", "测试流程", "article", "")


_SOURCES = textwrap.dedent("""
    [[source]]
    id = "rss_demo"
    name = "Demo"
    description = "简介"
    shape = "article"
    admission = "admitted"
""")
_COLLECTIONS = textwrap.dedent("""
    [[collection]]
    id = "one"
    name = "一个"
    description = "一个源"
    provenance_note = "测试"
    sources = ["rss_demo"]
""")


def _write_catalog(directory, system):
    (directory / "rss.toml").write_text(_SOURCES, encoding="utf-8")
    (directory / "system.toml").write_text(textwrap.dedent(system), encoding="utf-8")
    (directory / "collections.toml").write_text(_COLLECTIONS, encoding="utf-8")
    return directory


_TWO_SYSTEM = """
    [[system]]
    id = "dorami_daily_brief"
    name = "日报"
    shape = "article"

    [[system]]
    id = "dorami_trending_picks"
    name = "精读"
    shape = "article"

    [[system]]
    id = "dorami_other"
    name = "另一个流程"
    shape = "article"
    icon = "📈"
    description = "说明"
    analysis = false

    [reader_defaults]
    subscriptions = []
"""


def test_loader_reads_icon_description_and_analysis(tmp_path):
    loaded = catalog_files.load(_write_catalog(tmp_path, _TWO_SYSTEM))
    brief, other = loaded.system["dorami_daily_brief"], loaded.system["dorami_other"]
    assert (brief.icon, brief.description, brief.analysis) == ("", "", True)      # 都是可选的,analysis 缺省 true
    assert (other.icon, other.description, other.analysis) == ("📈", "说明", False)


@pytest.mark.parametrize("old, new, message", [
    ("analysis = false", 'analysis = "no"', "analysis 必须是 true 或 false"),
    ("analysis = false", "analysis = 0", "analysis 必须是 true 或 false"),
    ('icon = "📈"', "icon = 3", "icon 必须是字符串"),
    ("analysis = false", 'colour = "red"', "未知键"),
])
def test_loader_rejects_malformed_system_keys(tmp_path, old, new, message):
    assert old in _TWO_SYSTEM
    system = _TWO_SYSTEM.replace(old, new)
    with pytest.raises(catalog_files.CatalogError, match=message):
        catalog_files.load(_write_catalog(tmp_path, system))


def test_required_system_ids_must_still_be_present(tmp_path):
    system = _TWO_SYSTEM.replace('id = "dorami_daily_brief"', 'id = "dorami_renamed"')
    with pytest.raises(catalog_files.CatalogError, match="缺系统源"):
        catalog_files.load(_write_catalog(tmp_path, system))


# ── 源行:每个系统源各一行 ──────────────────────────────────────────────────────


def test_reconcile_creates_one_row_per_system_source(tmp_path, second_system_source):
    from services import source_rows

    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        first = source_rows.reconcile_source_rows(session)
        session.commit()
        assert first["system"] == "created"
        rows = {row.source_id: row for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.origin == "system")
        ).all()}
        assert set(rows) == {BRIEF, PICKS, second_system_source}
        other = rows[second_system_source]
        assert (other.name, other.shape, other.source_owner, other.legacy_export) == ("测试流程", "article", "dorami", False)
        assert not (other.fetcher_id or "")
        # analysis = false 的系统源建出来就不送分析;日报照旧送
        assert rows[BRIEF].ai_analysis_enabled is True and other.ai_analysis_enabled is False
        # 新建的行带上目录条目的图标与简介(源管理的列表与检视器读源行)
        assert (other.icon, other.description) == ("🧪", "测试用的第二个系统源。")
        assert (rows[PICKS].icon, rows[BRIEF].icon) == ("📈", "🤖")

        again = source_rows.reconcile_source_rows(session)
        session.commit()
        assert again["system"] == "unchanged"


def test_a_new_system_source_is_reported_as_created_next_to_an_existing_one(tmp_path, monkeypatch):
    """升级时的样子:库里已经有日报那一行,目录里多了一个系统源——汇总结果仍报 created。"""
    from services import source_rows

    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        assert source_rows.reconcile_source_rows(session)["system"] == "created"
        session.commit()
        real = catalog_files.loaded()
        extra = catalog_files.SystemEntry(id="dorami_later", name="后来的流程", shape="article")
        monkeypatch.setattr(catalog_files, "loaded", lambda: replace(real, system={**real.system, extra.id: extra}))
        assert source_rows.reconcile_source_rows(session)["system"] == "created"
        session.commit()
        assert session.get(SourceConfigRecord, "dorami_later").origin == "system"
        assert source_rows.reconcile_source_rows(session)["system"] == "unchanged"


def test_ensure_source_row_builds_a_system_row_for_any_system_source(tmp_path, second_system_source):
    """写入时补建:条目的 source_id 是系统源时建的是系统行,不是「收录临时源」。"""
    from services import source_rows

    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        for source_id in (BRIEF, second_system_source):
            row = source_rows.ensure_source_row(session, source_id, content_type="rss_article")
            assert row is not None and row.origin == "system" and row.admission_status != "provisional"
        session.commit()


# ── 不回流:公共日报候选 ────────────────────────────────────────────────────────


def test_daily_brief_candidates_never_include_system_source_items(tmp_path, second_system_source):
    """系统源的条目即使没有分析行也不能进候选(候选会被就地补评,挡不住)。"""
    from services.daily_brief import collect_candidates

    sink = _make_sink(tmp_path)
    _seed(sink.engine, "normal", "rss_openai_news")
    _seed(sink.engine, "brief-1", BRIEF, content_type="daily_brief")
    _seed(sink.engine, "other-1", second_system_source)
    with Session(sink.engine) as session:
        for cursor in ("", "2026-06-01T00:00:00"):
            candidates, _seen, _scanned = collect_candidates(session, cursor=cursor)
            assert [candidate.id for candidate in candidates] == ["normal"]


def test_without_the_second_system_source_its_id_is_an_ordinary_source(tmp_path):
    """反向对照:名单里没有它时,同一个 id 的条目照常是候选——排除靠的是名单,不是 ``dorami_`` 前缀。"""
    from services.daily_brief import collect_candidates

    sink = _make_sink(tmp_path)
    _seed(sink.engine, "other-1", "dorami_test_flow")
    with Session(sink.engine) as session:
        candidates, _seen, _scanned = collect_candidates(session, cursor="")
        assert [candidate.id for candidate in candidates] == ["other-1"]


# ── 不回流:收录通道不能往系统源写 ──────────────────────────────────────────────


def test_ingest_refuses_every_system_source(second_system_source):
    assert ingest_policy.reserved_source_id_reason(BRIEF) == "公共日报源只能由日报生成写入"
    # 「手工补一期日报」的口子只留给日报
    assert ingest_policy.reserved_source_id_reason(BRIEF, allow_daily_brief=True) is None
    assert ingest_policy.reserved_source_id_reason(second_system_source) == "系统源只能由其生成流程写入"
    assert (
        ingest_policy.reserved_source_id_reason(second_system_source, allow_daily_brief=True)
        == "系统源只能由其生成流程写入"
    )
    assert ingest_policy.reserved_source_id_reason("rss_openai_news") is None


def test_ingest_check_treats_the_id_as_ordinary_when_it_is_not_listed():
    assert ingest_policy.reserved_source_id_reason("dorami_test_flow") is None


# ── 不回流:兴趣取证、Taxonomy bootstrap ────────────────────────────────────────


def test_interest_evidence_scope_drops_system_sources(tmp_path, second_system_source):
    from services import custom_interests

    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        readable = custom_interests.readable_source_ids(
            session, "alice", [BRIEF, second_system_source, "rss_openai_news"]
        )
    assert readable == {"rss_openai_news"}


def test_taxonomy_bootstrap_skips_system_sources(tmp_path, second_system_source):
    from services import taxonomy_bootstrap

    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        assert not taxonomy_bootstrap.bootstrap_source_is_eligible(session, BRIEF)
        assert not taxonomy_bootstrap.bootstrap_source_is_eligible(session, second_system_source)
        assert taxonomy_bootstrap.bootstrap_source_is_eligible(session, "rss_openai_news")


# ── 读者目录:系统源有了条目才出现(日报除外) ────────────────────────────────────


def _login(client, username="user", password="user"):
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200


def test_reader_catalog_lists_a_system_source_only_once_it_has_items(monkeypatch, tmp_path, second_system_source):
    import api.app as app_module
    from config import RuntimeConfig
    from tests.conftest import seed_default_accounts

    sink = _make_sink(tmp_path, "catalog.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    seed_default_accounts(sink.engine)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="reader")))
    # 启动对账会给每个系统源建一行;这里对着本用例的库跑一遍(测试里应用启动时对账的不是这个库)
    from services import source_rows

    with Session(sink.engine) as session:
        source_rows.reconcile_source_rows(session)
        session.commit()
        assert session.get(SourceConfigRecord, second_system_source).origin == "system"

    with TestClient(app_module.app) as client:
        _login(client)
        before = {row["source_id"]: row for row in client.get("/api/reader/sources").json()["sources"]}
        # 日报:还没有任何一期也预先列出(便于提前订阅),现网如此
        assert before[BRIEF]["name"] == "哆啦美·AI资讯日报" and before[BRIEF]["count"] == 0
        # 别的系统源:源行在,但没有条目就不出现——升级后发现页不会多出一个空源
        assert second_system_source not in before

        _seed(sink.engine, "other-1", second_system_source)
        after = {row["source_id"]: row for row in client.get("/api/reader/sources").json()["sources"]}

    entry = after[second_system_source]
    assert (entry["name"], entry["count"]) == ("测试流程", 1)
    assert entry["icon"] == "🧪" and entry["source_owner"] == "dorami"


def test_a_row_that_is_not_a_system_row_still_wins_over_the_catalog_meta(monkeypatch, tmp_path, second_system_source):
    """元数据合并的先后没有变:源行(只要不是 origin=system)的字段盖过目录里系统源的展示元数据。

    基线里日报就是这样合并的(``{**DAILY_BRIEF_SOURCE_META, **meta}``)。这种行现实里来自存量:
    同一个 id 在登记成系统源之前已经有一行,且带着同步印记所以对账不收养。
    """
    import api.app as app_module
    from config import RuntimeConfig
    from tests.conftest import seed_default_accounts

    sink = _make_sink(tmp_path, "precedence.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    seed_default_accounts(sink.engine)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="reader")))
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id=second_system_source, name="行上的名字", source_type="legacy", origin="legacy",
            shape="article", source_owner="someone", collection_authority_id="authority-1",
            is_active=True, created_at="2026-06-01T00:00:00", updated_at="2026-06-01T00:00:00",
        ))
        session.commit()
    _seed(sink.engine, "other-1", second_system_source)

    with TestClient(app_module.app) as client:
        _login(client)
        catalog = {row["source_id"]: row for row in client.get("/api/reader/sources").json()["sources"]}

    entry = catalog[second_system_source]
    assert (entry["name"], entry["source_owner"]) == ("行上的名字", "someone")


def test_admin_content_overview_names_every_system_source_from_the_catalog(monkeypatch, tmp_path, second_system_source):
    """管理面「内容」总览里系统源的名称与图标来自目录条目——以前只给公共日报一个 id 合并。"""
    import api.app as app_module
    from tests.conftest import seed_default_accounts

    sink = _make_sink(tmp_path, "admin-content.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    seed_default_accounts(sink.engine)
    _seed(sink.engine, "brief-1", BRIEF, content_type="daily_brief")
    _seed(sink.engine, "other-1", second_system_source)
    _seed(sink.engine, "normal", "rss_openai_news")

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        response = client.get("/api/admin/content")
        assert response.status_code == 200
        sources = {row["source_id"]: row for row in response.json()["sources"]}

    assert (sources[BRIEF]["name"], sources[BRIEF]["icon"]) == ("哆啦美·AI资讯日报", "🤖")
    assert (sources[second_system_source]["name"], sources[second_system_source]["icon"]) == ("测试流程", "🧪")
    assert sources[second_system_source]["article_count"] == 1


def _legacy_row(source_id, *, authority=""):
    return SourceConfigRecord(
        source_id=source_id, name=source_id, source_type="legacy", origin="legacy", shape="article",
        collection_authority_id=authority, is_active=True,
        created_at="2026-06-01T00:00:00", updated_at="2026-06-01T00:00:00",
    )


def test_reconcile_summary_reports_adoption_and_prefers_created(tmp_path, monkeypatch):
    """对账摘要的 ``system`` 仍是一个词:只有收养 → adopted;新建与收养并存 → created;都没有 → unchanged。"""
    from services import source_rows

    real = catalog_files.loaded()
    extra = catalog_files.SystemEntry(id="dorami_later", name="后来的流程", shape="article")
    both = replace(real, system={**real.system, extra.id: extra})

    # 只有收养:系统源的行都在、但 origin 不是 system(存量回填的行)
    sink = _make_sink(tmp_path, "adopt.db")
    with Session(sink.engine) as session:
        session.add_all([_legacy_row(BRIEF), _legacy_row(PICKS)])
        session.commit()
        assert source_rows.reconcile_source_rows(session)["system"] == "adopted"
        session.commit()
        assert {session.get(SourceConfigRecord, sid).origin for sid in (BRIEF, PICKS)} == {"system"}
        assert source_rows.reconcile_source_rows(session)["system"] == "unchanged"

    # 新建与收养并存:日报要收养,另一个系统源要新建
    sink = _make_sink(tmp_path, "both.db")
    monkeypatch.setattr(catalog_files, "loaded", lambda: both)
    with Session(sink.engine) as session:
        session.add_all([_legacy_row(BRIEF), _legacy_row(PICKS)])
        session.commit()
        assert source_rows.reconcile_source_rows(session)["system"] == "created"
        session.commit()
        assert {session.get(SourceConfigRecord, sid).origin for sid in (BRIEF, PICKS, "dorami_later")} == {"system"}

    # 带同步印记的行不收养(上游也有这个源,目录字段不碰),也不算变化
    sink = _make_sink(tmp_path, "marked.db")
    with Session(sink.engine) as session:
        session.add_all([_legacy_row(sid, authority="authority-1") for sid in (BRIEF, PICKS, "dorami_later")])
        session.commit()
        assert source_rows.reconcile_source_rows(session)["system"] == "unchanged"
        assert session.get(SourceConfigRecord, "dorami_later").origin == "legacy"


def test_a_system_entry_without_icon_or_description_still_gets_its_row(tmp_path, monkeypatch):
    """图标与简介在目录里是可选的:没有写的条目照样建得出源行,两项都是空串。"""
    import dataclasses

    from services import collection_nodes, source_rows

    real = catalog_files.loaded()
    bare = catalog_files.SystemEntry(id="dorami_bare_flow", name="没有简介的流程", shape="article", owner="dorami")
    patched = dataclasses.replace(real, system={**real.system, bare.id: bare})
    monkeypatch.setattr(catalog_files, "loaded", lambda: patched)

    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        source_rows.reconcile_source_rows(session)
        session.commit()
        row = session.get(SourceConfigRecord, bare.id)
        assert (row.origin, row.icon, row.description) == ("system", "", "")
        node = {node["id"]: node for node in collection_nodes.source_management_catalog(session)}[bare.id]
        assert (node["icon"], node["desc"]) == ("", "")


def test_source_management_reads_icon_and_description_from_the_catalog_for_an_old_row(tmp_path):
    """老节点上日报那一行建于「图标 / 简介登记进目录」之前,行上是空的:源管理读目录里的,不回填;
    行上写了的(管理员改过)以行为准。"""
    from services import collection_nodes, source_rows

    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        source_rows.reconcile_source_rows(session)
        brief = session.get(SourceConfigRecord, BRIEF)
        brief.icon, brief.description = "", ""                                # 老行的样子
        picks = session.get(SourceConfigRecord, PICKS)
        picks.description = "管理员写的简介"
        session.add(brief); session.add(picks)
        session.commit()
        nodes = {node["id"]: node for node in collection_nodes.source_management_catalog(session)}
        assert (nodes[BRIEF]["icon"], nodes[BRIEF]["desc"]) == ("🤖", BRIEF_META_AT_MIGRATION["desc"])
        assert (nodes[PICKS]["icon"], nodes[PICKS]["desc"]) == ("📈", "管理员写的简介")
        assert (nodes[BRIEF]["origin"], nodes[BRIEF]["bound"]) == ("system", False)
        assert session.get(SourceConfigRecord, BRIEF).description == ""       # 没有回填


# ── 按条目类型排除的几处:系统源的条目类型一个不落 ──────────────────────────────


@pytest.mark.parametrize("source_id, content_type", [(BRIEF, "daily_brief"), (PICKS, "trending_picks")])
def test_sites_keyed_by_content_type_cover_every_system_content_type(tmp_path, source_id, content_type):
    """除了按源 id 的名单,还有几处是按条目类型排除的(条目的源行已不在、或换过源 id 时靠它兜住):
    加一个系统源时,这几处都要带上它的条目类型。"""
    from sqlalchemy import select as sa_select

    from models.db import ArticleAnalysisRecord
    from services import content_classification as cc, content_fingerprint, ranking_picks, rankings

    assert content_type in source_catalog.system_content_types()
    assert content_type in ranking_picks.EXCLUDED_CONTENT_TYPES            # 深度阅读 / 播客精选
    assert content_type in cc.URL_RULE_EXCLUDED_CONTENT_TYPES              # 不按链接形状猜论文
    assert cc.CONTENT_TYPE_DEFAULT_KINDS[content_type] == "digest"         # 内容类型:汇总
    assert cc.SPECIAL_SOURCE_KINDS[source_id] == "digest"
    assert cc.content_type_label(content_type) != content_type            # 有中文叫法

    sink = _make_sink(tmp_path)
    with Session(sink.engine) as session:
        # 同源内容判重:不给系统源的条目算指纹
        assert content_fingerprint.eligible(session, source_id, content_type) is False
        assert content_fingerprint.eligible(session, "gone_source", content_type) is False
        # 榜单:源行不在了的条目按类型归到「不上榜」;历史高分榜的 SQL 兜底也一样
        orphan = ArticleRecord(
            id="orphan", title="孤儿条目", content_type=content_type, source_id="gone_source", source_url="",
            publish_date="2026-06-05", fetched_date="2026-06-05T00:00:00", has_content=True, content="正文",
        )
        assert rankings._content_shape(orphan, {}) == "excluded"
        session.add(orphan)
        session.add(ArticleRecord(
            id="plain", title="普通文章", content_type="web_article", source_id="gone_source", source_url="",
            publish_date="2026-06-05", fetched_date="2026-06-05T00:00:00", has_content=True, content="正文",
        ))
        session.flush()                                                   # 分析行有外键指向文章行
        for article_id in ("orphan", "plain"):
            session.add(ArticleAnalysisRecord(
                article_id=article_id, status="succeeded", tagging_status="succeeded", quality_score=9.5,
                analysis_basis="article_body", created_at="2026-06-05T00:00:00", updated_at="2026-06-05T00:00:00",
            ))
        session.commit()
        assert sorted(session.exec(sa_select(ArticleRecord.id)).scalars().all()) == ["orphan", "plain"]
        assert [row["id"] for row in rankings._all_time_high_scores(session, shape="article")] == ["plain"]
