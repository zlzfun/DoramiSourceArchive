"""源目录读服务的消费方口径(issue #262 R1a 余项)。

业务代码不再直接读抓取器注册表,统一经 ``services.source_catalog``。这是纯重构:本文件前半
(``test_consumer_*``)钉住的是**改前就成立**的口径——各消费方在每一种来路的源上给出的角色 /
形态 / 主体 / 名称 / feed 地址;判据是文件里照改前算法写的对照实现(直接读注册表与源行),
所以这些用例在改动前后的代码上都应通过。后半(``test_catalog_*``)是读服务自身的新接口。
"""

import datetime as dt
import json
import os
import sys

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from fetchers.registry import DECOMMISSIONED_FETCHER_IDS, fetcher_registry  # noqa: E402
from models.db import ArticleRecord, ReaderSubscriptionRecord, SourceConfigRecord  # noqa: E402
from services import source_naming  # noqa: E402
from services.podcast_catalog import catalog_by_id  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


_NOW = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)).isoformat(timespec="seconds")

# (source_id, content_type, 条数)
_ARTICLES = [
    ("rss_openai_news", "rss_article", 2),
    ("x_openai", "social_post", 2),
    # 只在 articles 里出现的 id
    ("manual", "manual_note", 2),
    ("x_import_only", "social_post", 3),
    ("x_import_only", "web_article", 1),
    ("pod_import_only", "podcast_episode", 2),
    # 内建:日报 / 墓碑
    ("dorami_daily_brief", "daily_brief", 1),
    ("x_openrouter", "social_post", 2),
    ("docs_openai_api_changelog", "web_article", 1),
    # 配置源 / 读者源 / 收录临时源 / 同步源
    ("rss_admin_blog", "rss_article", 1),
    ("x_admin_account", "social_post", 1),
    ("podcast_admin_show", "podcast_episode", 1),
    ("user_rss_aaaaaaaaaaaa", "rss_article", 1),
    ("user_rss_orphan000000", "rss_article", 1),
    ("web_pushed_site", "web_article", 1),
    ("x_pushed_account", "social_post", 1),
    ("synced_upstream_rss", "rss_article", 1),
]
_EXTRA_EVIDENCE = {
    "zz_unknown": "",
    "zz_unknown_social": "social_post",
    "zz_unknown_podcast": "podcast_episode",
    "generic_rss": "",
}


def _article(index, source_id, content_type):
    return ArticleRecord(
        id=f"{source_id}_{content_type}_{index:03d}",
        title=f"{source_id} {index}",
        content_type=content_type,
        source_id=source_id,
        source_url=f"https://example.test/{source_id}/{content_type}/{index}",
        publish_date=_NOW,
        fetched_date=_NOW,
        has_content=True,
        content=f"body {source_id} {index} " * 10,
        extensions_json="{}",
    )


def _row(source_id, **kwargs):
    base = dict(
        source_id=source_id, name=kwargs.pop("name", source_id), source_type="rss", url="",
        created_at=_NOW, updated_at=_NOW, is_active=True, ai_analysis_enabled=True, params_json="{}",
    )
    base.update(kwargs)
    return SourceConfigRecord(**base)


def _seed_articles(session):
    for source_id, content_type, count in _ARTICLES:
        for index in range(count):
            session.add(_article(index, source_id, content_type))


def _bare(tmp_path):
    """源表为空:只有注册表类与 articles(对账之前、以及多数单测看到的世界)。"""
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'bare.db'}")
    with Session(sink.engine) as session:
        _seed_articles(session)
        session.commit()
    return sink


def _families(tmp_path):
    """每一种来路各有代表,跑过一次启动对账。返回 (sink, 装了行的播客目录 id, 没装行的播客目录 id)。"""
    from services import source_rows

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'families.db'}")
    catalog = catalog_by_id()
    installed, uninstalled = sorted(catalog)[:2]
    with Session(sink.engine) as session:
        _seed_articles(session)
        session.add(_article(0, installed, "podcast_episode"))
        # 管理员配置源
        session.add(_row("rss_admin_blog", name="管理员博客", url="https://blog.admin.test/feed.xml",
                         fetcher_id="generic_rss", source_scope="ai_media", provenance_tier="tier1_curated",
                         source_owner="AdminCo", base_url="https://blog.admin.test/"))
        session.add(_row("web_admin_site", name="管理员站点", source_type="web",
                         url="https://site.admin.test/news", fetcher_id="generic_web",
                         source_scope="frontier_ai_lab", provenance_tier="tier0_official",
                         source_owner="SiteLab"))
        session.add(_row("x_admin_account", name="X · Admin", source_type="x", url="https://x.com/AdminHandle",
                         fetcher_id="generic_x_timeline", source_scope="personal_commentary",
                         provenance_tier="tier2_personal_social",
                         params_json=json.dumps({"handle": "AdminHandle", "user_id": "4242"})))
        session.add(_row("leaderboard_src", name="榜单源", source_type="web", url="https://lb.test/",
                         fetcher_id="generic_web", source_scope="ai_benchmark_platform"))
        session.add(_row("empty_meta_src", name="无策展元数据", url="https://e.test/feed",
                         fetcher_id="generic_rss"))
        session.add(_row("social_literal", name="社交字面类型", source_type="social"))
        session.add(_row("bulletin_shape", name="旧动态形态", url="https://b.test/feed",
                         fetcher_id="generic_rss", shape="bulletin"))
        session.add(_row("podcast_admin_show", name="管理员播客", source_type="podcast_rss",
                         url="https://pod.admin.test/feed.xml", fetcher_id="generic_podcast_rss",
                         source_scope="expert_newsletter"))
        # 读者自定源、带凭证的公共源
        session.add(_row("user_rss_aaaaaaaaaaaa", name="读者的源", url="https://reader.test/feed.xml",
                         fetcher_id="generic_rss", owner_username="user"))
        session.add(_row("rss_credentialed", name="带凭证公共源", url="https://u:p@cred.test/feed.xml",
                         fetcher_id="generic_rss"))
        # 播客目录:只装一个
        source = catalog[installed]
        session.add(_row(installed, name=source.name, source_type="podcast_rss", url=source.feed_url,
                         fetcher_id="generic_podcast_rss", source_scope=source.source_scope or "",
                         provenance_tier=source.provenance_tier or "", admission_status="incubating"))
        # 收录临时源
        session.add(_row("web_pushed_site", name="推送来的站点", source_type="archive", origin="ingest",
                         shape="article", admission_status="provisional",
                         homepage_url="https://pushed.test/", legacy_export=False))
        session.add(_row("x_pushed_account", name="@pushed", source_type="x", origin="ingest",
                         shape="social", platform="x", admission_status="provisional",
                         params_json=json.dumps({"handle": "pushed", "user_id": "777"}), legacy_export=False))
        # 上游同步来的普通源
        session.add(_row("synced_upstream_rss", name="上游同步源", url="https://up.test/feed",
                         collection_authority_id="authority-up", source_scope="tech_media"))
        # 与预设同 id 的管理员行:对账收编成镜像行(目录字段改回类属性)
        session.add(_row("rss_simonwillison", name="被收编前的名字",
                         url="https://simonwillison.net/atom/everything/", fetcher_id="rss_simonwillison",
                         source_scope="ai_media"))
        # 与预设同 id、带上游印记、策展字段与本机类不同的行(上游与本机版本不同;对账不碰)
        session.add(_row("x_karpathy", name="上游版本的名字", source_type="x_timeline",
                         fetcher_id="x_karpathy", collection_authority_id="authority-up", origin="catalog",
                         shape="social", platform="x", source_scope="ai_media",
                         provenance_tier="tier1_curated", source_owner="UpstreamOwner"))
        session.add(_row("rss_retired_src", name="已退役源", url="https://old.test/feed",
                         fetcher_id="generic_rss", retired_at=_NOW, is_active=False))
        session.add(ReaderSubscriptionRecord(
            owner_username="user", name="只在订阅里",
            filters_json=json.dumps({"source_ids": "only_in_subscription"}),
            token_hash="consumers-fixture-token-hash", created_at=_NOW, updated_at=_NOW,
        ))
        session.commit()
    source_rows.run_startup_reconcile(sink.engine)
    return sink, installed, uninstalled


def _universe(session):
    """全部要比的 id、各自的条目类型证据(该源条目数最多的 content_type)与源行。"""
    from sqlalchemy import func

    rows = {row.source_id: row for row in session.exec(select(SourceConfigRecord)).all()}
    counts: dict[str, tuple[int, str]] = {}
    for sid, ctype, count in session.exec(
        select(ArticleRecord.source_id, ArticleRecord.content_type, func.count())
        .group_by(ArticleRecord.source_id, ArticleRecord.content_type)
    ).all():
        current = counts.get(sid)
        if current is None or (int(count), str(ctype or "")) > current:
            counts[sid] = (int(count), str(ctype or ""))
    evidence = {sid: value[1] for sid, value in counts.items()}
    evidence.update(_EXTRA_EVIDENCE)
    ids = sorted(
        set(fetcher_registry._fetchers) | set(rows) | set(evidence) | set(catalog_by_id())
        | set(DECOMMISSIONED_FETCHER_IDS) | {"dorami_daily_brief"}
    )
    return ids, {sid: evidence.get(sid, "") for sid in ids}, rows


# ── 对照实现:改前各消费方的算法,直接读注册表与源行 ─────────────────────────────


def _legacy_role_from_metadata(scope, tier, fallback):
    if tier == "tier2_personal_social" or scope in source_naming.PERSONAL_SCOPES:
        return "personal"
    if scope in source_naming.LEADERBOARD_SCOPES:
        return "leaderboard"
    if scope in source_naming.MEDIA_SCOPES or tier == "tier1_curated":
        return "media"
    return fallback


def _legacy_role(source_id, row):
    if row is not None:  # 调用方手里有行:行上的策展元数据说了算
        scope, tier = str(row.source_scope or ""), str(row.provenance_tier or "")
        return _legacy_role_from_metadata(scope, tier, "official" if (scope or tier) else "media")
    cls = fetcher_registry.get_class(source_id)
    if cls is not None:
        return _legacy_role_from_metadata(
            str(getattr(cls, "source_scope", "") or ""), str(getattr(cls, "provenance_tier", "") or ""), "official"
        )
    catalog_source = catalog_by_id().get(source_id)
    if catalog_source is not None:
        return _legacy_role_from_metadata(
            str(catalog_source.source_scope or ""), str(catalog_source.provenance_tier or ""), "official"
        )
    return "media"


def _legacy_shape_of_content_type(content_type):
    return {"social_post": "social", "podcast_episode": "podcast"}.get(content_type or "", "article")


def _legacy_row_shape(row):
    explicit = {"bulletin": "article"}.get(str(row.shape or "").strip().lower(), str(row.shape or "").strip().lower())
    if explicit in {"article", "social", "podcast"}:
        return explicit
    cls = fetcher_registry.get_class(row.fetcher_id) if row.fetcher_id else None
    if cls is not None:
        return str(getattr(cls, "content_shape", "article"))
    source_type = (row.source_type or "").strip().lower()
    if source_type in {"x", "twitter", "x_timeline", "social"}:
        return "social"
    if source_type in {"podcast", "podcast_rss"}:
        return "podcast"
    return "article"


def _legacy_digest(ids, evidence, rows):
    """早报 / 事件榜:有行看行,没行看类,都没有看条目类型。"""
    result = {}
    for source_id in ids:
        row, cls = rows.get(source_id), fetcher_registry.get_class(source_id)
        if row is not None:
            shape = _legacy_row_shape(row)
        elif cls is not None:
            shape = str(getattr(cls, "content_shape", "article") or "article")
        else:
            shape = _legacy_shape_of_content_type(evidence.get(source_id, ""))
        result[source_id] = (_legacy_role(source_id, row), shape)
    return result


def _legacy_assignment(session, ids, rows):
    """事件归并:形态与主体类优先,没有行也没有类的按条目数最多的形态。"""
    from sqlalchemy import func

    result = {}
    for source_id in ids:
        row, cls = rows.get(source_id), fetcher_registry.get_class(source_id)
        if row is not None:
            fallback_shape = _legacy_row_shape(row)
        elif cls is None:
            buckets: dict[str, int] = {}
            for ctype, count in session.exec(
                select(ArticleRecord.content_type, func.count())
                .where(ArticleRecord.source_id == source_id)
                .group_by(ArticleRecord.content_type)
            ).all():
                shape = _legacy_shape_of_content_type(ctype)
                buckets[shape] = buckets.get(shape, 0) + int(count or 0)
            fallback_shape = (
                max(buckets.items(), key=lambda item: (item[1], item[0] == "article"))[0] if buckets else "article"
            )
        else:
            fallback_shape = "article"
        shape = str(getattr(cls, "content_shape", "") or "").strip() if cls is not None else ""
        owner = str(getattr(cls, "source_owner", "") or "").strip() if cls is not None else ""
        if not owner and row is not None:
            owner = str(row.source_owner or "").strip()
        result[source_id] = (_legacy_role(source_id, row), shape or fallback_shape, owner)
    return result


# ── 消费方口径(改动前后都成立) ───────────────────────────────────────────────


@pytest.mark.parametrize("world", ["bare", "families"])
def test_consumer_role_shape_owner_match_the_pre_refactor_rules(tmp_path, world):
    from services import event_assignment, event_board, personal_digest

    sink = _bare(tmp_path) if world == "bare" else _families(tmp_path)[0]
    with Session(sink.engine) as session:
        ids, evidence, rows = _universe(session)
        expected = _legacy_digest(ids, evidence, rows)

        assert personal_digest._source_role_and_shape(session, evidence) == expected
        # 只给 id、不给条目类型:没有行也没有类的 id 一律按 article
        assert personal_digest._source_role_and_shape(session, ids) == _legacy_digest(ids, {}, rows)

        for reader_scope in (True, False):
            infos = event_board._source_infos(session, dict(evidence), reader_scope=reader_scope)
            assert {sid: (info.role, info.shape) for sid, info in infos.items()} == expected

        metas = event_assignment.load_source_meta(session, ids)
        assert {
            sid: (meta.role, meta.shape, meta.owner) for sid, meta in metas.items()
        } == _legacy_assignment(session, ids, rows)
    sink.engine.dispose()


def test_consumer_spot_checks_one_source_per_origin(tmp_path):
    """对照实现之外再钉几条字面值,免得两边错成一样。"""
    from services import event_assignment, event_board, personal_digest

    sink, installed, uninstalled = _families(tmp_path)
    with Session(sink.engine) as session:
        ids, evidence, _rows = _universe(session)
        digest = personal_digest._source_role_and_shape(session, evidence)
        metas = event_assignment.load_source_meta(session, ids)
        infos = event_board._source_infos(session, dict(evidence), reader_scope=True)
    sink.engine.dispose()

    assert digest["rss_openai_news"] == ("official", "article")          # 预设 + 镜像行
    assert digest["x_openai"] == ("official", "social")
    assert digest["rss_simonwillison"] == ("personal", "article")        # 收编后目录字段回到类属性
    assert digest["rss_admin_blog"] == ("media", "article")              # 管理员配置源
    assert digest["web_admin_site"] == ("official", "article")
    assert digest["x_admin_account"] == ("personal", "social")
    assert digest["leaderboard_src"] == ("leaderboard", "article")
    assert digest["empty_meta_src"] == ("media", "article")              # 没有策展元数据不给官方待遇
    assert digest["social_literal"] == ("media", "social")               # source_type=social
    assert digest["bulletin_shape"] == ("media", "article")              # 旧形态别名
    assert digest["podcast_admin_show"] == ("personal", "podcast")
    assert digest["user_rss_aaaaaaaaaaaa"] == ("media", "article")       # 读者源:不是 custom
    assert digest["x_pushed_account"] == ("media", "social")             # 收录临时源
    assert digest["x_import_only"] == ("media", "social")                # 回填的存量 id
    assert digest["dorami_daily_brief"] == ("media", "article")
    assert digest["x_openrouter"] == ("media", "social")                 # 墓碑
    assert digest[installed] == (digest[installed][0], "podcast")
    assert digest["zz_unknown_social"] == ("media", "social")            # 没行没类:看条目类型
    assert digest["zz_unknown_podcast"] == ("media", "podcast")
    assert digest["generic_rss"] == ("official", "article")              # 只有模板类、没有行(存量口径)
    # 播客目录里没装行、也没有条目的 id:角色来自目录,形态无证据按 article
    assert digest[uninstalled][1] == "article"
    # 上游同步来的预设行与本机类不同:角色跟行,名称与主体跟本机类
    assert digest["x_karpathy"] == ("media", "social")
    assert metas["x_karpathy"].owner == "andrej_karpathy"
    assert infos["x_karpathy"].name == "X · Andrej Karpathy"

    assert metas["rss_openai_news"].owner == "openai"
    assert metas["rss_admin_blog"].owner == "AdminCo"
    assert metas["dorami_daily_brief"].owner == "dorami"                 # 系统行
    assert metas["zz_unknown_social"].shape == "article"                 # 归并看库里的条目,不看调用方给的类型
    assert metas["x_import_only"].shape == "social"

    assert infos["web_pushed_site"].provisional and infos["x_pushed_account"].provisional
    assert not infos["rss_admin_blog"].provisional
    assert infos["user_rss_aaaaaaaaaaaa"].visible is False               # 读者私有源不进事件榜
    assert infos["rss_credentialed"].visible is False                    # 带凭证的公共源同样
    assert infos["rss_admin_blog"].visible is True


def test_consumer_dirty_source_ids_still_get_an_answer(tmp_path):
    """条目上的 source_id 带空白(脏数据)时,三个消费方都照样给出兜底结果,而不是抛错。"""
    from services import event_assignment, event_board, personal_digest

    sink = _bare(tmp_path)
    dirty = {" rss_openai_news ": "rss_article", " ": "social_post"}
    with Session(sink.engine) as session:
        assert personal_digest._source_role_and_shape(session, dirty) == {
            " rss_openai_news ": ("media", "article"), " ": ("media", "social"),
        }
        infos = event_board._source_infos(session, dict(dirty), reader_scope=True)
        assert {sid: (info.role, info.shape) for sid, info in infos.items()} == {
            " rss_openai_news ": ("media", "article"), " ": ("media", "social"),
        }
        metas = event_assignment.load_source_meta(session, list(dirty))
        assert {sid: (meta.role, meta.shape, meta.owner) for sid, meta in metas.items()} == {
            " rss_openai_news ": ("media", "article", ""), " ": ("media", "article", ""),
        }
    sink.engine.dispose()


def test_consumer_bare_world_spot_checks(tmp_path):
    from services import event_assignment, personal_digest

    sink = _bare(tmp_path)
    with Session(sink.engine) as session:
        ids, evidence, _rows = _universe(session)
        digest = personal_digest._source_role_and_shape(session, evidence)
        metas = event_assignment.load_source_meta(session, ids)
    sink.engine.dispose()
    assert digest["x_karpathy"] == ("personal", "social")                # 没有行:本机类说了算
    assert digest["x_import_only"] == ("media", "social")
    assert digest["x_openrouter"] == ("media", "social")                 # 墓碑没有行:看条目类型
    assert digest["docs_openai_api_changelog"] == ("media", "article")
    assert digest["dorami_daily_brief"] == ("media", "article")
    assert metas["dorami_daily_brief"].owner == ""                       # 没有行也没有类:不认主体
    assert metas["x_import_only"].shape == "social"                      # 3 条社交帖 > 1 篇文章
    assert metas["zz_unknown_social"].shape == "article"                 # 库里没有它的条目


def test_consumer_static_role_and_names(tmp_path):
    sink, installed, uninstalled = _families(tmp_path)
    with Session(sink.engine) as session:
        ids, _evidence, rows = _universe(session)
        batch = source_naming.friendly_source_names(ids, session)
    sink.engine.dispose()

    for source_id in ids:
        assert source_naming.source_role(source_id) == _legacy_role(source_id, None), source_id
    assert source_naming.source_role("") == "media"
    assert source_naming.source_role("rss_openai_news") == "official"
    assert source_naming.source_role("x_karpathy") == "personal"
    assert source_naming.source_role("generic_rss") == "official"
    assert source_naming.source_role("x_openrouter") == "media"
    assert source_naming.source_role("zz_unknown") == "media"
    # 调用方给了行上的元数据:以它为准,两项都空不给官方
    assert source_naming.source_role("rss_openai_news", source_scope="", provenance_tier="") == "media"
    assert source_naming.source_role("zz_unknown", source_scope="ai_media") == "media"
    assert source_naming.source_role("zz_unknown", source_scope="frontier_ai_lab") == "official"

    # 取名三级:预设源目录 → 兜底映射 → 源行 → 裸 id
    for source_id, cls in fetcher_registry._fetchers.items():
        assert source_naming._static_source_name(source_id) == cls.name
        assert batch[source_id] == cls.name
    assert batch["x_karpathy"] == "X · Andrej Karpathy"                  # 行上叫「上游版本的名字」
    assert batch["dorami_daily_brief"] == "哆啦美·AI资讯日报"
    assert batch["x_openrouter"] == "X · OpenRouter"
    assert batch["rss_admin_blog"] == "管理员博客"
    assert batch["user_rss_aaaaaaaaaaaa"] == "读者的源"
    assert batch[installed] == rows[installed].name
    assert batch[uninstalled] == uninstalled                             # 目录里有、库里没行:裸 id
    assert source_naming._static_source_name("rss_admin_blog") == ""
    assert source_naming._static_source_name("zz_unknown") == ""


def test_consumer_default_kinds_and_mcp_listing(tmp_path):
    import mcp_server
    from services import content_classification

    kinds = content_classification.source_default_kinds()
    expected = {
        sid: str(cls.default_kind).strip().lower()
        for sid, cls in fetcher_registry._fetchers.items()
        if str(getattr(cls, "default_kind", "") or "").strip().lower() in content_classification.ITEM_KIND_LABELS
    }
    expected.update(content_classification.SPECIAL_SOURCE_KINDS)
    assert kinds == expected
    assert kinds["generic_github_releases"] == "release"                 # 模板的先验也在
    assert "rss_openai_news" not in kinds

    sink = _bare(tmp_path)
    listed = mcp_server._list_sources_impl(sink)
    sink.engine.dispose()
    metadata = fetcher_registry.get_all_metadata()
    assert [item["source_id"] for item in listed] == [meta["id"] for meta in metadata]
    assert [item["name"] for item in listed] == [meta["name"] for meta in metadata]
    assert all(item["last_fetch_status"] is None for item in listed)


def test_consumer_feed_and_identity_lookups(tmp_path):
    from services import ingest_policy, ingest_push, opml, source_identity, source_rows, user_sources

    sink, installed, uninstalled = _families(tmp_path)
    with Session(sink.engine) as session:
        outline = {
            sid: opml._outline_for_source(session, sid)
            for sid in ("rss_openai_news", "rss_simonwillison", "x_openai", "web_anthropic_news",
                        "generic_rss", "rss_admin_blog", "podcast_admin_show", installed, uninstalled, "zz_unknown")
        }
        system_feeds = user_sources.system_feed_urls(session)
        x_index = source_identity.x_identity_index(session)
        web_index = ingest_push._web_index(session)
    sink.engine.dispose()

    # OPML:预设源用自己的 feed 地址与主页,没有 feed 的预设 / 模板 / 没行的 id 不导出
    assert outline["rss_openai_news"] == {
        "name": "OpenAI 新闻", "xml_url": "https://openai.com/news/rss.xml", "html_url": "", "podcast": False,
    }
    assert outline["rss_simonwillison"]["html_url"] == "https://simonwillison.net"
    assert outline["x_openai"] is None and outline["web_anthropic_news"] is None
    assert outline["generic_rss"] is None
    assert outline["rss_admin_blog"]["xml_url"] == "https://blog.admin.test/feed.xml"
    assert outline["podcast_admin_show"]["podcast"] is True
    assert outline[installed]["podcast"] is True
    assert outline[uninstalled] is None and outline["zz_unknown"] is None

    # 系统源撞库:全部带 feed 的预设(注册顺序,同地址后者覆盖前者)+ 公共 RSS / 播客行
    expected_presets = {}
    for source_id, cls in fetcher_registry._fetchers.items():
        feed_url = getattr(cls, "feed_url", "") or ""
        if feed_url and not getattr(cls, "is_template", False):
            expected_presets[user_sources.canonical_feed_url(feed_url)] = source_id
    assert expected_presets
    for url, source_id in expected_presets.items():
        if system_feeds[url]["source_id"] == source_id:   # 没被同地址的配置行覆盖
            assert system_feeds[url]["name"] == fetcher_registry.get_class(source_id).name
    assert system_feeds["https://openai.com/news/rss.xml"] == {
        "source_id": "rss_openai_news", "name": "OpenAI 新闻", "content_kind": "article",
    }
    assert system_feeds["https://blog.admin.test/feed.xml"]["source_id"] == "rss_admin_blog"
    assert system_feeds["https://pod.admin.test/feed.xml"]["content_kind"] == "podcast"
    assert not any(value["source_id"].startswith("user_rss_") for value in system_feeds.values())

    # X 身份键:预设的 handle / user_id 认回预设源,配置行与收录源各认各的
    registry_x = list(source_identity._registry_x_sources())
    assert registry_x == [
        (sid, str(getattr(cls, "handle", "") or ""), str(getattr(cls, "user_id", "") or ""))
        for sid, cls in fetcher_registry._fetchers.items()
        if not getattr(cls, "is_template", False) and str(getattr(cls, "platform", "") or "").lower() == "x"
    ]
    assert registry_x and all(handle for _sid, handle, _user_id in registry_x)
    for source_id, handle, user_id in registry_x:
        assert x_index[f"x:handle:{handle.lower()}"] == source_id
        if user_id:                                   # 个别预设还没登记稳定 user_id
            assert x_index[f"x:user_id:{user_id}"] == source_id
        assert source_rows._preset_identity_keys(source_id) == source_identity.social_identity_keys(
            "x", handle=handle, user_id=user_id
        )
    assert source_rows._preset_identity_keys("rss_openai_news") == []
    assert x_index["x:handle:adminhandle"] == "x_admin_account"
    assert x_index["x:user_id:777"] == "x_pushed_account"

    # 收录的站点索引:预设的 feed 地址与列表页前缀都在
    assert web_index.feeds["https://openai.com/news/rss.xml"] == "rss_openai_news"
    assert web_index.feeds["https://blog.admin.test/feed.xml"] == "rss_admin_blog"
    assert ("anthropic.com", "/news", "web_anthropic_news") in web_index.prefixes
    assert ("pushed.test", "/", "web_pushed_site") in web_index.prefixes

    # 保留 id:模板、下线源、日报、读者前缀
    templates = {sid for sid, cls in fetcher_registry._fetchers.items() if getattr(cls, "is_template", False)}
    assert ingest_policy._registry_template_ids() == templates
    assert ingest_policy.reserved_source_id_reason("generic_rss")
    assert ingest_policy.reserved_source_id_reason("x_openrouter")
    assert ingest_policy.reserved_source_id_reason("rss_openai_news") is None


def test_consumer_daily_job_and_x_probe_follow_registry_order(tmp_path):
    from ensure_daily_collection_job import selected_fetcher_ids

    from api.routers import x_api

    metadata = fetcher_registry.get_all_metadata()
    assert selected_fetcher_ids() == [
        meta["id"] for meta in metadata
        if not meta["is_template"] and meta["admission_status"] != "incubating"
    ]
    assert selected_fetcher_ids(include_advanced=True) == [meta["id"] for meta in metadata]
    assert "rss_openai_news" not in selected_fetcher_ids(exclude_fetchers={"rss_openai_news"})

    # 没有任何缓存与抓取状态时,探针取注册顺序里第一个带稳定 user_id 的 X 预设
    first = next(
        (meta["id"], fetcher_registry.get_class(meta["id"]).user_id)
        for meta in metadata
        if meta.get("platform") == "x" and not meta.get("is_template")
        and str(getattr(fetcher_registry.get_class(meta["id"]), "user_id", "")).isdigit()
    )
    sink = _bare(tmp_path)
    with Session(sink.engine) as session:
        probe = x_api._probe_resource(session)
    sink.engine.dispose()
    assert (probe["source_id"], probe["resource_id"]) == first
    assert probe["resource_type"] == "user"


# ── 读服务自身的接口(本波新增) ───────────────────────────────────────────────


def test_catalog_preset_views_keep_registry_order_and_expose_bindings():
    from services import source_catalog

    everything = source_catalog.preset_views(include_templates=True)
    assert list(everything) == list(fetcher_registry._fetchers)          # 注册顺序,不排序
    presets = source_catalog.preset_views()
    assert list(presets) == [
        sid for sid, cls in fetcher_registry._fetchers.items() if not getattr(cls, "is_template", False)
    ]
    assert all(view.registered and not view.configured for view in everything.values())
    assert all(view.name for view in everything.values())                # 取名口径依赖:每个类都有名字

    assert source_catalog.preset_binding("rss_openai_news")["feed_url"] == "https://openai.com/news/rss.xml"
    x = source_catalog.preset_binding("x_openai")
    assert x["handle"] and x["user_id"].isdigit()
    assert source_catalog.preset_binding("zz_unknown") == {}
    assert source_catalog.preset_binding("") == {}

    assert source_catalog.preset_metadata() == fetcher_registry.get_all_metadata()
    assert source_catalog.preset_metadata() is not source_catalog.preset_metadata()   # 每次新对象
    assert source_catalog.fetcher_class("generic_rss") is fetcher_registry.get_class("generic_rss")
    assert source_catalog.fetcher_class("") is None and source_catalog.fetcher_class("nope") is None
    assert source_catalog.podcast_catalog_source(sorted(catalog_by_id())[0]) is not None
    assert source_catalog.podcast_catalog_source("rss_openai_news") is None

    assert source_catalog.template_ids() == {
        sid for sid, cls in fetcher_registry._fetchers.items() if getattr(cls, "is_template", False)
    }
    assert source_catalog.template_ids() and "rss_openai_news" not in source_catalog.template_ids()
    kinds = source_catalog.preset_default_kinds()
    assert list(kinds) == list(fetcher_registry._fetchers)
    assert kinds["generic_github_releases"] == "release" and kinds["rss_openai_news"] == ""


def test_catalog_views_follow_live_class_attributes(monkeypatch):
    """视图按类缓存,但取用时对照类上的当前属性:临时改写类属性不会留下陈旧视图。"""
    from services import source_catalog

    cls = fetcher_registry.get_class("x_openai")
    before = source_catalog.registry_view("x_openai")
    assert source_catalog.registry_view("x_openai") is before            # 属性没变:同一个对象
    assert before.admission_status == "incubating"

    with monkeypatch.context() as patch:
        patch.setattr(cls, "admission_status", "admitted", raising=False)
        patch.setattr(cls, "name", "临时改名", raising=False)
        patched = source_catalog.registry_view("x_openai")
        assert patched.admission_status == "admitted" and patched.daily is True
        assert patched.name == "临时改名"
        assert source_catalog.preset_views()["x_openai"].name == "临时改名"
        assert source_naming.friendly_source_name("x_openai") == "临时改名"

    after = source_catalog.registry_view("x_openai")
    assert after.admission_status == "incubating" and after.name == before.name
    assert source_naming.friendly_source_name("x_openai") == before.name


def test_catalog_describe_reuses_rows_and_applies_evidence_only_without_row_or_class(tmp_path):
    from services import source_catalog

    sink, installed, uninstalled = _families(tmp_path)
    statements: list[str] = []

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with Session(sink.engine) as session:
        wanted = ["rss_openai_news", "rss_admin_blog", "x_openrouter", uninstalled, "zz_unknown"]
        rows = {
            row.source_id: row for row in session.exec(
                select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(wanted))
            ).all()
        }
        loaded = source_catalog.describe(session, wanted)
        event.listen(sink.engine, "before_cursor_execute", count)
        try:
            reused = source_catalog.describe(session, wanted, rows=rows)
        finally:
            event.remove(sink.engine, "before_cursor_execute", count)
        assert statements == []                                          # 给了行就不再查库
        assert reused == loaded

        # 证据只对没有行也没有类的 id 生效
        evidence = {sid: "social_post" for sid in wanted}
        with_evidence = source_catalog.describe(session, wanted, content_types=evidence)
        assert with_evidence["rss_openai_news"].shape == "article"       # 有类有行:不看证据
        assert with_evidence["rss_admin_blog"].shape == "article"        # 有行:不看证据
        assert with_evidence["x_openrouter"].shape == "social"           # 墓碑有行(对账建的)
        assert with_evidence[uninstalled].shape == "social"              # 内建目录 id、没有行:证据优先
        assert with_evidence["zz_unknown"].shape == "social"
        assert loaded[uninstalled].shape == "podcast"                    # 没给证据:内建视图自带的形态
        assert source_catalog.describe(session, [uninstalled], content_types={uninstalled: ""})[uninstalled].shape == "article"
        assert source_catalog.get(session, uninstalled).shape == "podcast"
        assert source_catalog.get(session, "zz_unknown", content_type="podcast_episode").shape == "podcast"
        assert loaded["zz_unknown"].origin == source_catalog.ORIGIN_ARCHIVE and not loaded["zz_unknown"].known

        # 键按调用方给的原样:带空白的脏 id 也有视图(占位),不会让按条目回查的消费方 KeyError
        dirty = source_catalog.describe(session, [" rss_openai_news ", " ", "", None, "rss_openai_news"])
        assert set(dirty) == {" rss_openai_news ", " ", "rss_openai_news"}
        assert not dirty[" rss_openai_news "].known and dirty["rss_openai_news"].registered
    sink.engine.dispose()


def test_catalog_compat_merge_takes_shape_and_curation_scope_from_the_row(tmp_path):
    """类与行并存:形态、策展范围、信源层级取行(改前早报 / 事件榜 / 阅读器目录的口径),其余取类;
    事件归并原先形态取类,本波起与另外两家一致——只在上游同步来的行与本机类不同时分得出先后。"""
    from services import event_assignment, event_board, personal_digest, source_catalog

    sink, _installed, _uninstalled = _families(tmp_path)
    with Session(sink.engine) as session:
        # 上游把 rss_hf_blog 改成了播客形态、换了策展范围;本机类还是旧的
        row = session.get(SourceConfigRecord, "rss_hf_blog")
        assert row is not None and row.origin == "catalog"
        row.collection_authority_id = "authority-up"
        row.shape = "podcast"
        row.source_scope = "personal_commentary"
        row.source_owner = "UpstreamOwner"
        row.name = "上游改的名字"
        session.add(row)
        session.commit()

        view = source_catalog.describe(session, ["rss_hf_blog"])["rss_hf_blog"]
        cls = fetcher_registry.get_class("rss_hf_blog")
        assert (view.shape, view.source_scope) == ("podcast", "personal_commentary")
        assert view.name == cls.name and view.source_owner == cls.source_owner
        assert view.synced and view.configured and view.registered
        assert source_catalog.info_role(view) == "personal"

        assert personal_digest._source_role_and_shape(session, ["rss_hf_blog"])["rss_hf_blog"] == ("personal", "podcast")
        info = event_board._source_infos(session, {"rss_hf_blog": "rss_article"}, reader_scope=False)["rss_hf_blog"]
        assert (info.role, info.shape, info.name) == ("personal", "podcast", cls.name)
        meta = event_assignment.load_source_meta(session, ["rss_hf_blog"])["rss_hf_blog"]
        assert (meta.role, meta.shape, meta.owner) == ("personal", "podcast", cls.source_owner)

        # 类上没写主体时才用行上的
        monkey_owner = source_catalog._merge(
            source_catalog.SourceView(source_id="p", origin="catalog", name="P", registered=True),
            source_catalog.SourceView(source_id="p", origin="catalog", name="Q", source_owner="RowOwner", configured=True),
        )
        assert monkey_owner.source_owner == "RowOwner" and monkey_owner.name == "P"
    sink.engine.dispose()


def test_catalog_full_model_reads_identity_from_the_row(tmp_path):
    from services import source_catalog

    sink, _installed, _uninstalled = _families(tmp_path)
    try:
        with Session(sink.engine) as session:
            compat = source_catalog.describe(session, ["x_karpathy"])["x_karpathy"]
            source_catalog.configure(model="full")
            full = source_catalog.describe(session, ["x_karpathy"])["x_karpathy"]
        assert compat.name == "X · Andrej Karpathy" and compat.source_owner == "andrej_karpathy"
        assert full.name == "上游版本的名字" and full.source_owner == "UpstreamOwner"
        assert compat.source_scope == full.source_scope == "ai_media"
    finally:
        source_catalog.configure(model="compat")
        sink.engine.dispose()


def test_catalog_row_shape_is_one_implementation():
    from api import sources as api_sources
    from services import source_catalog

    cases = [
        (dict(source_type="social"), "social"),
        (dict(source_type="twitter"), "social"),
        (dict(source_type="podcast_rss"), "podcast"),
        (dict(source_type="rss", shape="bulletin"), "article"),
        (dict(source_type="rss", shape="Podcast "), "podcast"),
        (dict(source_type="preset", fetcher_id="x_openai"), "social"),
        (dict(source_type="x", fetcher_id="generic_rss"), "article"),     # 绑定的类优先于类型
        (dict(source_type="wechat"), "article"),
    ]
    for kwargs, expected in cases:
        row = _row("shape_case", **kwargs)
        assert source_catalog.row_shape(row) == expected, kwargs
        assert api_sources.row_shape(row) == expected, kwargs
        assert source_catalog.view_from_row(row).shape == expected, kwargs
    assert api_sources.normalize_shape("bulletin") == source_catalog.normalize_shape("bulletin") == "article"
    assert api_sources.normalize_shape(None) == "" and source_catalog.normalize_shape(" X ") == ""
    assert api_sources.configured_source_content_type("social", "") == "social_post"
    assert api_sources.configured_source_content_type("rss", "x_openai") == "social_post"
    assert api_sources.configured_source_platform("social", "") == ""
    assert api_sources.configured_source_platform("x", "") == "x"
    assert api_sources.configured_source_platform("x", "generic_rss") == ""
    assert set(api_sources._registry_source_meta()) == set(fetcher_registry._fetchers)


def test_catalog_info_role_keeps_reader_sources_out_of_custom(tmp_path):
    from services import source_catalog

    sink, _installed, _uninstalled = _families(tmp_path)
    with Session(sink.engine) as session:
        views = source_catalog.describe(session, ["user_rss_aaaaaaaaaaaa", "generic_rss", "x_openrouter"])
    sink.engine.dispose()
    reader = views["user_rss_aaaaaaaaaaaa"]
    assert reader.role == "custom"                                       # 阅读器目录的分组
    assert source_catalog.info_role(reader) == "media"                   # 评分 / 早报 / 事件层的角色
    assert source_catalog.info_role(views["generic_rss"]) == "official"  # 只有模板类、没有行
    assert source_catalog.info_role(views["x_openrouter"]) == "media"
