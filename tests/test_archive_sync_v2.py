import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest
import httpx
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    ArchiveSyncClockRecord,
    ArchiveSyncEntityStateRecord,
    CmsTagCandidateRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagAliasRecord,
    CmsTagRecord,
    CollectionJobRunRecord,
    FetchRunRecord,
    RemoteCandidateEvidenceRecord,
    MediaAssetRecord,
    SourceConfigRecord,
    SourceStateRecord,
    TaxonomyVersionRecord,
)
from services import archive_sync_v2  # noqa: E402
from services import remote_sync as remote_sync_service  # noqa: E402
from services.article_analysis import compute_content_hash, queue_article_analysis  # noqa: E402
from services.media_store import extract_image_urls  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _sink(tmp_path, name):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _source(source_id="rss_platform", **overrides):
    data = dict(
        source_id=source_id,
        name="Platform",
        source_type="rss",
        url="https://example.test/feed.xml",
        category="official",
        fetcher_id="generic_rss",
        owner_username="",
        ai_analysis_enabled=True,
        is_active=True,
        fetch_interval_minutes=60,
        params_json='{"feed_url":"https://example.test/feed.xml"}',
        created_at="2026-09-01T00:00:00+00:00",
        updated_at="2026-09-02T00:00:00+00:00",
    )
    data.update(overrides)
    return SourceConfigRecord(**data)


def _article(article_id="article-1", **overrides):
    data = dict(
        id=article_id,
        title="Article",
        content_type="rss_article",
        source_id="rss_platform",
        source_url="https://example.test/a",
        publish_date="2026-09-01T00:00:00+00:00",
        fetched_date="2026-09-01T01:00:00+00:00",
        archive_updated_at="2026-09-02T01:00:00+00:00",
        has_content=True,
        content="Full body",
        extensions_json="{}",
    )
    data.update(overrides)
    return ArticleRecord(**data)


def _copy_stream(producer, consumer, stream, *, limit=1000):
    raw = archive_sync_v2.export_page(
        producer.engine,
        stream,
        limit=limit,
    )
    result = archive_sync_v2.import_page(consumer.engine, raw, expected_stream=stream)
    return raw, result


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_article_jsonl_preserves_unicode_line_separators(tmp_path, separator, newline):
    producer = _sink(tmp_path, "producer-unicode.db")
    consumer = _sink(tmp_path, "consumer-unicode.db")
    body = f"First paragraph{separator}第二段\nThird paragraph\r\nFourth paragraph"
    with Session(producer.engine) as session:
        session.add(_article("article-1"))
        session.commit()
        session.add(_article("article-2", content=body))
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, "articles")
    wire = raw.replace("\n", newline)
    result = archive_sync_v2.import_page(consumer.engine, wire, expected_stream="articles")
    assert result["inserted"] == 2
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "article-2").content == body

    # A genuinely broken JSON record must still reject the whole page before writing.
    broken_consumer = _sink(tmp_path, "consumer-broken-json.db")
    with pytest.raises(archive_sync_v2.SyncV2Error, match="line 3: invalid JSON"):
        archive_sync_v2.import_page(
            broken_consumer.engine, wire.rstrip()[:-1], expected_stream="articles",
        )
    with Session(broken_consumer.engine) as session:
        assert session.exec(select(ArticleRecord)).all() == []


def test_synced_source_is_created_with_analysis_off_and_no_binding(tmp_path):
    """issue #263:同步建出来的源默认关分析、不带采集绑定;启用状态等目录字段照上游。"""
    producer = _sink(tmp_path, "producer-source.db")
    consumer = _sink(tmp_path, "consumer-source.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()

    _copy_stream(producer, consumer, "sources")
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        assert row.is_active is True  # 目录字段:个人早报的范围与 readiness 靠它
        assert row.collection_authority_id == authority  # 簿记:这个源上游也有
        assert row.ai_analysis_enabled is False
        assert (row.fetcher_id, row.params_json) == ("", "{}")
        # 管理员在本机打开分析:之后的同步不再覆盖这个开关
        row.ai_analysis_enabled = True
        session.add(row)
        session.commit()

    with Session(producer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        row.name = "Renamed upstream"
        row.is_active = False
        row.ai_analysis_enabled = False
        row.fetch_interval_minutes = 99
        row.updated_at = "2026-09-03T00:00:00+00:00"
        session.add(row)
        session.commit()
    _copy_stream(producer, consumer, "sources")
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        assert row.name == "Renamed upstream"
        assert row.is_active is False  # 本机没在用这个源:停用照上游
        assert row.ai_analysis_enabled is True  # 本机的开关不被同步覆盖
        assert row.fetch_interval_minutes == 60  # 本机运行参数,只在建行时取上游的


def test_first_upstream_marker_turns_analysis_off_only_for_sources_not_in_local_use(tmp_path):
    """新节点启动对账先建了预设行(分析默认开),随后才接入上游:上游印记第一次落上去时,本机没在用
    的源与「同步建出来的源」同一口径——分析关;本机在用的源不动。之后管理员打开的开关,同步不再碰。"""
    from models.db import CollectionJobRecord
    from services import source_catalog

    idle_id, in_job_id = source_catalog.registry_source_ids()[:2]
    producer = _sink(tmp_path, "producer-first-marker.db")
    consumer = _sink(tmp_path, "consumer-first-marker.db")
    with Session(producer.engine) as session:
        for source_id in (idle_id, in_job_id):
            row = session.get(SourceConfigRecord, source_id) or _source(source_id)
            row.owner_username = ""
            session.add(row)
        session.commit()
    with Session(consumer.engine) as session:
        # 预设镜像行的样子:绑定 = 同名预设抓取器;本机没跑过、没被任务点名
        for source_id in (idle_id, in_job_id):
            row = session.get(SourceConfigRecord, source_id) or _source(source_id)
            row.fetcher_id, row.origin, row.ai_analysis_enabled = source_id, "catalog", True
            row.collection_authority_id = ""
            session.add(row)
        session.add(CollectionJobRecord(
            name="mine", fetcher_ids_json=json.dumps([in_job_id]), is_active=True,
            created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    _copy_stream(producer, consumer, "sources")
    with Session(consumer.engine) as session:
        idle = session.get(SourceConfigRecord, idle_id)
        in_job = session.get(SourceConfigRecord, in_job_id)
        assert (idle.ai_analysis_enabled, idle.collection_authority_id) == (False, authority)
        assert (in_job.ai_analysis_enabled, in_job.collection_authority_id) == (True, authority)
        assert idle.fetcher_id == idle_id            # 本机的绑定不动
        idle.ai_analysis_enabled = True               # 管理员之后打开
        session.add(idle)
        session.commit()

    with Session(producer.engine) as session:
        row = session.get(SourceConfigRecord, idle_id)
        row.name = "Renamed upstream"
        session.add(row)
        session.commit()
    _copy_stream(producer, consumer, "sources")
    with Session(consumer.engine) as session:
        idle = session.get(SourceConfigRecord, idle_id)
        assert (idle.name, idle.ai_analysis_enabled) == ("Renamed upstream", True)


def test_admin_saved_binding_stays_protected_after_sync_overwrites_the_origin_field(tmp_path):
    """检视 R1 #2:本机存过采集绑定、还没跑过也没进任务的源算「在用」。第一次同步会把目录字段
    ``origin`` 盖成上游的值(catalog);保护不能因此在第二次同步时失效。"""
    producer = _sink(tmp_path, "producer-origin.db")
    consumer = _sink(tmp_path, "consumer-origin.db")
    with Session(producer.engine) as session:
        session.add(_source("rss_shared", origin="catalog", fetcher_id="rss_shared"))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source("rss_shared", origin="admin", name="My own binding"))   # 绑定 generic_rss
        session.commit()

    def sync_sources():
        # 目录投影(新版本两端的常态):载荷里带 origin 等目录字段
        raw = archive_sync_v2.export_page(
            producer.engine, "sources", receiver_capabilities=archive_sync_v2.SOURCE_CATALOG_CAPABILITY
        )
        return archive_sync_v2.import_page(consumer.engine, raw, expected_stream="sources")

    sync_sources()
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_shared")
        assert (row.origin, row.fetcher_id, row.is_active, row.ai_analysis_enabled) == (
            "catalog", "generic_rss", True, True
        )
        assert archive_sync_v2.source_in_local_use(session, row, archive_sync_v2._PageOutcome())  # noqa: SLF001

    with Session(producer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_shared")
        row.is_active = False
        row.retired_at = "2026-11-01T00:00:00+00:00"
        session.add(row)
        session.commit()
    sync_sources()
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_shared")
        assert (row.is_active, row.retired_at) == (True, None)

    with Session(producer.engine) as session:
        session.delete(session.get(SourceConfigRecord, "rss_shared"))
        session.commit()
    sync_sources()
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_shared")
        assert row is not None and row.is_active and row.fetcher_id == "generic_rss"


def test_auto_installed_bindings_do_not_count_as_local_use():
    """预设镜像行(绑定 = 同名预设抓取器)与播客目录自动安装的行不算管理员存的绑定;判断只看绑定
    本身,不看可被同步覆盖的 origin。"""
    from services import podcast_catalog, source_catalog

    outcome = archive_sync_v2._PageOutcome()  # noqa: SLF001
    preset = source_catalog.registry_source_ids()[0]
    podcast = next(iter(podcast_catalog.catalog_by_id()))
    assert outcome.binding_is_auto_generated(preset, preset) is True
    assert outcome.binding_is_auto_generated(podcast, "generic_podcast_rss") is True
    assert outcome.binding_is_auto_generated(preset, "generic_rss") is False       # 管理员改绑
    assert outcome.binding_is_auto_generated("rss_my_feed", "generic_rss") is False


def test_import_takes_the_write_lock_before_comparing_with_the_local_version(tmp_path, monkeypatch):
    """检视 R1 #1:「读本机版本 → 比较 → 覆盖」之间不能让本机写事务插进来。导入一页先取写锁;
    比较发生时,别的连接上的写必须在等锁,而不是已经提交。"""
    producer = _sink(tmp_path, "producer-race.db")
    consumer = _sink(tmp_path, "consumer-race.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_article(content="Upstream body"))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source())
        session.add(_article(content="Local body", archive_updated_at="2026-09-01T00:00:00+00:00"))
        session.commit()
    _copy_stream(producer, consumer, "sources")

    observed = {}
    real_compare = archive_sync_v2.revision_newer

    def local_fetch_commits_a_newer_version():
        try:
            with Session(consumer.engine) as session:
                row = session.get(ArticleRecord, "article-1")
                row.content = "Local newer body"
                row.archive_updated_at = "2026-12-01T00:00:00+00:00"
                session.add(row)
                session.commit()
            observed["writer"] = "committed"
        except Exception as exc:  # noqa: BLE001 - 只记录结果
            observed["writer"] = type(exc).__name__

    def compare_while_a_local_writer_races(candidate, other):
        if "blocked_during_compare" not in observed:
            writer = threading.Thread(target=local_fetch_commits_a_newer_version)
            writer.start()
            writer.join(timeout=0.6)
            observed["blocked_during_compare"] = writer.is_alive()
            observed["thread"] = writer
        return real_compare(candidate, other)

    monkeypatch.setattr(archive_sync_v2, "revision_newer", compare_while_a_local_writer_races)
    _raw, applied = _copy_stream(producer, consumer, "articles")
    observed["thread"].join(timeout=10)

    assert observed["blocked_during_compare"] is True       # 比较时本机写事务在等锁,没有插进来
    assert applied["taken_over"] == 1                        # 导入按它读到的本机版本做了决定
    assert observed["writer"] == "committed"                 # 导入提交后它才写进去


def test_fetch_rechecks_under_the_write_lock_before_rewriting_an_existing_row(tmp_path, monkeypatch):
    """检视 R1 #1 的另一半:抓取读到「本机版本、没有正文」之后、写回之前,同步把这篇换成了上游的
    版本。抓取取锁后重读、按最新的行重新判定——不会留下「上游的印记 + 本机的正文」。"""
    from api.articles_view import GenericContent

    consumer = _sink(tmp_path, "consumer-fetch-race.db")
    with Session(consumer.engine) as session:
        session.add(_source())
        session.add(_article(has_content=False, content=""))
        session.commit()

    real_lock = DatabaseStorage._lock_existing_for_rewrite  # noqa: SLF001

    def sync_lands_first(session, existing):
        with Session(consumer.engine) as other:
            row = other.get(ArticleRecord, "article-1")
            row.has_content, row.content = True, "Upstream body"
            row.analysis_authority_id = "upstream-a"
            other.add(row)
            other.commit()
        return real_lock(session, existing)

    monkeypatch.setattr(DatabaseStorage, "_lock_existing_for_rewrite", staticmethod(sync_lands_first))
    item = GenericContent(
        id="article-1", title="Article", source_url="https://example.test/a",
        publish_date="2026-09-01T00:00:00+00:00", content="Local fetched body", has_content=True,
    )
    item.source_id = "rss_platform"
    item.content_type = "rss_article"
    outcome = asyncio.run(consumer.save_with_outcome(item))

    assert outcome == "duplicate"
    with Session(consumer.engine) as session:
        row = session.get(ArticleRecord, "article-1")
        assert (row.content, row.analysis_authority_id) == ("Upstream body", "upstream-a")


@pytest.mark.parametrize("usage", ["saved_binding", "scheduled", "ran_locally"])
def test_source_in_local_use_ignores_upstream_stop_retire_and_delete(tmp_path, usage):
    """本机在用的源(存过绑定 / 被采集任务点名 / 本机跑过)不接受上游的停用、退役与删除;
    目录字段照常更新,本机的分析开关与绑定原样保留。"""
    from models.db import CollectionJobRecord

    producer = _sink(tmp_path, f"producer-in-use-{usage}.db")
    consumer = _sink(tmp_path, f"consumer-in-use-{usage}.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()
    with Session(consumer.engine) as session:
        local = _source(ai_analysis_enabled=True, name="Local name")
        if usage != "saved_binding":
            local.fetcher_id = ""
            local.params_json = "{}"
        session.add(local)
        if usage == "scheduled":
            session.add(CollectionJobRecord(
                name="mine", fetcher_ids_json='["rss_platform"]', is_active=False,
                created_at="2026-09-01T00:00:00", updated_at="2026-09-01T00:00:00",
            ))
        if usage == "ran_locally":
            session.add(SourceStateRecord(
                source_id="rss_platform", fetcher_id="generic_rss", status="healthy",
                total_runs=3, updated_at="2026-09-05T00:00:00+00:00",
            ))
        session.commit()
        binding_before = (local.fetcher_id, local.params_json)

    first = archive_sync_v2.export_page(producer.engine, "sources")
    since = archive_sync_v2.parse_page(first, expected_stream="sources")[0]["snapshot"]
    archive_sync_v2.import_page(consumer.engine, first, expected_stream="sources")
    with Session(producer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        row.name = "Upstream name"
        row.is_active = False
        row.retired_at = "2026-09-04T00:00:00+00:00"
        row.updated_at = "2026-09-04T00:00:00+00:00"
        session.add(row)
        session.commit()
    archive_sync_v2.import_page(
        consumer.engine,
        archive_sync_v2.export_page(
            producer.engine, "sources", since=since,
            receiver_capabilities=[archive_sync_v2.SOURCE_CATALOG_CAPABILITY],
        ),
        expected_stream="sources",
    )
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        assert row.name == "Upstream name"
        assert row.is_active is True and row.retired_at is None
        assert row.ai_analysis_enabled is True
        assert (row.fetcher_id, row.params_json) == binding_before

    # 上游把它物理删了:本机继续用,只摘掉「上游也有」的簿记
    with Session(producer.engine) as session:
        latest = archive_sync_v2.parse_page(
            archive_sync_v2.export_page(producer.engine, "sources"), expected_stream="sources"
        )[0]["snapshot"]
        session.delete(session.get(SourceConfigRecord, "rss_platform"))
        session.commit()
    delta = archive_sync_v2.export_page(producer.engine, "sources", since=latest)
    assert [row["operation"] for row in archive_sync_v2.parse_page(delta)[1]] == ["tombstone"]
    applied = archive_sync_v2.import_page(consumer.engine, delta, expected_stream="sources")
    assert applied["deleted"] == 0 and applied["skipped"] == 1
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        assert row is not None and row.retired_at is None
        assert row.collection_authority_id == ""


@pytest.mark.parametrize("has_articles", [True, False], ids=["referenced", "unreferenced"])
def test_upstream_source_delete_retires_or_removes_an_unused_receiver_copy(tmp_path, has_articles):
    """本机没在用的同步源:上游删除后,本机有它的文章就保留身份并退役,没有文章才删行。"""
    producer = _sink(tmp_path, f"producer-source-delete-{has_articles}.db")
    consumer = _sink(tmp_path, f"consumer-source-delete-{has_articles}.db")
    with Session(producer.engine) as session:
        session.add(_source())
        if has_articles:
            session.add(_article())
        session.add(SourceStateRecord(
            source_id="rss_platform",
            fetcher_id="rss_platform",
            status="healthy",
            updated_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()

    checkpoints = {}
    for stream in ("sources", "articles", "source_states"):
        first_page = archive_sync_v2.export_page(producer.engine, stream)
        first_manifest, _ = archive_sync_v2.parse_page(
            first_page, expected_stream=stream
        )
        checkpoints[stream] = first_manifest["snapshot"]
        archive_sync_v2.import_page(consumer.engine, first_page, expected_stream=stream)
    with Session(consumer.engine) as session:
        assert session.get(SourceConfigRecord, "rss_platform") is not None
        assert session.get(SourceStateRecord, "rss_platform") is not None

    with Session(producer.engine) as session:
        session.delete(session.get(SourceConfigRecord, "rss_platform"))
        session.commit()
        if has_articles:
            article = session.get(ArticleRecord, "article-1")
            article.content = "updated after source delete"
            session.add(article)
            session.commit()

    for stream in ("sources", "articles", "source_states"):
        delta = archive_sync_v2.export_page(
            producer.engine, stream, since=checkpoints[stream]
        )
        _manifest, rows = archive_sync_v2.parse_page(delta, expected_stream=stream)
        if stream in {"sources", "source_states"}:
            assert len(rows) == 1 and rows[0]["operation"] == "tombstone"
        archive_sync_v2.import_page(consumer.engine, delta, expected_stream=stream)
    with Session(consumer.engine) as session:
        source = session.get(SourceConfigRecord, "rss_platform")
        assert session.get(SourceStateRecord, "rss_platform") is None
        if has_articles:
            assert source is not None and source.retired_at
            article = session.get(ArticleRecord, "article-1")
            assert article is not None and article.content == "updated after source delete"
        else:
            assert source is None
        # 删除继续往下游转发:接收端自己的导出里这是一条 tombstone
        forwarded = archive_sync_v2.parse_page(
            archive_sync_v2.export_page(consumer.engine, "source_states"),
            expected_stream="source_states",
        )[1]
        assert [(row["identity"], row["operation"]) for row in forwarded] == [
            ("rss_platform", "tombstone")
        ]


def test_upstream_marker_on_a_source_does_not_abort_local_collection(tmp_path, monkeypatch):
    """源上的同步印记只是簿记(上游也有这个源):本机采集途中它被盖上,本次采集照常完成,
    抓到的文章是本机版本。"""
    consumer = _sink(tmp_path, "midflight-marker.db")
    with Session(consumer.engine) as session:
        session.add(_source())
        session.add(_article("preexisting"))
        session.commit()

    import api.app as app_module
    monkeypatch.setattr(app_module, "db_sink", consumer)
    monkeypatch.setattr(app_module, "queue_article_analysis_after_commit", lambda _ids: 0)
    monkeypatch.setattr(app_module, "schedule_media_prefetch", lambda _ids: None)

    async def fake_run_task(_fetcher, *, lineage, **_params):
        with Session(consumer.engine) as session:
            session.add(_article("created-midflight", fetch_run_id=lineage["fetch_run_id"]))
            source = session.get(SourceConfigRecord, "rss_platform")
            source.collection_authority_id = "external"
            session.add(source)
            session.commit()
        return SimpleNamespace(
            fetched_count=1, saved_count=1, skipped_count=0,
            saved_content_ids=["created-midflight"],
        )

    monkeypatch.setattr(app_module.pipeline, "run_task", fake_run_task)
    asyncio.run(app_module.run_single_fetch_as_collection(
        "generic_rss", {"source_id": "rss_platform"}, "no-race"
    ))
    with Session(consumer.engine) as session:
        created = session.get(ArticleRecord, "created-midflight")
        assert created is not None and created.analysis_authority_id == ""
        assert session.get(ArticleRecord, "preexisting") is not None
        assert session.exec(select(FetchRunRecord)).one().status == "success"
        state = session.get(SourceStateRecord, "rss_platform")
        assert state.authority_id == "" and state.total_runs == 1
        # 上游也有的源下,本机自己抓到的文章照样进本机的导出
        exported = archive_sync_v2.parse_page(
            archive_sync_v2.export_page(consumer.engine, "articles"), expected_stream="articles"
        )[1]
        assert {row["identity"] for row in exported} == {"created-midflight", "preexisting"}


def test_synced_source_is_locally_manageable_but_synced_articles_stay_read_only(tmp_path, monkeypatch):
    """拉取别人不再限制本机管理公共源(issue #263);同步写入的文章仍不能在本机改写或删除——
    它的当前版本是上游的,改了也会被下一次同步覆盖。"""
    sink = _sink(tmp_path, "admin-authority-fence.db")
    with Session(sink.engine) as session:
        session.add(_source(collection_authority_id="external"))
        session.add(_article(analysis_authority_id="external"))
        session.commit()

    from fastapi import HTTPException
    from api.routers import articles as articles_router
    from api.routers import source_configs as sources_router
    from api.schemas import BatchOpParams

    with Session(sink.engine) as session:
        updated = sources_router.update_source_config(
            "rss_platform", sources_router.SourceConfigUpdate(name="changed"), session
        )
        assert updated["name"] == "changed"
        toggled = sources_router.toggle_source_config("rss_platform", False, session)
        assert toggled["is_active"] is False

    monkeypatch.setattr(articles_router.deps, "get_db_sink", lambda: sink)
    monkeypatch.setattr(
        articles_router, "_app", lambda: SimpleNamespace(queue_article_analysis_after_commit=lambda _ids: None)
    )
    with pytest.raises(HTTPException) as update_article:
        asyncio.run(articles_router.update_article(
            "article-1", articles_router.ArticleUpdateParams(title="changed")
        ))
    assert update_article.value.status_code == 409
    with pytest.raises(HTTPException) as delete_article:
        asyncio.run(articles_router.delete_article("article-1"))
    assert delete_article.value.status_code == 409
    with pytest.raises(HTTPException) as batch_delete:
        asyncio.run(articles_router.batch_delete_articles(BatchOpParams(ids=["article-1"])))
    assert batch_delete.value.status_code == 409
    with pytest.raises(HTTPException) as update_extensions:
        asyncio.run(articles_router.update_article(
            "article-1",
            articles_router.ArticleUpdateParams(extensions_json='{"reader_cache":true}'),
        ))
    assert update_extensions.value.status_code == 409


def test_article_authority_suppresses_queue_and_fences_old_lease(tmp_path):
    producer = _sink(tmp_path, "producer-article.db")
    consumer = _sink(tmp_path, "consumer-article.db")
    with Session(producer.engine) as session:
        session.add(_article())
        session.commit()
    with Session(consumer.engine) as session:
        old = _article(archive_updated_at="2026-09-01T01:00:00+00:00", content="Old")
        session.add(old)
        session.commit()
        assert queue_article_analysis(session, old.id) == "created"
        session.commit()

    _copy_stream(producer, consumer, "articles")
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert article.content == "Full body"
        assert article.analysis_authority_id == authority
        assert analysis.status == "pending"
        assert analysis.lease_owner is None
        assert queue_article_analysis(session, article.id, force=True) == "remote_authority"


def test_article_handoff_keeps_same_content_result_until_remote_revision_arrives(tmp_path):
    producer = _sink(tmp_path, "producer-handoff.db")
    consumer = _sink(tmp_path, "consumer-handoff.db")
    now = "2026-09-02T02:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(_article())
        session.commit()
    with Session(consumer.engine) as session:
        article = _article(archive_updated_at="2026-09-01T01:00:00+00:00")
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id, status="succeeded", tagging_status="succeeded",
            quality_score=7.5, score_reason="old",
            summary="old summary", content_genre="industry_news",
            content_hash=compute_content_hash(article), analyzed_at=now,
            tagged_at=now, created_at=now, updated_at=now,
        ))
        session.commit()
    _copy_stream(producer, consumer, "articles")
    with Session(consumer.engine) as session:
        record = session.get(ArticleAnalysisRecord, "article-1")
        assert record.status == "pending"
        assert record.quality_score == 7.5
        assert record.summary == "old summary"
        assert record.analyzed_at == now


def test_local_version_strictly_newer_than_upstream_is_kept(tmp_path):
    """修订号新者胜(issue #263 用户拍板):本机自己写入的版本严格新于上游的,就保留本机的——
    文章、它的分析、本机跑出来的源状态都不动,也不把本机行的导出状态改成远端的。"""
    producer = _sink(tmp_path, "producer-newer-wins.db")
    consumer = _sink(tmp_path, "consumer-newer-wins.db")
    with Session(producer.engine) as session:
        session.add(_source(updated_at="2026-09-01T00:00:00+00:00", name="External"))
        article = _article(
            archive_updated_at="2026-09-01T01:00:00+00:00",
            content="External body A",
        )
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id, status="succeeded", tagging_status="succeeded",
            quality_score=4.0, content_hash=compute_content_hash(article),
            created_at="2026-09-01T01:00:00+00:00", updated_at="2026-09-01T01:00:00+00:00",
        ))
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="failing",
            updated_at="2026-09-02T00:00:00+00:00",
        ))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source(updated_at="2026-09-03T00:00:00+00:00", name="Local edit"))
        local_article = _article(
            archive_updated_at="2026-09-03T01:00:00+00:00",
            content="Local body B",
        )
        session.add(local_article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=local_article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=9.0,
            content_hash=compute_content_hash(local_article),
            created_at="2026-09-03T01:00:00+00:00",
            updated_at="2026-09-03T01:00:00+00:00",
        ))
        session.add(SourceStateRecord(
            source_id="rss_platform",
            fetcher_id="rss_platform",
            status="healthy",
            last_completed_at="2026-09-03T01:00:00+00:00",
            updated_at="2026-09-03T01:00:00+00:00",
        ))
        session.commit()

    _copy_stream(producer, consumer, "sources")
    with Session(consumer.engine) as session:
        source = session.get(SourceConfigRecord, "rss_platform")
        assert source.name == "External"  # 目录字段照上游
        assert session.get(SourceStateRecord, "rss_platform").status == "healthy"  # 本机状态不被删
    _raw, articles = _copy_stream(producer, consumer, "articles")
    assert (articles["inserted"], articles["updated"], articles["skipped"]) == (0, 0, 1)
    assert articles["skipped_identities"] == ["article-1"] and articles["taken_over"] == 0
    _raw, analyses = _copy_stream(producer, consumer, "analyses")
    assert analyses["skipped"] == 1
    _raw, states = _copy_stream(producer, consumer, "source_states")
    assert states["skipped"] == 1
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert article.content == "Local body B"
        assert article.analysis_authority_id == ""
        assert article.archive_updated_at == "2026-09-03T01:00:00+00:00"
        assert (analysis.status, analysis.quality_score, analysis.authority_id) == ("succeeded", 9.0, "")
        state = session.get(SourceStateRecord, "rss_platform")
        assert (state.status, state.authority_id) == ("healthy", "")
        for stream in ("articles", "analyses"):
            entity = session.get(ArchiveSyncEntityStateRecord, (stream, "article-1"))
            assert entity.authority_id == "" and entity.operation == "upsert"
        assert archive_sync_v2.pending_dependency_replay(consumer.engine) == set()


@pytest.mark.parametrize(
    ("local_revision", "kept_local"),
    [
        ("2026-09-02T01:00:00+00:00", False),          # 相等:上游胜
        ("2026-09-02T09:00:00+08:00", False),          # 同一时刻的另一种时区写法:仍是相等
        ("2026-09-02T01:00:00.000001+00:00", True),    # 本机严格更新
        ("not-a-timestamp", False),                    # 坏值视为最旧
    ],
)
def test_revision_comparison_handles_ties_timezones_and_bad_values(tmp_path, local_revision, kept_local):
    producer = _sink(tmp_path, "producer-revision.db")
    consumer = _sink(tmp_path, "consumer-revision.db")
    with Session(producer.engine) as session:
        session.add(_article(content="Upstream"))  # archive_updated_at = 2026-09-02T01:00:00+00:00
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_article(content="Local", archive_updated_at=local_revision))
        session.commit()
    _raw, applied = _copy_stream(producer, consumer, "articles")
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        if kept_local:
            assert (article.content, article.analysis_authority_id) == ("Local", "")
            assert applied["skipped"] == 1
        else:
            assert article.content == "Upstream" and article.analysis_authority_id
            assert applied["taken_over"] == 1
            assert archive_sync_v2.pending_dependency_replay(consumer.engine) == set(
                archive_sync_v2.DEPENDENCY_REPLAY_STREAMS
            )


def test_naive_revisions_are_read_in_the_node_timezone():
    import datetime

    naive = archive_sync_v2.revision_instant("2026-09-02T01:00:00")
    aware = archive_sync_v2.revision_instant(
        datetime.datetime(2026, 9, 2, 1, 0, 0).astimezone().isoformat()
    )
    assert naive == aware
    assert archive_sync_v2.revision_instant("") is None
    assert archive_sync_v2.revision_instant("garbage") is None
    assert archive_sync_v2.revision_newer("2026-09-02T01:00:00+00:00", "garbage") is True
    assert archive_sync_v2.revision_newer("garbage", "2026-09-02T01:00:00+00:00") is False
    assert archive_sync_v2.revision_newer("garbage", "also-garbage") is False


def test_analysis_maps_tags_by_code_and_preserves_manual_overlay(tmp_path):
    producer = _sink(tmp_path, "producer-analysis.db")
    consumer = _sink(tmp_path, "consumer-analysis.db")
    now = "2026-09-02T02:00:00+00:00"
    with Session(producer.engine) as session:
        article = _article()
        session.add(article)
        tag = CmsTagRecord(
            code="ai-agents", kind="topic", name_zh="智能体", name_en="AI Agents",
            normalized_name="ai agents", status="active", taxonomy_version=1,
            created_at=now, updated_at=now,
        )
        session.add(tag)
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id, status="succeeded", tagging_status="succeeded",
            quality_score=8.5, score_reason="good",
            summary="summary", content_genre="industry_news",
            content_hash=compute_content_hash(article), model_name="model",
            analysis_basis="podcast_show_notes",
            analysis_input_hash="actual-model-input-sha256",
            transcript_artifact_id="publisher-transcript-v1",
            analysis_diagnostics_json='{"topic_heat":{"window_days":7}}',
            prompt_version="p1", scoring_version="s1", taxonomy_version=1,
            analyzed_at=now, tagged_at=now, created_at=now, updated_at=now,
        ))
        session.add(ArticleTagAssignmentRecord(
            article_id=article.id, tag_id=int(tag.id), tag_kind="topic", is_primary=True,
            relevance=0.9, assignment_source="llm", prompt_version="p1",
            taxonomy_version=1, created_at=now, updated_at=now,
        ))
        session.commit()

    _copy_stream(producer, consumer, "taxonomy")
    _copy_stream(producer, consumer, "articles")
    with Session(consumer.engine) as session:
        tag = CmsTagRecord(
            code="manual-industry", kind="industry", name_zh="人工行业", name_en="Manual Industry",
            normalized_name="manual industry", status="active", taxonomy_version=1,
            created_at=now, updated_at=now,
        )
        session.add(tag)
        session.flush()
        session.add(ArticleTagAssignmentRecord(
            article_id="article-1", tag_id=int(tag.id), tag_kind="industry", is_primary=True,
            relevance=1, assignment_source="manual", prompt_version="",
            taxonomy_version=1, created_at=now, updated_at=now,
        ))
        session.commit()

    _copy_stream(producer, consumer, "analyses")
    with Session(producer.engine) as session:
        producer_revision = session.get(
            ArchiveSyncEntityStateRecord, ("analyses", "article-1")
        ).revision
    with Session(consumer.engine) as session:
        record = session.get(ArticleAnalysisRecord, "article-1")
        assignments = session.exec(select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == "article-1"
        )).all()
        assert record.quality_score == 8.5
        assert record.analysis_basis == "podcast_show_notes"
        assert record.analysis_input_hash == "actual-model-input-sha256"
        assert record.transcript_artifact_id == "publisher-transcript-v1"
        assert json.loads(record.analysis_diagnostics_json)["topic_heat"]["window_days"] == 7
        assert record.authority_id == archive_sync_v2.producer_authority_id(producer.engine)
        assert record.authority_revision == str(producer_revision)
        assert sorted((row.assignment_source, row.tag_kind, row.is_primary) for row in assignments) == [
            ("llm", "topic", False), ("manual", "industry", True),
        ]
        assert record.primary_tag_id == next(row.tag_id for row in assignments if row.assignment_source == "manual")

    # Replay cannot regress or duplicate the authority result.
    _, replay = _copy_stream(producer, consumer, "analyses")
    assert replay["inserted"] == 0
    assert replay["updated"] == 0


def test_legacy_v2_analysis_payload_clears_newer_provenance_fields(tmp_path):
    consumer = _sink(tmp_path, "consumer-legacy-analysis.db")
    authority_id = "producer-legacy"
    now = "2026-09-02T02:00:00+00:00"
    with Session(consumer.engine) as session:
        article = _article()
        article.analysis_authority_id = authority_id
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            quality_score=8.0,
            content_hash=compute_content_hash(article),
            analysis_basis="podcast_show_notes",
            analysis_input_hash="stale-hash",
            transcript_artifact_id="stale-artifact",
            analysis_diagnostics_json='{"stale":true}',
            authority_id=authority_id,
            authority_revision="1",
            created_at=now,
            updated_at=now,
        ))
        session.commit()

        archive_sync_v2._apply_analyses(
            session,
            [{
                "revision": "2",
                "payload": {
                    "article_id": article.id,
                    "status": "succeeded",
                    "quality_score": 8.5,
                    "content_hash": compute_content_hash(article),
                    "created_at": now,
                    "updated_at": now,
                    "assignments": [],
                },
            }],
            authority_id,
        )
        session.commit()
        record = session.get(ArticleAnalysisRecord, article.id)
        assert record.quality_score == 8.5
        assert record.analysis_basis == ""
        assert record.analysis_input_hash == ""
        assert record.transcript_artifact_id is None
        assert record.analysis_diagnostics_json == "{}"


def test_content_value_fields_sync_and_legacy_payload_clears_them(tmp_path):
    """issue #178 第二波:内容价值四项随分析流传到接收端;旧版生产者的结果不带它们时置空。"""
    producer = _sink(tmp_path, "producer-content-value.db")
    consumer = _sink(tmp_path, "consumer-content-value.db")
    authority_id = "producer-cv"
    now = "2026-10-12T02:00:00+00:00"
    with Session(producer.engine) as session:
        article = _article()
        session.add(article)
        session.flush()
        row = ArticleAnalysisRecord(
            article_id=article.id, status="succeeded", quality_score=5.2,
            content_kind="paper", content_value=7.6, content_value_reason="方法新",
            content_value_version="content-value-v2", pending_scope="content_value",
            content_hash=compute_content_hash(article), created_at=now, updated_at=now,
        )
        session.add(row)
        session.commit()
        payload = archive_sync_v2._analysis_payload(session, row)
    assert (payload["content_kind"], payload["content_value"], payload["content_value_version"]) == (
        "paper", 7.6, "content-value-v2",
    )
    assert "pending_scope" not in payload   # 本地任务状态,不外传

    with Session(consumer.engine) as session:
        article = _article()
        article.analysis_authority_id = authority_id
        session.add(article)
        session.commit()
        archive_sync_v2._apply_analyses(session, [{"revision": "1", "payload": payload}], authority_id)
        session.commit()
        record = session.get(ArticleAnalysisRecord, article.id)
        assert (record.content_value, record.content_value_reason) == (7.6, "方法新")
        assert record.pending_scope == ""

        new_fields = {"content_kind", "content_value", "content_value_reason", "content_value_version"}
        legacy = {key: value for key, value in payload.items() if key not in new_fields}
        archive_sync_v2._apply_analyses(session, [{"revision": "2", "payload": legacy}], authority_id)
        session.commit()
        record = session.get(ArticleAnalysisRecord, article.id)
        assert record.quality_score == 5.2
        assert (record.content_kind, record.content_value, record.content_value_version) == (None, None, "")


def test_new_article_body_hides_old_authority_analysis_until_matching_result_arrives(tmp_path):
    producer = _sink(tmp_path, "producer-body-revision.db")
    consumer = _sink(tmp_path, "consumer-body-revision.db")
    first_revision = "2026-09-02T01:00:00+00:00"
    second_revision = "2026-09-03T01:00:00+00:00"
    with Session(producer.engine) as session:
        article = _article(content="Body A", archive_updated_at=first_revision)
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=8.0,
            analysis_basis="article_body",
            analysis_input_hash="old-input",
            transcript_artifact_id="old-artifact",
            analysis_diagnostics_json='{"old":true}',
            content_hash=compute_content_hash(article),
            created_at=first_revision,
            updated_at=first_revision,
        ))
        session.commit()
    article_raw = archive_sync_v2.export_page(producer.engine, "articles")
    article_manifest, _ = archive_sync_v2.parse_page(
        article_raw, expected_stream="articles"
    )
    archive_sync_v2.import_page(consumer.engine, article_raw, expected_stream="articles")
    analysis_raw = archive_sync_v2.export_page(producer.engine, "analyses")
    analysis_manifest, _ = archive_sync_v2.parse_page(
        analysis_raw, expected_stream="analyses"
    )
    archive_sync_v2.import_page(consumer.engine, analysis_raw, expected_stream="analyses")

    with Session(producer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.content = "Body B"
        article.archive_updated_at = second_revision
        session.add(article)
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        analysis.content_hash = compute_content_hash(article)
        analysis.quality_score = 9.0
        analysis.updated_at = second_revision
        session.add(analysis)
        session.commit()

    raw = archive_sync_v2.export_page(
        producer.engine,
        "articles",
        since=article_manifest["snapshot"],
    )
    archive_sync_v2.import_page(consumer.engine, raw, expected_stream="articles")
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert article.content == "Body B"
        assert analysis.status == "pending"
        assert analysis.quality_score is None
        assert analysis.analysis_basis == ""
        assert analysis.analysis_input_hash == ""
        assert analysis.transcript_artifact_id is None
        assert analysis.analysis_diagnostics_json == "{}"

    raw = archive_sync_v2.export_page(
        producer.engine,
        "analyses",
        since=analysis_manifest["snapshot"],
    )
    archive_sync_v2.import_page(consumer.engine, raw, expected_stream="analyses")
    with Session(consumer.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert analysis.status == "succeeded"
        assert analysis.quality_score == 9.0


def test_taxonomy_full_snapshot_retires_receiver_only_active_tag(tmp_path):
    producer = _sink(tmp_path, "producer-taxonomy-retire.db")
    consumer = _sink(tmp_path, "consumer-taxonomy-retire.db")
    now = "2026-09-02T02:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(CmsTagRecord(
            code="external-tag", kind="topic", name_zh="外部标签",
            normalized_name="外部标签", status="active", taxonomy_version=1,
            created_at=now, updated_at=now,
        ))
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(CmsTagRecord(
            code="local-only", kind="topic", name_zh="本地标签",
            normalized_name="本地标签", status="active", taxonomy_version=1,
            user_selectable=True, filterable=True, recommendable=True,
            created_at=now, updated_at=now,
        ))
        session.commit()

    _copy_stream(producer, consumer, "taxonomy")
    with Session(consumer.engine) as session:
        stale = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "local-only")).one()
        assert stale.status == "deprecated"
        assert stale.user_selectable is False
        assert stale.filterable is False
        assert stale.recommendable is False
        external = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "external-tag")).one()
        assert external.status == "active"

    no_op = archive_sync_v2.export_page(producer.engine, "taxonomy", since="1")
    manifest, rows = archive_sync_v2.parse_page(no_op, expected_stream="taxonomy")
    assert rows == []
    assert manifest["full_snapshot"] is False
    archive_sync_v2.import_page(consumer.engine, no_op, expected_stream="taxonomy")
    with Session(consumer.engine) as session:
        external = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "external-tag")).one()
        assert external.status == "active"

    from services.taxonomy import rename_tag
    with Session(producer.engine) as session:
        external = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "external-tag")).one()
        rename_tag(
            session, int(external.id), actor_id="admin", name_zh="外部新标签",
            reason="sync watermark regression",
        )
    renamed = archive_sync_v2.export_page(producer.engine, "taxonomy", since="1")
    renamed_manifest, renamed_rows = archive_sync_v2.parse_page(
        renamed, expected_stream="taxonomy", requested_since="1"
    )
    assert int(renamed_manifest["snapshot"]) > 1
    assert renamed_manifest["taxonomy_version"] == 1
    assert renamed_manifest["full_snapshot"] is True
    assert any(row["payload"]["name_zh"] == "外部新标签" for row in renamed_rows)


def test_bad_page_is_atomic_and_checkpoint_cannot_be_claimed(tmp_path):
    producer = _sink(tmp_path, "producer-bad.db")
    consumer = _sink(tmp_path, "consumer-bad.db")
    with Session(producer.engine) as session:
        session.add(_article("a1"))
        session.add(_article("a2"))
        session.commit()
    raw = archive_sync_v2.export_page(producer.engine, "articles")
    lines = raw.splitlines()
    damaged = json.loads(lines[2])
    damaged["payload"]["title"] = "tampered"
    lines[2] = json.dumps(damaged)
    with pytest.raises(archive_sync_v2.SyncV2Error, match="checksum"):
        archive_sync_v2.import_page(consumer.engine, "\n".join(lines) + "\n")
    with Session(consumer.engine) as session:
        assert session.exec(select(ArticleRecord)).all() == []


def test_taxonomy_full_replay_retries_candidate_reconciliation(tmp_path, monkeypatch):
    producer = _sink(tmp_path, "producer-taxonomy-reconcile-retry.db")
    consumer = _sink(tmp_path, "consumer-taxonomy-reconcile-retry.db")
    now = "2026-09-02T00:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(CmsTagRecord(
            code="retry-tag", kind="topic", name_en="Retry Tag",
            normalized_name="retry tag", status="active", taxonomy_version=1,
            created_at=now, updated_at=now,
        ))
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.commit()
    raw = archive_sync_v2.export_page(producer.engine, "taxonomy")
    calls = []

    def flaky_reconcile(_session, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("simulated reconcile failure")

    monkeypatch.setattr(
        archive_sync_v2, "reconcile_synced_taxonomy_candidates", flaky_reconcile
    )
    with pytest.raises(RuntimeError, match="simulated reconcile failure"):
        archive_sync_v2.import_page(consumer.engine, raw, expected_stream="taxonomy")
    with Session(consumer.engine) as session:
        assert session.exec(select(CmsTagRecord).where(
            CmsTagRecord.code == "retry-tag"
        )).one() is not None

    replay = archive_sync_v2.import_page(
        consumer.engine, raw, expected_stream="taxonomy"
    )
    assert replay["inserted"] == 0 and replay["updated"] == 0
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_taxonomy_snapshot_allows_canonical_and_alias_owner_swaps(tmp_path):
    producer = _sink(tmp_path, "producer-taxonomy-swap.db")
    consumer = _sink(tmp_path, "consumer-taxonomy-swap.db")
    now = "2026-09-02T00:00:00+00:00"
    with Session(producer.engine) as session:
        tag_a = CmsTagRecord(
            code="a", kind="topic", name_en="Alpha", normalized_name="alpha",
            status="active", taxonomy_version=1, created_at=now, updated_at=now,
        )
        tag_b = CmsTagRecord(
            code="b", kind="topic", name_en="Beta", normalized_name="beta",
            status="active", taxonomy_version=1, created_at=now, updated_at=now,
        )
        session.add_all([tag_a, tag_b])
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.flush()
        session.add_all([
            CmsTagAliasRecord(
                tag_id=int(tag_a.id), kind="topic", alias="Alias X",
                normalized_alias="alias x", alias_type="synonym", locale="en",
                created_at=now, updated_at=now,
            ),
            CmsTagAliasRecord(
                tag_id=int(tag_b.id), kind="topic", alias="Alias Y",
                normalized_alias="alias y", alias_type="synonym", locale="en",
                created_at=now, updated_at=now,
            ),
        ])
        session.commit()
    with Session(consumer.engine) as session:
        old_a = CmsTagRecord(
            code="a", kind="topic", name_en="Beta", normalized_name="beta",
            status="active", created_at=now, updated_at=now,
        )
        old_b = CmsTagRecord(
            code="b", kind="topic", name_en="Alpha", normalized_name="alpha",
            status="active", created_at=now, updated_at=now,
        )
        session.add_all([old_a, old_b])
        session.flush()
        session.add_all([
            CmsTagAliasRecord(
                tag_id=int(old_a.id), kind="topic", alias="Alias Y",
                normalized_alias="alias y", alias_type="synonym", locale="en",
                created_at=now, updated_at=now,
            ),
            CmsTagAliasRecord(
                tag_id=int(old_b.id), kind="topic", alias="Alias X",
                normalized_alias="alias x", alias_type="synonym", locale="en",
                created_at=now, updated_at=now,
            ),
        ])
        session.commit()

    _copy_stream(producer, consumer, "taxonomy")
    with Session(consumer.engine) as session:
        tags = {row.code: row for row in session.exec(select(CmsTagRecord)).all()}
        aliases = {row.normalized_alias: row.tag_id for row in session.exec(
            select(CmsTagAliasRecord)
        ).all()}
        assert tags["a"].normalized_name == "alpha"
        assert tags["b"].normalized_name == "beta"
        assert aliases == {"alias x": tags["a"].id, "alias y": tags["b"].id}


@pytest.mark.parametrize("stream", ["sources", "articles", "source_states"])
def test_legacy_custom_source_id_collision_is_rejected_atomically(tmp_path, stream):
    producer = _sink(tmp_path, f"producer-private-collision-{stream}.db")
    consumer = _sink(tmp_path, f"consumer-private-collision-{stream}.db")
    with Session(producer.engine) as session:
        if stream == "sources":
            session.add_all([_source("a-public-new"), _source("legacy-custom")])
        elif stream == "articles":
            session.add_all([
                _article("a-public-new", source_id="public-new"),
                _article("legacy-collision", source_id="legacy-custom"),
            ])
        else:
            session.add_all([
                SourceStateRecord(
                    source_id="a-public-new", fetcher_id="rss", status="healthy",
                    updated_at="2026-09-02T00:00:00+00:00",
                ),
                SourceStateRecord(
                    source_id="legacy-custom", fetcher_id="rss", status="healthy",
                    updated_at="2026-09-02T00:00:00+00:00",
                ),
            ])
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source("legacy-custom", owner_username="alice"))
        session.add(_article("local-private", source_id="legacy-custom"))
        session.add(SourceStateRecord(
            source_id="legacy-custom", fetcher_id="private-rss", status="healthy",
            updated_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, stream)
    with pytest.raises(archive_sync_v2.SyncV2Error, match="local custom source"):
        archive_sync_v2.import_page(consumer.engine, raw, expected_stream=stream)

    with Session(consumer.engine) as session:
        private = session.get(SourceConfigRecord, "legacy-custom")
        assert private.owner_username == "alice"
        assert private.collection_authority_id == ""
        assert session.get(ArticleRecord, "local-private").analysis_authority_id == ""
        assert session.get(SourceStateRecord, "legacy-custom").fetcher_id == "private-rss"
        assert session.get(SourceConfigRecord, "a-public-new") is None
        assert session.get(ArticleRecord, "a-public-new") is None
        assert session.get(SourceStateRecord, "a-public-new") is None


def test_keyset_snapshot_and_source_state_readiness_fence(tmp_path):
    producer = _sink(tmp_path, "producer-pages.db")
    consumer = _sink(tmp_path, "consumer-pages.db")
    with Session(producer.engine) as session:
        session.add(_article("a1", archive_updated_at="2026-09-01T01:00:00+00:00"))
        session.add(_article("a2", archive_updated_at="2026-09-01T01:00:00+00:00"))
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00+00:00", last_success_at="2026-09-02T03:00:00+00:00",
            total_runs=1, success_runs=1, updated_at="2026-09-02T03:00:00+00:00",
        ))
        session.commit()
    first_raw = archive_sync_v2.export_page(producer.engine, "articles", limit=1)
    first_manifest, _ = archive_sync_v2.parse_page(first_raw)
    second_raw = archive_sync_v2.export_page(
        producer.engine, "articles", limit=1,
        snapshot=first_manifest["snapshot"],
        after=first_manifest["next_cursor"],
    )
    archive_sync_v2.import_page(consumer.engine, first_raw)
    archive_sync_v2.import_page(consumer.engine, second_raw)
    with Session(consumer.engine) as session:
        assert [r.id for r in session.exec(select(ArticleRecord).order_by(ArticleRecord.id)).all()] == ["a1", "a2"]
        # Source terminal state is a final stream, not published by article pages.
        assert session.get(SourceStateRecord, "rss_platform") is None
    _copy_stream(producer, consumer, "source_states")
    with Session(producer.engine) as session:
        producer_revision = session.get(
            ArchiveSyncEntityStateRecord, ("source_states", "rss_platform")
        ).revision
    with Session(consumer.engine) as session:
        state = session.get(SourceStateRecord, "rss_platform")
        assert state.status == "healthy"
        assert state.last_run_id is None
        assert state.authority_id == archive_sync_v2.producer_authority_id(producer.engine)
        assert state.authority_revision == str(producer_revision)


def test_parse_page_rejects_manifest_type_and_request_echo_tampering(tmp_path):
    producer = _sink(tmp_path, "strict-page.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()
    raw = archive_sync_v2.export_page(producer.engine, "sources")
    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="sources", requested_since="")
    bad = dict(manifest)
    bad["complete"] = "true"
    with pytest.raises(archive_sync_v2.SyncV2Error, match="boolean"):
        archive_sync_v2.parse_page(archive_sync_v2.encode_page(bad, rows), expected_stream="sources")
    with pytest.raises(archive_sync_v2.SyncV2Error, match="since"):
        archive_sync_v2.parse_page(raw, expected_stream="sources", requested_since="unexpected")


def test_export_includes_synced_rows_but_never_private_ones(tmp_path, monkeypatch):
    """拉来的也导出(issue #263):行上的同步印记不再决定导不导;读者自定源的文章、分析、
    源状态与图片照旧永不外传。"""
    producer = _sink(tmp_path, "no-launder.db")
    private_url = "https://img.test/legacy-private.png"
    with Session(producer.engine) as session:
        session.add(_source("remote-source", collection_authority_id="other-authority"))
        session.add(_article("orphan-local", source_id="remote-source"))
        session.add(SourceStateRecord(
            source_id="remote-source", fetcher_id="rss", status="healthy",
            authority_id="", updated_at="2026-09-02T00:00:00",
        ))
        session.add(_source("local-source"))
        session.add(_article("owned-local", source_id="local-source"))
        session.add(_source("legacy-custom", owner_username="alice"))
        session.add(_article(
            "legacy-private", source_id="legacy-custom",
            content=f"![private]({private_url})",
        ))
        session.flush()
        session.add(SourceStateRecord(
            source_id="legacy-custom", fetcher_id="rss", status="healthy",
            authority_id="", updated_at="2026-09-02T00:00:00",
        ))
        session.add(ArticleAnalysisRecord(
            article_id="legacy-private", status="succeeded", tagging_status="succeeded",
            authority_id="", created_at="2026-09-02T00:00:00+00:00",
            updated_at="2026-09-02T00:00:00+00:00",
        ))
        session.add(MediaAssetRecord(
            url_hash=hashlib.sha256(private_url.encode()).hexdigest(), url=private_url,
            status="cached", content_hash="b" * 64, mime="image/png", ext=".png",
            size_bytes=10, created_at="2026-09-02T00:00:00", updated_at="2026-09-02T00:00:00",
        ))
        session.commit()
    _, article_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "articles"), expected_stream="articles"
    )
    _, state_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "source_states"), expected_stream="source_states"
    )
    assert sorted(row["identity"] for row in article_rows) == ["orphan-local", "owned-local"]
    assert [row["identity"] for row in state_rows] == ["remote-source"]
    _, analysis_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "analyses"),
        expected_stream="analyses",
    )
    _, media_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "media"), expected_stream="media"
    )
    assert analysis_rows == []
    assert media_rows == []

    from api.routers import archive_sync as archive_router
    monkeypatch.setattr(archive_router.deps, "get_db_sink", lambda: producer)
    response = archive_router.export_archive_articles_jsonl(limit=1000)
    v1_rows = [json.loads(line) for line in response.body.decode().splitlines()][1:]
    assert [row["article"]["id"] for row in v1_rows] == ["orphan-local", "owned-local"]


def test_media_install_rejects_declared_image_with_non_image_bytes(tmp_path):
    sink = _sink(tmp_path, "media-mime.db")
    body = b"<html>not an image</html>"
    key = hashlib.sha256(b"https://img.test/not-image.jpg").hexdigest()
    with Session(sink.engine) as session:
        session.add(MediaAssetRecord(
            url_hash=key, url="https://img.test/not-image.jpg", status="pending_sync",
            content_hash=hashlib.sha256(body).hexdigest(), mime="image/jpeg", ext=".jpg",
            size_bytes=len(body), created_at="2026-09-01", updated_at="2026-09-01",
        ))
        session.commit()
    with pytest.raises(archive_sync_v2.SyncV2Error, match="图片"):
        archive_sync_v2.install_media_bytes(sink.engine, tmp_path / "media-mime", key, body)


def test_media_scope_includes_cover_but_not_audio_and_verifies_binary(tmp_path):
    urls = extract_image_urls(
        "![body](https://img.test/body.png)",
        {"image_url": "https://img.test/podcast.jpg", "audio_url": "https://audio.test/e.mp3"},
    )
    assert "https://img.test/podcast.jpg" in urls
    assert not any("audio.test" in url for url in urls)

    sink = _sink(tmp_path, "media.db")
    body = b"\xff\xd8\xffimage bytes"
    content_hash = hashlib.sha256(body).hexdigest()
    url = "https://img.test/podcast.jpg"
    key = hashlib.sha256(url.encode()).hexdigest()
    with Session(sink.engine) as session:
        session.add(MediaAssetRecord(
            url_hash=key, url=url, status="pending_sync", content_hash=content_hash,
            mime="image/jpeg", ext=".jpg", size_bytes=len(body),
            created_at="2026-09-01", updated_at="2026-09-01",
        ))
        session.commit()
    record = archive_sync_v2.install_media_bytes(sink.engine, tmp_path / "media", key, body)
    assert record.status == "cached"
    assert record.updated_at == "2026-09-01"
    assert (tmp_path / "media" / content_hash[:2] / f"{content_hash}.jpg").read_bytes() == body


def test_media_refresh_replaces_cached_manifest_and_public_scope_is_enforced(tmp_path):
    sink = _sink(tmp_path, "media-refresh.db")
    public_url = "https://img.test/public.jpg"
    private_url = "https://img.test/private.jpg"
    downstream_url = "https://img.test/remote-owned.jpg"
    public_key = hashlib.sha256(public_url.encode()).hexdigest()
    old_body = b"\xff\xd8\xffold image"
    new_body = b"\xff\xd8\xffnew image"
    with Session(sink.engine) as session:
        session.add(_article(content=f"![cover]({public_url})"))
        session.add(_source("user_rss_private", owner_username="alice"))
        session.add(_article(
            "private-article",
            source_id="user_rss_private",
            content=f"![private]({private_url})",
        ))
        session.add(_article(
            "remote-owned-article", content=f"![remote]({downstream_url})",
            analysis_authority_id="upstream",
        ))
        session.add(MediaAssetRecord(
            url_hash=public_key,
            url=public_url,
            status="cached",
            content_hash=hashlib.sha256(old_body).hexdigest(),
            mime="image/jpeg",
            ext=".jpg",
            size_bytes=len(old_body),
            fetched_at="2026-09-01T00:00:00+00:00",
            created_at="2026-09-01T00:00:00+00:00",
            updated_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()
        assert archive_sync_v2.is_public_media_reference(session, public_url) is True
        assert archive_sync_v2.is_public_media_reference(session, private_url) is False
        # 同步写入的文章引用的图片同样可以转发给下游(issue #263)
        assert archive_sync_v2.is_public_media_reference(session, downstream_url) is True

    payload = {
        "url_hash": public_key,
        "url": public_url,
        "content_hash": hashlib.sha256(new_body).hexdigest(),
        "mime": "image/jpeg",
        "ext": ".jpg",
        "size_bytes": len(new_body),
        "fetched_at": "2026-09-02T00:00:00+00:00",
        "updated_at": "2026-09-02T00:00:00+00:00",
    }
    row = archive_sync_v2._line(  # noqa: SLF001 - precise wire-contract fixture
        "media", payload, revision="1", identity=public_key
    )
    manifest = archive_sync_v2._manifest(  # noqa: SLF001
        "media", "external", "1", "", [row], complete=True
    )
    archive_sync_v2.import_page(
        sink.engine,
        archive_sync_v2.encode_page(manifest, [row]),
        expected_stream="media",
    )
    with Session(sink.engine) as session:
        refreshed = session.get(MediaAssetRecord, public_key)
        assert refreshed.status == "pending_sync"
        assert refreshed.content_hash == hashlib.sha256(new_body).hexdigest()
        assert refreshed.fetched_at is None

    installed = archive_sync_v2.install_media_bytes(
        sink.engine, tmp_path / "media-refresh", public_key, new_body
    )
    assert installed.status == "cached"
    assert installed.updated_at == payload["updated_at"]


def test_public_media_reference_scans_past_substring_false_positives(tmp_path):
    sink = _sink(tmp_path, "media-reference-pagination.db")
    url = "https://img.test/late-real-image.png"
    with Session(sink.engine) as session:
        session.add_all([
            _article(
                f"a-false-positive-{index:03d}",
                content=f"ordinary link, not an image: {url}",
            )
            for index in range(205)
        ])
        session.add(_article("z-real-image", content=f"![image]({url})"))
        session.commit()
        assert archive_sync_v2.is_public_media_reference(session, url) is True


def test_public_media_reference_resolves_relative_urls_with_exact_origin_scope(tmp_path):
    sink = _sink(tmp_path, "relative-media.db")
    with Session(sink.engine) as session:
        session.add(_article(content="![face](/media/face.webp)", source_url="https://openai.com/index/demo"))
        session.add(_article("private", source_id="user_rss_private", content="![](/private.webp)",
                             source_url="https://openai.com/index/demo"))
        session.commit()
        assert archive_sync_v2.is_public_media_reference(session, "https://openai.com/media/face.webp")
        assert not archive_sync_v2.is_public_media_reference(session, "https://evil.test/media/face.webp")
        assert not archive_sync_v2.is_public_media_reference(session, "https://openai.com/private.webp")


def test_media_manifest_rejects_path_traversal_extension(tmp_path):
    sink = _sink(tmp_path, "media-traversal.db")
    body = b"image bytes"
    url = "https://img.test/safe.jpg"
    payload = {
        "url_hash": hashlib.sha256(url.encode()).hexdigest(),
        "url": url,
        "content_hash": hashlib.sha256(body).hexdigest(),
        "mime": "image/jpeg",
        "ext": "/../../../../escaped.py",
        "size_bytes": len(body),
        "updated_at": "2026-09-02T00:00:00+00:00",
    }
    row = archive_sync_v2._line(  # noqa: SLF001
        "media", payload, revision="1", identity=payload["url_hash"]
    )
    manifest = archive_sync_v2._manifest(  # noqa: SLF001
        "media", "external", "1", "", [row], complete=True
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="extension"):
        archive_sync_v2.import_page(
            sink.engine,
            archive_sync_v2.encode_page(manifest, [row]),
            expected_stream="media",
        )
    assert not (tmp_path / "escaped.py").exists()


@pytest.mark.parametrize("separator", [" ", "\u0085", "\u2028", "\u2029"])
def test_candidate_evidence_is_minimized_and_review_only(tmp_path, separator):
    sink = _sink(tmp_path, "candidate-inbound.db")
    payload = {
        "label": f"Agent{separator}Memory",
        "kind": "topic",
        "confidence": 0.8,
        "article_fingerprint": "a" * 64,
        "source_provenance": "user_rss_example",
        "prompt_version": "p1",
    }
    manifest = {
        "kind": "manifest", "schema_version": archive_sync_v2.SCHEMA_VERSION,
        "stream": "candidate_evidence", "authority_id": "inner-stable",
        "snapshot": "2026-09-02T00:00:00+00:00", "after": "",
        "next_cursor": archive_sync_v2._encode_cursor("2026-09-01T00:00:00+00:00", "00000000000000000001"),
        "complete": True, "count": 1,
    }
    row = {"kind": "candidate_evidence", "schema_version": archive_sync_v2.SCHEMA_VERSION,
               "revision": "2026-09-01T00:00:00+00:00", "identity": "00000000000000000001",
               "checksum": archive_sync_v2.checksum(payload), "payload": payload}
    raw = archive_sync_v2.encode_page(manifest, [row])
    result = archive_sync_v2.import_candidate_evidence_page(sink.engine, raw)
    assert result["inserted"] == 1
    with Session(sink.engine) as session:
        evidence = session.exec(select(RemoteCandidateEvidenceRecord)).one()
        assert evidence.article_fingerprint == "a" * 64
        assert session.exec(select(CmsTagRecord)).first() is None  # never auto-activates a canonical taxonomy tag
        from api.routers.taxonomy import _candidate_payload
        candidate = session.get(CmsTagCandidateRecord, evidence.candidate_id)
        review = _candidate_payload(session, candidate)
        assert review["support_article_count_7d"] == 0
        assert review["remote_evidence"] == [{
            "authority_id": "inner-stable",
            "source_provenance": "user_rss_example",
            "confidence": 0.8,
            "label": payload["label"],
            "prompt_version": "p1",
            "created_at": evidence.created_at,
        }]
        assert "article_fingerprint" not in review["remote_evidence"][0]

    withdrawn_manifest = {
        **manifest,
        "snapshot": "2026-09-03T00:00:00+00:00",
        "next_cursor": "",
        "count": 0,
    }
    archive_sync_v2.import_candidate_evidence_page(
        sink.engine, archive_sync_v2.encode_page(withdrawn_manifest, [])
    )
    with Session(sink.engine) as session:
        assert session.exec(select(RemoteCandidateEvidenceRecord)).all() == []
        assert session.exec(select(CmsTagCandidateRecord)).all() == []

    leaking = json.loads(json.dumps(row))
    leaking["payload"]["content"] = "private body"
    leaking["checksum"] = archive_sync_v2.checksum(leaking["payload"])
    with pytest.raises(archive_sync_v2.SyncV2Error, match="unknown content fields"):
        archive_sync_v2.import_candidate_evidence_page(
            sink.engine, archive_sync_v2.encode_page(manifest, [leaking])
        )


def test_custom_candidate_evidence_uses_fixed_snapshot_keyset_pages(tmp_path):
    sink = _sink(tmp_path, "candidate-export.db")
    with Session(sink.engine) as session:
        session.add(_source("user_rss_local", owner_username="alice"))
        session.add(_source("legacy_custom", owner_username="alice"))
        session.add(_source(
            "user_rss_secret", owner_username="alice",
            params_json='{"credentialed_private":true}',
        ))
        session.add_all([
            _article("custom-a", source_id="user_rss_local"),
            _article("custom-b", source_id="legacy_custom"),
            _article("custom-secret", source_id="user_rss_secret"),
            _article("custom-orphan", source_id="user_rss_orphan"),
        ])
        candidate = CmsTagCandidateRecord(
            label="Memory", normalized_label="memory", proposed_kind="topic",
            first_seen_at="2026-09-01", last_seen_at="2026-09-01",
            created_at="2026-09-01", updated_at="2026-09-01",
        )
        session.add(candidate)
        session.flush()
        session.add_all([
            CmsTagCandidateEvidenceRecord(
                candidate_id=int(candidate.id), article_id=article_id,
                source_id=source_id, confidence=0.8, raw_label="Memory",
                created_at="2026-09-01T00:00:00+00:00",
            )
            for article_id, source_id in (
                ("custom-a", "user_rss_local"),
                ("custom-b", "legacy_custom"),
                ("custom-secret", "user_rss_secret"),
                ("custom-orphan", "user_rss_orphan"),
            )
        ])
        session.commit()
    first_raw = archive_sync_v2.export_custom_candidate_evidence_page(sink.engine, limit=1)
    first, first_rows = archive_sync_v2.parse_candidate_evidence_page(first_raw)
    assert first["complete"] is False and len(first_rows) == 1
    second_raw = archive_sync_v2.export_custom_candidate_evidence_page(
        sink.engine, snapshot=first["snapshot"], after=first["next_cursor"], limit=1,
    )
    second, second_rows = archive_sync_v2.parse_candidate_evidence_page(second_raw)
    assert second["snapshot"] == first["snapshot"]
    assert second["complete"] is True and len(second_rows) == 1
    assert second_rows[0]["identity"] > first_rows[0]["identity"]
    assert {
        row["payload"]["source_provenance"] for row in first_rows + second_rows
    } == {"user_rss_local", "legacy_custom"}

    receiver = _sink(tmp_path, "candidate-export-receiver.db")
    archive_sync_v2.import_candidate_evidence_page(receiver.engine, first_raw)
    tampered_manifest = {**second, "snapshot": "2099-01-01T00:00:00+00:00"}
    with pytest.raises(archive_sync_v2.SyncV2Error, match="staging"):
        archive_sync_v2.import_candidate_evidence_page(
            receiver.engine, archive_sync_v2.encode_page(tampered_manifest, second_rows)
        )


def test_fetch_v2_podcast_page_streams_with_a_hard_byte_limit():
    requested = []

    def handler(request):
        requested.append(dict(request.url.params))
        return httpx.Response(200, content=b"x" * 33)

    async def exercise():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await remote_sync_service._fetch_v2_page(  # noqa: SLF001
                client,
                "https://remote.test",
                "session=ok",
                stream="podcast_texts",
                snapshot="",
                since="",
                after="",
                limit=1,
                max_bytes=32,
            )

    with pytest.raises(remote_sync_service.RemoteSyncError, match="页面超过字节上限"):
        asyncio.run(exercise())
    # receiver_capabilities:接收端能力声明(issue #262 R1c),旧生产者忽略。
    assert requested == [{
        "limit": "1", "page_max_bytes": "32", "receiver_capabilities": "source-catalog-v1",
    }]


class _V2Remote:
    def __init__(self, producer, *, fail_stream=""):
        self.producer = producer
        self.fail_stream = fail_stream
        self.requested_streams = []
        self.requested_params = []
        self.presence_requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"user": {"role": "admin"}},
                headers={"set-cookie": "session=ok; Secure"},
            )
        if "session=ok" not in (request.headers.get("cookie") or ""):
            return httpx.Response(401)
        prefix = "/api/archive/v2/export/"
        if path.startswith(prefix):
            stream = path[len(prefix):].removesuffix(".jsonl")
            self.requested_streams.append(stream)
            if stream == self.fail_stream:
                return httpx.Response(400, json={"detail": "boom"})
            params = dict(request.url.params)
            self.requested_params.append((stream, params))
            return httpx.Response(
                200,
                text=archive_sync_v2.export_page(
                    self.producer.engine,
                    stream,
                    snapshot=params.get("snapshot", ""),
                    since=params.get("since", ""),
                    after=params.get("after", ""),
                    limit=int(params.get("limit", 1000)),
                    podcast_text_requested_page_max_bytes=(
                        int(params["page_max_bytes"])
                        if "page_max_bytes" in params
                        else None
                    ),
                ),
            )
        if path == "/api/archive/v2/presence":
            # 新版本接收端不再发这个请求(issue #263);端点留给旧版本接收端。
            self.presence_requests += 1
            payload = json.loads(request.content)
            identities = payload["identities"]
            return httpx.Response(200, json={
                "schema_version": archive_sync_v2.SCHEMA_VERSION,
                "capability": archive_sync_v2.AUTHORITATIVE_PRESENCE_CAPABILITY,
                "authority_id": archive_sync_v2.producer_authority_id(
                    self.producer.engine
                ),
                "stream": payload["stream"],
                "requested": identities,
                "present": archive_sync_v2.authority_present_identities(
                    self.producer.engine,
                    payload["stream"],
                    identities,
                ),
            })
        if path == "/api/archive/v2/candidate-evidence.jsonl":
            return httpx.Response(200, json={"status": "success", "inserted": 0, "skipped": 0})
        return httpx.Response(404)


class _LegacySixStreamV2Remote(_V2Remote):
    """A v3 producer that predates the optional Podcast text stream."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        response = super().handler(request)
        if response.status_code != 200 or not request.url.path.startswith(
            "/api/archive/v2/export/"
        ):
            return response
        lines = response.text.splitlines()
        manifest = json.loads(lines[0])
        manifest["capabilities"] = list(archive_sync_v2.REQUIRED_CAPABILITIES)
        return httpx.Response(
            200,
            text="\n".join([json.dumps(manifest), *lines[1:]]) + "\n",
        )


def test_podcast_text_capability_is_advertised_but_not_globally_required(tmp_path):
    producer = _sink(tmp_path, "producer-capability-split.db")
    manifest, _ = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "sources"),
        expected_stream="sources",
    )

    assert set(archive_sync_v2.REQUIRED_CAPABILITIES) < set(
        archive_sync_v2.ADVERTISED_CAPABILITIES
    )
    assert (
        archive_sync_v2.PODCAST_TEXT_PUBLICATIONS_CAPABILITY
        in manifest["capabilities"]
    )
    archive_sync_v2.require_transaction_revision_capability(
        {"capabilities": list(archive_sync_v2.REQUIRED_CAPABILITIES)}
    )


def test_probe_v2_returns_remote_authority(tmp_path):
    producer = _sink(tmp_path, "producer-probe-v2.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(TaxonomyVersionRecord(
            version=1,
            status="active",
            created_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()
    remote = _V2Remote(producer)
    result = asyncio.run(remote_sync_service.probe(
        "https://remote.test", "admin", "secret",
        protocol="v2", transport=httpx.MockTransport(remote.handler),
    ))
    assert result["protocol"] == "v2"
    assert result["authority_id"] == archive_sync_v2.producer_authority_id(producer.engine)
    assert archive_sync_v2.TRANSACTION_REVISION_CAPABILITY in result["capabilities"]
    assert remote.requested_streams == ["sources", "taxonomy"]
    assert result["taxonomy_ready"] is True


def test_probe_and_pull_keep_legacy_six_stream_peer_compatible(tmp_path):
    producer = _sink(tmp_path, "producer-legacy-six-stream.db")
    consumer = _sink(tmp_path, "consumer-legacy-six-stream.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(TaxonomyVersionRecord(
            version=1,
            status="active",
            created_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()
    remote = _LegacySixStreamV2Remote(producer)
    transport = httpx.MockTransport(remote.handler)

    probed = asyncio.run(remote_sync_service.probe(
        "https://remote.test", "admin", "secret",
        protocol="v2", transport=transport,
    ))
    assert probed["capabilities"] == list(archive_sync_v2.REQUIRED_CAPABILITIES)
    remote.requested_streams.clear()
    result = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "legacy-six-stream-media",
        expected_authority_id=probed["authority_id"],
        expected_capabilities=probed["capabilities"],
        transport=transport,
    ))

    assert remote.requested_streams == list(remote_sync_service.V2_LEGACY_STREAM_ORDER)
    assert tuple(result["streams"]) == remote_sync_service.V2_LEGACY_STREAM_ORDER
    assert "podcast_texts" not in result["streams"]


def test_pull_negotiates_podcast_text_before_source_readiness(tmp_path):
    producer = _sink(tmp_path, "producer-podcast-order.db")
    consumer = _sink(tmp_path, "consumer-podcast-order.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()
    remote = _V2Remote(producer)

    result = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "podcast-order-media",
        expected_capabilities=list(archive_sync_v2.ADVERTISED_CAPABILITIES),
        podcast_text_page_max_bytes=4096,
        podcast_text_page_max_rows=1,
        transport=httpx.MockTransport(remote.handler),
    ))

    assert remote.requested_streams == list(remote_sync_service.V2_STREAM_ORDER)
    assert tuple(result["streams"]) == remote_sync_service.V2_STREAM_ORDER
    podcast_request = next(
        params for stream, params in remote.requested_params if stream == "podcast_texts"
    )
    assert podcast_request["limit"] == "1"
    assert podcast_request["page_max_bytes"] == "4096"
    assert result["streams"]["podcast_texts"]["snapshot"] == result["streams"][
        "source_states"
    ]["snapshot"]


def test_pull_rejects_optional_capability_change_between_streams(tmp_path):
    producer = _sink(tmp_path, "producer-capability-change.db")
    consumer = _sink(tmp_path, "consumer-capability-change.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()

    class CapabilityChangingRemote(_V2Remote):
        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if response.status_code != 200 or not request.url.path.endswith(
                "/taxonomy.jsonl"
            ):
                return response
            lines = response.text.splitlines()
            manifest = json.loads(lines[0])
            manifest["capabilities"] = list(archive_sync_v2.REQUIRED_CAPABILITIES)
            return httpx.Response(
                200,
                text="\n".join([json.dumps(manifest), *lines[1:]]) + "\n",
            )

    remote = CapabilityChangingRemote(producer)
    with pytest.raises(remote_sync_service.RemoteSyncError, match="同步中发生变化"):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine,
            base_url="https://remote.test",
            username="admin",
            password="secret",
            media_root=tmp_path / "capability-change-media",
            transport=httpx.MockTransport(remote.handler),
        ))
    assert remote.requested_streams == ["sources", "taxonomy"]


def test_checkpoint_accepts_only_negotiated_streams(tmp_path):
    consumer = _sink(tmp_path, "consumer-negotiated-checkpoint.db")
    base_url = "https://remote.test"
    authority_id = "producer-a"
    checkpoint = {
        "authority_id": authority_id,
        "snapshot": "1",
        "cursor": "",
    }
    with Session(consumer.engine) as session:
        remote_sync_service.prepare_transaction_revision_consumer(
            session,
            base_url=base_url,
            username="admin",
            authority_id=authority_id,
            schema_version=archive_sync_v2.SCHEMA_VERSION,
            prepared_at="2026-09-05T00:00:00+00:00",
            capabilities=list(archive_sync_v2.REQUIRED_CAPABILITIES),
        )
        session.commit()
    with pytest.raises(ValueError, match="unnegotiated"):
        remote_sync_service.record_v2_stream_success(
            consumer.engine,
            base_url=base_url,
            username="admin",
            stream="podcast_texts",
            checkpoint=checkpoint,
            synced_at="2026-09-05T01:00:00+00:00",
        )

    with Session(consumer.engine) as session:
        remote_sync_service.prepare_transaction_revision_consumer(
            session,
            base_url=base_url,
            username="admin",
            authority_id=authority_id,
            schema_version=archive_sync_v2.SCHEMA_VERSION,
            prepared_at="2026-09-05T02:00:00+00:00",
            capabilities=list(archive_sync_v2.ADVERTISED_CAPABILITIES),
        )
        session.commit()
    remote_sync_service.record_v2_stream_success(
        consumer.engine,
        base_url=base_url,
        username="admin",
        stream="podcast_texts",
        checkpoint=checkpoint,
        synced_at="2026-09-05T03:00:00+00:00",
    )
    assert "podcast_texts" in remote_sync_service.load_sync_state(
        consumer.engine
    )["targets"][base_url]["v2_streams"]


def test_probe_rejects_unpublished_taxonomy_before_consumer_transition(tmp_path):
    producer = _sink(tmp_path, "producer-unpublished-taxonomy.db")
    consumer = _sink(tmp_path, "consumer-before-unpublished-probe.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(CmsTagRecord(
            code="draft-catalog", kind="topic", name_en="Draft Catalog",
            normalized_name="draft catalog", status="active", taxonomy_version=0,
            created_at="2026-09-01T00:00:00+00:00",
            updated_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_article("must-survive-preflight"))
        session.commit()

    remote = _V2Remote(producer)
    with pytest.raises(remote_sync_service.RemoteSyncError, match="尚未人工发布"):
        asyncio.run(remote_sync_service.probe(
            "https://remote.test", "admin", "secret",
            protocol="v2", transport=httpx.MockTransport(remote.handler),
        ))
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "must-survive-preflight") is not None
        assert session.get(AppSettingRecord, "remote_sync:v2_consumer_mode") is None


def test_new_consumer_rejects_old_v2_peer_during_probe_and_pull(tmp_path):
    producer = _sink(tmp_path, "producer-old-v2.db")
    consumer = _sink(tmp_path, "consumer-new-v2.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()

    class OldV2Remote(_V2Remote):
        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if (
                response.status_code == 200
                and request.url.path.endswith("/sources.jsonl")
            ):
                lines = response.text.splitlines()
                manifest = json.loads(lines[0])
                manifest.pop("capabilities", None)
                return httpx.Response(
                    200,
                    text="\n".join([json.dumps(manifest), *lines[1:]]) + "\n",
                )
            return response

    remote = OldV2Remote(producer)
    transport = httpx.MockTransport(remote.handler)
    with pytest.raises(remote_sync_service.RemoteSyncError, match="capability"):
        asyncio.run(remote_sync_service.probe(
            "https://remote.test",
            "admin",
            "secret",
            protocol="v2",
            transport=transport,
        ))
    with pytest.raises(remote_sync_service.RemoteSyncError, match="capability"):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine,
            base_url="https://remote.test",
            username="admin",
            password="secret",
            media_root=tmp_path / "old-peer-media",
            transport=transport,
        ))
    with Session(consumer.engine) as session:
        assert session.exec(select(SourceConfigRecord)).all() == []


def test_pull_rejects_authority_changed_after_probe_before_first_write(tmp_path):
    producer = _sink(tmp_path, "producer-authority-b.db")
    consumer = _sink(tmp_path, "consumer-prepared-authority-a.db")
    with Session(producer.engine) as session:
        session.add(_source("authority-b-source"))
        session.commit()
    with Session(consumer.engine) as session:
        remote_sync_service.prepare_transaction_revision_consumer(
            session,
            base_url="https://remote.test",
            username="admin",
            authority_id="prepared-authority-a",
            schema_version=archive_sync_v2.SCHEMA_VERSION,
            prepared_at="2026-09-04T00:00:00+00:00",
        )
        session.commit()

    remote = _V2Remote(producer)
    with pytest.raises(remote_sync_service.RemoteSyncError, match="连接预检不一致"):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine,
            base_url="https://remote.test",
            username="admin",
            password="secret",
            media_root=tmp_path / "authority-mismatch-media",
            expected_authority_id="prepared-authority-a",
            transport=httpx.MockTransport(remote.handler),
        ))
    with Session(consumer.engine) as session:
        assert session.get(SourceConfigRecord, "authority-b-source") is None
        producer_authority = archive_sync_v2.producer_authority_id(producer.engine)
        assert session.exec(select(ArchiveSyncEntityStateRecord).where(
            ArchiveSyncEntityStateRecord.authority_id == producer_authority
        )).all() == []
        state = json.loads(session.get(
            AppSettingRecord, remote_sync_service.REMOTE_SYNC_STATE_KEY
        ).value)
        assert state["targets"]["https://remote.test"]["v2_authority_id"] == "prepared-authority-a"


def test_new_producer_rejects_wall_clock_snapshot_and_invalid_continuation(tmp_path):
    producer = _sink(tmp_path, "producer-fail-closed-v2.db")
    with Session(producer.engine) as session:
        session.add_all([_source("rss_a"), _source("rss_b")])
        session.commit()

    with pytest.raises(archive_sync_v2.SyncV2Error, match="canonical"):
        archive_sync_v2.export_page(
            producer.engine,
            "analyses",
            snapshot="2026-09-04T00:00:00+00:00",
        )

    first_raw = archive_sync_v2.export_page(
        producer.engine,
        "sources",
        limit=1,
    )
    first, _rows = archive_sync_v2.parse_page(
        first_raw,
        expected_stream="sources",
    )
    assert first["complete"] is False
    with pytest.raises(archive_sync_v2.SyncV2Error, match="snapshot is required"):
        archive_sync_v2.export_page(
            producer.engine,
            "sources",
            after=first["next_cursor"],
            limit=1,
        )

    with pytest.raises(archive_sync_v2.SyncV2Error, match="canonical"):
        archive_sync_v2.export_page(
            producer.engine,
            "articles",
            snapshot=first["snapshot"],
            after=archive_sync_v2._encode_cursor("01", "article"),
            limit=1,
        )


@pytest.mark.parametrize("already_synced", [False, True], ids=["legacy-v1", "v2-incremental"])
@pytest.mark.parametrize("page_size", [1, 1000], ids=["paged", "same-page"])
def test_stale_brief_analysis_does_not_block_score_sync(tmp_path, already_synced, page_size):
    producer = _sink(tmp_path, "producer-stale-analysis.db")
    consumer = _sink(tmp_path, "consumer-stale-analysis.db")
    brief_id = "daily_brief_2026-09-15"
    with Session(producer.engine) as session:
        session.add(_source())
        for article in (_article(brief_id, content="Original daily brief"), _article()):
            session.add(article)
            session.flush()
            session.add(ArticleAnalysisRecord(
                article_id=article.id, status="succeeded", quality_score=5.8,
                content_hash=compute_content_hash(article),
                created_at="2026-09-15", updated_at="2026-09-15",
            ))
        session.commit()
    # V1 already copied articles but never supplied authoritative analyses.
    with Session(consumer.engine) as session:
        brief = _article(brief_id, content="Original daily brief", read_count=17)
        session.add_all([brief, _article()])
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=brief.id, status="succeeded", quality_score=4.0,
            content_hash=compute_content_hash(brief),
            created_at="2026-09-15", updated_at="2026-09-15",
        ))
        session.commit()
    remote = _V2Remote(producer)

    def pull(checkpoints=None):
        return asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine, base_url="https://remote.test",
            username="admin", password="secret", page_size=page_size,
            media_root=tmp_path / "media", checkpoints=checkpoints,
            transport=httpx.MockTransport(remote.handler),
        ))

    previous = pull()["streams"] if already_synced else None
    with Session(producer.engine) as session:
        brief = session.get(ArticleRecord, brief_id)
        brief.content = "Regenerated daily brief"
        session.add(brief)
        session.flush()
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        analysis.quality_score = 9.0
        analysis.score_reason = "Current news value"
        analysis.summary = "Current summary"
        session.add(analysis)
        session.commit()

    result = pull(previous)
    assert list(result["streams"]) == list(remote_sync_service.V2_STREAM_ORDER)
    assert result["streams"]["analyses"]["count"] == 2
    with Session(consumer.engine) as session:
        brief = session.get(ArticleRecord, brief_id)
        assert brief.content == "Regenerated daily brief"
        assert brief.read_count == 17
        assert session.get(ArticleAnalysisRecord, brief_id) is None
        assert session.get(ArchiveSyncEntityStateRecord, ("analyses", brief_id)).operation == "tombstone"
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert (analysis.quality_score, analysis.score_reason, analysis.summary) == (
            9.0, "Current news value", "Current summary",
        )
    assert archive_sync_v2.authority_present_identities(
        producer.engine, "analyses", [brief_id, "article-1"]
    ) == ["article-1"]

    # Export does not rewrite the producer's old result. A genuinely new
    # analysis gets its own revision and can restore the withdrawn score.
    with Session(producer.engine) as session:
        brief = session.get(ArticleRecord, brief_id)
        analysis = session.get(ArticleAnalysisRecord, brief_id)
        assert analysis.content_hash != compute_content_hash(brief)
        assert analysis.quality_score == 5.8
        analysis.content_hash = compute_content_hash(brief)
        analysis.quality_score = 7.0
        session.add(analysis)
        session.commit()
    restored = pull(result["streams"])
    assert restored["streams"]["analyses"]["count"] == 1
    with Session(consumer.engine) as session:
        assert session.get(ArticleAnalysisRecord, brief_id).quality_score == 7.0
        assert session.get(ArticleAnalysisRecord, "article-1").quality_score == 9.0


def test_analysis_hash_mismatch_still_rolls_back_the_whole_page(tmp_path):
    producer = _sink(tmp_path, "producer-bad-analysis.db")
    consumer = _sink(tmp_path, "consumer-bad-analysis.db")
    with Session(producer.engine) as session:
        for article_id in ("first", "second"):
            article = _article(article_id)
            session.add(article)
            session.flush()
            session.add(ArticleAnalysisRecord(
                article_id=article.id, status="succeeded", quality_score=8.0,
                content_hash=compute_content_hash(article),
                created_at="2026-09-15", updated_at="2026-09-15",
            ))
        session.commit()
    _copy_stream(producer, consumer, "articles")
    manifest, rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "analyses")
    )
    rows[1]["payload"]["content_hash"] = "0" * 64
    rows[1]["checksum"] = archive_sync_v2.checksum(rows[1]["payload"])
    with pytest.raises(archive_sync_v2.SyncV2Error, match="analysis content_hash mismatch: second"):
        archive_sync_v2.import_page(
            consumer.engine, archive_sync_v2.encode_page(manifest, rows),
            expected_stream="analyses",
        )
    with Session(consumer.engine) as session:
        assert session.exec(select(ArticleAnalysisRecord)).all() == []
        assert session.get(ArchiveSyncEntityStateRecord, ("analyses", "first")) is None


def test_remote_pull_v2_end_to_end_and_terminal_state_is_last(tmp_path):
    producer = _sink(tmp_path, "producer-remote-v2.db")
    consumer = _sink(tmp_path, "consumer-remote-v2.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_article())
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00+00:00", last_success_at="2026-09-02T03:00:00+00:00",
            total_runs=1, success_runs=1, updated_at="2026-09-02T03:00:00+00:00",
        ))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source())
        matching = _article(content="stale receiver body")
        receiver_only = _article("receiver-only", content="not present on authority")
        legacy_orphan = _article("legacy-orphan", content="v1 row without source metadata")
        legacy_orphan.source_id = "legacy-missing-source"
        session.add_all([matching, receiver_only, legacy_orphan])
        session.flush()
        session.add_all([
            ArticleAnalysisRecord(
                article_id=matching.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=3.0,
                content_hash=compute_content_hash(matching),
                created_at="2026-09-01T00:00:00+00:00",
                updated_at="2026-09-01T00:00:00+00:00",
            ),
            ArticleAnalysisRecord(
                article_id=legacy_orphan.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=2.0,
                content_hash=compute_content_hash(legacy_orphan),
                created_at="2026-09-01T00:00:00+00:00",
                updated_at="2026-09-01T00:00:00+00:00",
            ),
        ])
        session.commit()
    remote = _V2Remote(producer)
    completed = []
    result = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-media",
        on_stream_complete=lambda stream, checkpoint: completed.append(stream),
        transport=httpx.MockTransport(remote.handler),
    ))
    assert completed == list(remote_sync_service.V2_STREAM_ORDER)
    assert completed[-1] == "source_states"
    first_params = {stream: params for stream, params in remote.requested_params}
    assert "snapshot" not in first_params["sources"]
    generation = result["streams"]["sources"]["snapshot"]
    assert all(
        first_params[stream]["snapshot"] == generation
        for stream in ("articles", "analyses", "media", "source_states")
    )
    assert result["candidate_evidence"]["status"] == "success"
    assert remote.presence_requests == 0
    with Session(consumer.engine) as session:
        # 修订号相等:上游胜,v1 归档 / 库副本起步的文章接上上游的版本
        taken = session.get(ArticleRecord, "article-1")
        assert taken.analysis_authority_id == result["authority_id"]
        assert taken.content == "Full body"
        # 正文换了,本机旧分析作废待定;上游没有这篇的分析,所以不会有结果
        superseded = session.get(ArticleAnalysisRecord, "article-1")
        assert (superseded.status, superseded.quality_score) == ("pending", None)
        # 不再做「上游没有就删」:本机独有的文章与分析原样保留
        assert session.get(ArticleRecord, "receiver-only").analysis_authority_id == ""
        assert session.get(ArticleRecord, "legacy-orphan") is not None
        assert session.get(ArticleAnalysisRecord, "legacy-orphan").quality_score == 2.0
        assert session.get(SourceStateRecord, "rss_platform").status == "healthy"
    assert all(stream["pruned"] == 0 for stream in result["streams"].values())
    # 接管触发的依赖流重放已在同一轮完成并清掉标记
    assert archive_sync_v2.pending_dependency_replay(consumer.engine) == set()

    # Independent completed snapshots become exclusive lower watermarks; an
    # unchanged second run is genuinely incremental, not a hidden full replay.
    second = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-media",
        checkpoints=result["streams"],
        transport=httpx.MockTransport(remote.handler),
    ))
    assert all(item["count"] == 0 for item in second["streams"].values())


def test_full_prune_presence_protects_row_updated_between_pages(tmp_path):
    producer = _sink(tmp_path, "producer-presence-race.db")
    consumer = _sink(tmp_path, "consumer-presence-race.db")
    now = "2026-09-02T00:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(_source())
        session.add_all([
            _article("a-first", content="first"),
            _article("z-race", content="old authority body"),
        ])
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source())
        session.add(_article("z-race", content="old receiver body"))
        tag = CmsTagRecord(
            code="manual-local", kind="topic", name_en="Manual Local",
            normalized_name="manual local", status="active", taxonomy_version=0,
            created_at=now, updated_at=now,
        )
        session.add(tag)
        session.flush()
        session.add(ArticleTagAssignmentRecord(
            article_id="z-race", tag_id=int(tag.id), tag_kind="topic",
            is_primary=True, relevance=1, assignment_source="manual",
            prompt_version="", taxonomy_version=0, created_at=now, updated_at=now,
        ))
        session.commit()

    class UpdatingBetweenPagesRemote(_V2Remote):
        updated = False

        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if (
                not self.updated
                and request.url.path.endswith("/articles.jsonl")
                and "after" not in request.url.params
            ):
                with Session(self.producer.engine) as session:
                    raced = session.get(ArticleRecord, "z-race")
                    raced.content = "new authority body"
                    raced.fetched_date = "2026-09-03T01:00:00+00:00"
                    raced.archive_updated_at = "2026-09-03T00:00:00+00:00"
                    session.add(raced)
                    session.commit()
                self.updated = True
            return response

    remote = UpdatingBetweenPagesRemote(producer)
    first = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "presence-media",
        page_size=1,
        transport=httpx.MockTransport(remote.handler),
    ))
    assert first["streams"]["articles"]["pruned"] == 0
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "z-race").content == "old receiver body"
        assert session.exec(select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == "z-race",
            ArticleTagAssignmentRecord.assignment_source == "manual",
        )).one() is not None

    second = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "presence-media",
        page_size=1,
        checkpoints=first["streams"],
        transport=httpx.MockTransport(remote.handler),
    ))
    assert second["streams"]["articles"]["count"] == 1
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "z-race").content == "new authority body"
        assert session.get(ArticleRecord, "z-race").fetched_date == "2026-09-03T01:00:00+00:00"
        assert session.exec(select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == "z-race",
            ArticleTagAssignmentRecord.assignment_source == "manual",
        )).one() is not None


def test_completed_checkpoints_do_not_hide_later_article_and_matching_analysis(tmp_path):
    producer = _sink(tmp_path, "producer-after-checkpoint.db")
    consumer = _sink(tmp_path, "consumer-after-checkpoint.db")
    with Session(producer.engine) as session:
        session.add(_source())
        article = _article(content="Body A")
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=7.0,
            content_hash=compute_content_hash(article),
            created_at="2026-09-02T02:00:00+00:00",
            updated_at="2026-09-02T02:00:00+00:00",
        ))
        session.commit()
    remote = _V2Remote(producer)
    first = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-after-checkpoint-media",
        transport=httpx.MockTransport(remote.handler),
    ))

    with Session(producer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        old_revision = article.archive_updated_at
        article.content = "Body B"
        session.add(article)
        assert queue_article_analysis(
            session,
            article.id,
            force=True,
            now=dt.datetime.now(dt.timezone.utc),
        ) == "invalidated"
        session.commit()
        session.refresh(article)
        assert article.archive_updated_at > old_revision

    # Complete the newly queued result only after the content mutation has
    # committed, as the worker would. It must match the article hash that the
    # consumer receives earlier in the same v2 run.
    with Session(producer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, article.id)
        analysis.status = "succeeded"
        analysis.tagging_status = "succeeded"
        analysis.quality_score = 9.0
        analysis.content_hash = compute_content_hash(article)
        analysis.updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        session.add(analysis)
        session.commit()

    second = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-after-checkpoint-media",
        checkpoints=first["streams"],
        transport=httpx.MockTransport(remote.handler),
    ))
    assert second["streams"]["articles"]["count"] == 1
    assert second["streams"]["analyses"]["count"] == 1
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, article.id)
        assert article.content == "Body B"
        assert analysis.status == "succeeded"
        assert analysis.quality_score == 9.0
        assert analysis.content_hash == compute_content_hash(article)


def test_analysis_update_in_same_wall_clock_second_gets_new_transaction_revision(tmp_path):
    producer = _sink(tmp_path, "producer-subsecond-analysis.db")
    with Session(producer.engine) as session:
        session.add(_source())
        article = _article(content="Body A")
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=7.0,
            content_hash=compute_content_hash(article),
            analyzed_at="2026-09-04T12:00:00.400000+00:00",
            created_at="2026-09-04T12:00:00.400000+00:00",
            updated_at="2026-09-04T12:00:00.400000+00:00",
        ))
        session.commit()

    first_raw = archive_sync_v2.export_page(producer.engine, "analyses")
    first_manifest, first_rows = archive_sync_v2.parse_page(
        first_raw, expected_stream="analyses"
    )
    assert len(first_rows) == 1

    with Session(producer.engine) as session:
        outcome = queue_article_analysis(
            session,
            "article-1",
            force=True,
            now=dt.datetime.fromisoformat("2026-09-04T12:00:00.700000+00:00"),
        )
        assert outcome == "invalidated"
        session.commit()
        assert session.get(ArticleAnalysisRecord, "article-1").updated_at == (
            "2026-09-04T12:00:00.700000+00:00"
        )

    second_raw = archive_sync_v2.export_page(
        producer.engine,
        "analyses",
        since=first_manifest["snapshot"],
    )
    second_manifest, second_rows = archive_sync_v2.parse_page(
        second_raw, expected_stream="analyses"
    )
    assert int(second_manifest["snapshot"]) > int(first_manifest["snapshot"])
    assert len(second_rows) == 1
    assert int(second_rows[0]["revision"]) > int(first_manifest["snapshot"])
    assert second_rows[0]["payload"]["status"] == "pending"


def test_preflush_commit_is_replayed_and_rolled_back_revision_never_escapes(tmp_path):
    sink = _sink(tmp_path, "preflush-rollback.db")
    with Session(sink.engine) as session:
        session.add(_article())
        session.commit()

    baseline_raw = archive_sync_v2.export_page(sink.engine, "articles")
    baseline, _ = archive_sync_v2.parse_page(
        baseline_raw, expected_stream="articles"
    )

    with Session(sink.engine) as writer:
        article = writer.get(ArticleRecord, "article-1")
        article.content = "assigned before export, not flushed"
        writer.add(article)

        # Assigning ORM state no longer assigns a wall-clock watermark. Until
        # flush/commit, another connection sees the old transaction revision.
        interim_raw = archive_sync_v2.export_page(
            sink.engine, "articles", since=baseline["snapshot"]
        )
        interim, interim_rows = archive_sync_v2.parse_page(
            interim_raw, expected_stream="articles"
        )
        assert interim_rows == []
        assert interim["snapshot"] == baseline["snapshot"]
        writer.commit()

    committed_raw = archive_sync_v2.export_page(
        sink.engine, "articles", since=interim["snapshot"]
    )
    committed, committed_rows = archive_sync_v2.parse_page(
        committed_raw, expected_stream="articles"
    )
    assert [row["identity"] for row in committed_rows] == ["article-1"]
    assert int(committed_rows[0]["revision"]) > int(interim["snapshot"])

    with Session(sink.engine) as writer:
        article = writer.get(ArticleRecord, "article-1")
        article.content = "this mutation rolls back"
        writer.add(article)
        writer.flush()
        transient_revision = writer.get(
            ArchiveSyncEntityStateRecord, ("articles", "article-1")
        ).revision
        assert transient_revision > int(committed["snapshot"])
        writer.rollback()

    after_rollback_raw = archive_sync_v2.export_page(
        sink.engine, "articles", since=committed["snapshot"]
    )
    after_rollback, after_rollback_rows = archive_sync_v2.parse_page(
        after_rollback_raw, expected_stream="articles"
    )
    assert after_rollback_rows == []
    assert after_rollback["snapshot"] == committed["snapshot"]
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "article-1").content == (
            "assigned before export, not flushed"
        )


def test_article_parent_revision_precedes_matching_analysis_child_revision(tmp_path):
    sink = _sink(tmp_path, "parent-child-revision.db")
    with Session(sink.engine) as session:
        article = _article(content="Body A")
        session.add(article)
        session.flush()
        session.add(
            ArticleAnalysisRecord(
                article_id=article.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=7.0,
                content_hash=compute_content_hash(article),
                created_at="2026-09-04T12:00:00+00:00",
                updated_at="2026-09-04T12:00:00+00:00",
            )
        )
        session.commit()

    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.content = "Body B"
        session.add(article)
        assert queue_article_analysis(session, article.id, force=True) == "invalidated"
        analysis = session.get(ArticleAnalysisRecord, article.id)
        analysis.status = "succeeded"
        analysis.tagging_status = "succeeded"
        analysis.quality_score = 9.0
        analysis.content_hash = compute_content_hash(article)
        session.add(analysis)
        session.commit()

    with Session(sink.engine) as session:
        article_state = session.get(
            ArchiveSyncEntityStateRecord, ("articles", "article-1")
        )
        analysis_state = session.get(
            ArchiveSyncEntityStateRecord, ("analyses", "article-1")
        )
        clock = session.get(ArchiveSyncClockRecord, 1)
        assert article_state.operation == analysis_state.operation == "upsert"
        assert article_state.revision < analysis_state.revision <= clock.revision
        snapshot = str(clock.revision)

    article_manifest, article_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(sink.engine, "articles", snapshot=snapshot),
        expected_stream="articles",
    )
    analysis_manifest, analysis_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(sink.engine, "analyses", snapshot=snapshot),
        expected_stream="analyses",
    )
    assert article_manifest["snapshot"] == analysis_manifest["snapshot"] == snapshot
    assert int(article_rows[0]["revision"]) < int(analysis_rows[0]["revision"])
    assert analysis_rows[0]["payload"]["content_hash"] == compute_content_hash(
        _article(content="Body B")
    )


def test_integer_revision_keyset_orders_nine_before_ten(tmp_path):
    sink = _sink(tmp_path, "numeric-keyset.db")
    with Session(sink.engine) as session:
        for number in range(1, 13):
            session.add(_source(f"rss_{number:02d}"))
            session.flush()
        session.commit()

    first_raw = archive_sync_v2.export_page(sink.engine, "sources", limit=9)
    first, first_rows = archive_sync_v2.parse_page(
        first_raw, expected_stream="sources"
    )
    assert [int(row["revision"]) for row in first_rows] == list(range(1, 10))
    assert first["complete"] is False

    second_raw = archive_sync_v2.export_page(
        sink.engine,
        "sources",
        snapshot=first["snapshot"],
        after=first["next_cursor"],
        limit=9,
    )
    second, second_rows = archive_sync_v2.parse_page(
        second_raw, expected_stream="sources"
    )
    assert second["snapshot"] == first["snapshot"]
    assert [int(row["revision"]) for row in second_rows] == [10, 11, 12]


def test_tombstone_wins_when_an_older_upsert_page_is_replayed(tmp_path):
    producer = _sink(tmp_path, "producer-tombstone-replay.db")
    consumer = _sink(tmp_path, "consumer-tombstone-replay.db")
    with Session(producer.engine) as session:
        session.add(_article())
        session.commit()

    old_page = archive_sync_v2.export_page(producer.engine, "articles")
    old_manifest, old_rows = archive_sync_v2.parse_page(
        old_page, expected_stream="articles"
    )
    assert old_rows[0]["operation"] == "upsert"
    archive_sync_v2.import_page(consumer.engine, old_page, expected_stream="articles")

    with Session(producer.engine) as session:
        session.delete(session.get(ArticleRecord, "article-1"))
        session.commit()
    tombstone_page = archive_sync_v2.export_page(
        producer.engine, "articles", since=old_manifest["snapshot"]
    )
    _tombstone_manifest, tombstone_rows = archive_sync_v2.parse_page(
        tombstone_page, expected_stream="articles"
    )
    assert tombstone_rows[0]["operation"] == "tombstone"
    assert int(tombstone_rows[0]["revision"]) > int(old_rows[0]["revision"])
    archive_sync_v2.import_page(
        consumer.engine, tombstone_page, expected_stream="articles"
    )
    replay = archive_sync_v2.import_page(
        consumer.engine, old_page, expected_stream="articles"
    )
    assert replay["inserted"] == replay["updated"] == replay["deleted"] == 0
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "article-1") is None
        state = session.get(
            ArchiveSyncEntityStateRecord, ("articles", "article-1")
        )
        assert state.operation == "tombstone"
        assert state.revision == int(tombstone_rows[0]["revision"])


@pytest.mark.parametrize("relative", [False, True])
def test_media_delta_is_driven_by_current_article_reference_revision(tmp_path, relative):
    sink = _sink(tmp_path, "media-reference-revision.db")
    url = "https://img.test/reference-driven.jpg"
    key = hashlib.sha256(url.encode()).hexdigest()
    with Session(sink.engine) as session:
        session.add(
            MediaAssetRecord(
                url_hash=key,
                url=url,
                status="cached",
                content_hash="a" * 64,
                mime="image/jpeg",
                ext=".jpg",
                size_bytes=10,
                created_at="2026-09-01T00:00:00",
                updated_at="2026-09-01T00:00:00",
            )
        )
        session.commit()
    with Session(sink.engine) as session:
        session.add(_article(content="Body without an image"))
        session.commit()

    baseline_raw = archive_sync_v2.export_page(sink.engine, "media")
    baseline, baseline_rows = archive_sync_v2.parse_page(
        baseline_raw, expected_stream="media"
    )
    assert baseline_rows == []

    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.source_url = "https://img.test/article"
        article.content = "![new reference](/reference-driven.jpg)" if relative else f"![new reference]({url})"
        session.add(article)
        session.commit()
        article_revision = session.get(
            ArchiveSyncEntityStateRecord, ("articles", article.id)
        ).revision
        media_revision = session.get(
            ArchiveSyncEntityStateRecord, ("media", key)
        ).revision
        assert article_revision > media_revision

    referenced_raw = archive_sync_v2.export_page(
        sink.engine, "media", since=baseline["snapshot"]
    )
    referenced, referenced_rows = archive_sync_v2.parse_page(
        referenced_raw, expected_stream="media"
    )
    assert [row["identity"] for row in referenced_rows] == [key]
    assert int(referenced_rows[0]["revision"]) == article_revision

    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.content = "Reference removed"
        media = session.get(MediaAssetRecord, key)
        media.content_hash = "b" * 64
        session.add(article)
        session.add(media)
        session.commit()
    unreferenced_raw = archive_sync_v2.export_page(
        sink.engine, "media", since=referenced["snapshot"]
    )
    _unreferenced, unreferenced_rows = archive_sync_v2.parse_page(
        unreferenced_raw, expected_stream="media"
    )
    assert unreferenced_rows == []


@pytest.mark.parametrize(
    ("stream", "identity"),
    [
        ("sources", "rss_platform"),
        ("articles", "article-1"),
        ("analyses", "article-1"),
        ("media", hashlib.sha256(b"https://img.test/commit-race.jpg").hexdigest()),
        ("source_states", "rss_platform"),
    ],
)
def test_uncommitted_writer_is_replayed_after_its_commit_revision(
    tmp_path, stream, identity
):
    """A concurrent uncommitted write lands strictly after the visible snapshot."""

    sink = _sink(tmp_path, f"commit-barrier-{stream}.db")
    media_url = "https://img.test/commit-race.jpg"
    with Session(sink.engine) as session:
        session.add(_source(updated_at="2020-01-01T00:00:00"))
        article = _article(
            content=f"![image]({media_url})",
            archive_updated_at="2020-01-01T00:00:00",
        )
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=7.0,
            content_hash=compute_content_hash(article),
            created_at="2020-01-01T00:00:00+00:00",
            updated_at="2020-01-01T00:00:00+00:00",
        ))
        session.add(MediaAssetRecord(
            url_hash=hashlib.sha256(media_url.encode()).hexdigest(),
            url=media_url,
            status="cached",
            content_hash="a" * 64,
            mime="image/jpeg",
            ext=".jpg",
            size_bytes=10,
            created_at="2020-01-01T00:00:00",
            updated_at="2020-01-01T00:00:00",
        ))
        session.add(SourceStateRecord(
            source_id="rss_platform",
            fetcher_id="rss_platform",
            status="healthy",
            total_runs=1,
            success_runs=1,
            updated_at="2020-01-01T00:00:00",
        ))
        session.commit()

    first_raw = archive_sync_v2.export_page(sink.engine, stream)
    first_manifest, _ = archive_sync_v2.parse_page(first_raw, expected_stream=stream)
    writer_ready = threading.Event()
    allow_commit = threading.Event()
    writer_errors = []

    def write_before_snapshot_but_commit_after_export_starts():
        connection = sink.engine.connect()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with Session(bind=connection) as session:
                if stream == "sources":
                    record = session.get(SourceConfigRecord, identity)
                    record.name = "committed source"
                elif stream == "articles":
                    record = session.get(ArticleRecord, identity)
                    record.content = "committed article body"
                elif stream == "analyses":
                    record = session.get(ArticleAnalysisRecord, identity)
                    record.quality_score = 8.0
                elif stream == "media":
                    record = session.get(MediaAssetRecord, identity)
                    record.content_hash = "b" * 64
                else:
                    record = session.get(SourceStateRecord, identity)
                    record.total_runs = 2
                session.add(record)
                session.flush()
                writer_ready.set()
                if not allow_commit.wait(timeout=5):
                    raise AssertionError("test did not release the writer")
            connection.commit()
        except Exception as exc:  # pragma: no cover - surfaced in the main thread
            writer_errors.append(exc)
            connection.rollback()
        finally:
            connection.close()

    writer = threading.Thread(target=write_before_snapshot_but_commit_after_export_starts)
    writer.start()
    assert writer_ready.wait(timeout=5)
    # The exporter sees neither the uncommitted entity state nor its clock tick.
    interim_raw = archive_sync_v2.export_page(
        sink.engine,
        stream,
        since=first_manifest["snapshot"],
    )
    interim_manifest, interim_rows = archive_sync_v2.parse_page(
        interim_raw,
        expected_stream=stream,
        requested_since=first_manifest["snapshot"],
    )
    assert interim_rows == []
    assert interim_manifest["snapshot"] == first_manifest["snapshot"]

    allow_commit.set()
    writer.join(timeout=5)
    assert not writer.is_alive()
    assert writer_errors == []

    committed_raw = archive_sync_v2.export_page(
        sink.engine,
        stream,
        since=interim_manifest["snapshot"],
    )
    committed_manifest, committed_rows = archive_sync_v2.parse_page(
        committed_raw,
        expected_stream=stream,
        requested_since=interim_manifest["snapshot"],
    )
    assert int(committed_manifest["snapshot"]) > int(interim_manifest["snapshot"])
    assert [row["identity"] for row in committed_rows] == [identity]


def test_taxonomy_import_rejects_out_of_order_and_same_revision_rewrite(tmp_path):
    producer = _sink(tmp_path, "producer-taxonomy-order.db")
    consumer = _sink(tmp_path, "consumer-taxonomy-order.db")
    now = "2026-09-02T02:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(CmsTagRecord(
            code="stable-tag",
            kind="topic",
            name_zh="稳定标签",
            normalized_name="稳定标签",
            status="active",
            taxonomy_version=1,
            created_at=now,
            updated_at=now,
        ))
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, "taxonomy")
    first = archive_sync_v2.import_page(consumer.engine, raw, expected_stream="taxonomy")
    replay = archive_sync_v2.import_page(consumer.engine, raw, expected_stream="taxonomy")
    assert first["inserted"] == 1
    assert replay["inserted"] == replay["updated"] == 0

    no_op = archive_sync_v2.export_page(producer.engine, "taxonomy", since="1")
    no_op_result = archive_sync_v2.import_page(
        consumer.engine, no_op, expected_stream="taxonomy"
    )
    assert no_op_result["inserted"] == no_op_result["updated"] == 0

    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="taxonomy")
    rewritten_rows = json.loads(json.dumps(rows))
    rewritten_rows[0]["payload"]["name_zh"] = "同版本偷改"
    rewritten_rows[0]["checksum"] = archive_sync_v2.checksum(
        rewritten_rows[0]["payload"]
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="does not match"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(manifest, rewritten_rows),
            expected_stream="taxonomy",
        )

    lower_manifest = {**manifest, "snapshot": "0"}
    lower_rows = json.loads(json.dumps(rows))
    for row in lower_rows:
        row["revision"] = "0"
    lower_manifest["next_cursor"] = archive_sync_v2._encode_cursor(
        "0", lower_rows[-1]["identity"]
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="backwards"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(lower_manifest, lower_rows),
            expected_stream="taxonomy",
        )

    higher_noop = {
        **manifest,
        "snapshot": "2",
        "since": "1",
        "next_cursor": "",
        "full_snapshot": False,
        "count": 0,
    }
    with pytest.raises(archive_sync_v2.SyncV2Error, match="requires a full snapshot"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(higher_noop, []),
            expected_stream="taxonomy",
        )

    with Session(consumer.engine) as session:
        tag = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "stable-tag")).one()
        assert tag.name_zh == "稳定标签"


def test_remote_pull_failure_does_not_publish_source_readiness(tmp_path):
    producer = _sink(tmp_path, "producer-failed-v2.db")
    consumer = _sink(tmp_path, "consumer-failed-v2.db")
    with Session(producer.engine) as session:
        session.add(_article())
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00+00:00", total_runs=1,
            success_runs=1, updated_at="2026-09-02T03:00:00+00:00",
        ))
        session.commit()
    remote = _V2Remote(producer, fail_stream="analyses")
    with pytest.raises(remote_sync_service.RemoteSyncError, match="analyses"):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine,
            base_url="https://remote.test",
            username="admin",
            password="secret",
            media_root=tmp_path / "consumer-media-failed",
            transport=httpx.MockTransport(remote.handler),
        ))
    assert "source_states" not in remote.requested_streams
    assert remote.requested_streams.count("analyses") == 1
    with Session(consumer.engine) as session:
        assert session.get(SourceStateRecord, "rss_platform") is None


def test_source_readiness_cannot_advance_past_shared_generation_revision(tmp_path):
    producer = _sink(tmp_path, "producer-readiness-cutoff.db")
    consumer = _sink(tmp_path, "consumer-readiness-cutoff.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_article())
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00", total_runs=1,
            success_runs=1, updated_at="2026-09-02T03:00:00",
        ))
        session.commit()

    class MidRunFetchRemote(_V2Remote):
        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if request.url.path.endswith("/articles.jsonl") and response.status_code == 200:
                manifest = json.loads(response.text.splitlines()[0])
                with Session(self.producer.engine) as session:
                    session.add(_article(
                        "arrived-after-cutoff",
                    ))
                    state = session.get(SourceStateRecord, "rss_platform")
                    state.last_completed_at = "2026-09-03T00:00:00"
                    state.total_runs = 2
                    session.add(state)
                    session.commit()
            return response

    remote = MidRunFetchRemote(producer)
    result = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-media-cutoff",
        transport=httpx.MockTransport(remote.handler),
    ))

    assert result["streams"]["source_states"]["snapshot"] == result["streams"]["articles"]["snapshot"]
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "article-1") is not None
        assert session.get(ArticleRecord, "arrived-after-cutoff") is None
        assert session.get(SourceStateRecord, "rss_platform") is None


def test_article_and_analysis_committed_after_generation_wait_for_next_pull(tmp_path):
    producer = _sink(tmp_path, "producer-analysis-generation-race.db")
    consumer = _sink(tmp_path, "consumer-analysis-generation-race.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_article())
        session.commit()

    class AnalysisRaceRemote(_V2Remote):
        created = False

        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if (
                not self.created
                and request.url.path.endswith("/articles.jsonl")
                and response.status_code == 200
            ):
                with Session(self.producer.engine) as session:
                    article = _article("arrived-with-analysis-after-cutoff")
                    session.add(article)
                    session.flush()
                    session.add(ArticleAnalysisRecord(
                        article_id=article.id,
                        status="succeeded",
                        tagging_status="succeeded",
                        quality_score=9.1,
                        content_hash=compute_content_hash(article),
                        analyzed_at="2026-09-04T12:00:00+00:00",
                        created_at="2026-09-04T12:00:00+00:00",
                        updated_at="2026-09-04T12:00:00+00:00",
                    ))
                    session.commit()
                self.created = True
            return response

    remote = AnalysisRaceRemote(producer)
    first = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-analysis-generation-media",
        transport=httpx.MockTransport(remote.handler),
    ))
    first_params = {stream: params for stream, params in remote.requested_params}
    generation = first["streams"]["sources"]["snapshot"]
    assert first_params["articles"]["snapshot"] == generation
    assert first_params["analyses"]["snapshot"] == generation
    assert first["streams"]["analyses"]["count"] == 0
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "arrived-with-analysis-after-cutoff") is None
        assert session.get(
            ArticleAnalysisRecord, "arrived-with-analysis-after-cutoff"
        ) is None

    second = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-analysis-generation-media",
        checkpoints=first["streams"],
        transport=httpx.MockTransport(remote.handler),
    ))
    assert second["streams"]["articles"]["count"] == 1
    assert second["streams"]["analyses"]["count"] == 1
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "arrived-with-analysis-after-cutoff")
        analysis = session.get(
            ArticleAnalysisRecord, "arrived-with-analysis-after-cutoff"
        )
        assert article is not None
        assert analysis is not None and analysis.quality_score == 9.1


def _prepare_and_pull(consumer, producer, remote, tmp_path, *, label):
    base_url = "https://remote.test"
    with Session(consumer.engine) as session:
        remote_sync_service.prepare_transaction_revision_consumer(
            session,
            base_url=base_url,
            username="admin",
            authority_id=archive_sync_v2.producer_authority_id(producer.engine),
            schema_version=archive_sync_v2.SCHEMA_VERSION,
            prepared_at="2026-10-12T00:00:00+00:00",
            capabilities=list(archive_sync_v2.ADVERTISED_CAPABILITIES),
        )
        session.commit()
    target = remote_sync_service.load_sync_state(consumer.engine)["targets"][base_url]
    return asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url=base_url,
        username="admin",
        password="secret",
        media_root=tmp_path / f"{label}-media",
        checkpoints=target.get("v2_streams") or {},
        on_stream_complete=lambda stream, checkpoint: remote_sync_service.record_v2_stream_success(
            consumer.engine, base_url=base_url, username="admin", stream=stream,
            checkpoint=checkpoint, synced_at="2026-10-12T00:00:00+00:00",
        ),
        transport=httpx.MockTransport(remote.handler),
    ))


@pytest.mark.parametrize("old_generation", [None, 2])
def test_upgraded_receiver_refetches_analyses_dropped_by_its_old_version(tmp_path, old_generation):
    """issue #178 第二波:旧版接收端丢掉了内容价值字段、同步进度已越过那些行;升级后第一次同步自动整流重拉
    分析流把它们补回来,其它流仍是增量。"""
    producer = _sink(tmp_path, "producer-refetch.db")
    consumer = _sink(tmp_path, "consumer-refetch.db")
    now = "2026-10-12T00:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(_source())
        article = _article()
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id, status="succeeded", tagging_status="succeeded", quality_score=5.2,
            score_reason="常规论文", summary="摘要", content_kind="paper", content_value=7.6,
            content_value_reason="方法新", content_value_version="content-value-v2",
            content_hash=compute_content_hash(article), created_at=now, updated_at=now,
        ))
        session.commit()
    remote = _V2Remote(producer)
    _prepare_and_pull(consumer, producer, remote, tmp_path, label="first")
    base_url = "https://remote.test"
    with Session(consumer.engine) as session:
        assert session.get(ArticleAnalysisRecord, "article-1").content_value == 7.6
        # 模拟「旧版本拉过」:内容价值字段被丢掉、记账里没有字段代次,但分析流 checkpoint 已越过这一行
        record = session.get(ArticleAnalysisRecord, "article-1")
        record.content_kind, record.content_value = None, None
        record.content_value_reason, record.content_value_version = "", ""
        session.add(record)
        row = session.get(AppSettingRecord, remote_sync_service.REMOTE_SYNC_STATE_KEY)
        state = json.loads(row.value)
        assert state["targets"][base_url]["v2_analysis_payload_generation"] == archive_sync_v2.ANALYSIS_PAYLOAD_GENERATION
        if old_generation is None:
            state["targets"][base_url].pop("v2_analysis_payload_generation")
        else:
            state["targets"][base_url]["v2_analysis_payload_generation"] = old_generation
        assert "analyses" in state["targets"][base_url]["v2_streams"]
        row.value = json.dumps(state, ensure_ascii=False)
        session.add(row)
        session.commit()

    def states(stream):
        with Session(consumer.engine) as session:
            return {
                (row.identity, int(row.revision))
                for row in session.exec(select(ArchiveSyncEntityStateRecord).where(
                    ArchiveSyncEntityStateRecord.stream == stream
                )).all()
            }

    article_states_before = states("articles")
    analysis_states_before = states("analyses")
    assert analysis_states_before
    remote.requested_params.clear()
    result = _prepare_and_pull(consumer, producer, remote, tmp_path, label="upgraded")
    # 补拉清掉了分析流的逐行应用记账(否则同版本号的行会被当旧数据跳过),拉取后重建;文章流记账不动
    assert states("analyses") == analysis_states_before
    assert states("articles") == article_states_before
    params = dict(remote.requested_params)
    assert "since" not in params["analyses"] or not params["analyses"]["since"]   # 分析流整流重拉
    assert params["articles"].get("since")                                         # 其它流仍是增量
    assert result["streams"]["analyses"]["count"] == 1
    assert result["streams"]["articles"]["count"] == 0
    with Session(consumer.engine) as session:
        record = session.get(ArticleAnalysisRecord, "article-1")
        assert (record.content_value, record.content_value_version) == (7.6, "content-value-v2")
    target = remote_sync_service.load_sync_state(consumer.engine)["targets"][base_url]
    assert target["v2_analysis_payload_generation"] == archive_sync_v2.ANALYSIS_PAYLOAD_GENERATION
    assert "analyses" in target["v2_streams"]   # 补拉完成后 checkpoint 重新建立

    # 再同步一次:代次已是当前,不再重拉
    remote.requested_params.clear()
    again = _prepare_and_pull(consumer, producer, remote, tmp_path, label="again")
    assert dict(remote.requested_params)["analyses"].get("since")
    assert again["streams"]["analyses"]["count"] == 0


def test_refetch_that_fails_midway_resumes_on_the_next_sync(tmp_path):
    """补拉中途失败:checkpoint 仍空,下次从头重新下载,已应用的行被记账跳过,剩下的行补齐(检视 R4-1)。"""
    producer = _sink(tmp_path, "producer-refetch-fail.db")
    consumer = _sink(tmp_path, "consumer-refetch-fail.db")
    now = "2026-10-12T00:00:00+00:00"
    ids = ["a-1", "a-2", "a-3"]
    with Session(producer.engine) as session:
        session.add(_source())
        for article_id in ids:
            article = _article(article_id, content=f"body {article_id}")
            session.add(article)
            session.flush()
            session.add(ArticleAnalysisRecord(
                article_id=article_id, status="succeeded", tagging_status="succeeded", quality_score=5.0,
                summary="摘要", content_kind="paper", content_value=7.0, content_value_reason="方法新",
                content_value_version="content-value-v2", content_hash=compute_content_hash(article),
                created_at=now, updated_at=now,
            ))
        session.commit()
    remote = _V2Remote(producer)
    base_url = "https://remote.test"
    _prepare_and_pull(consumer, producer, remote, tmp_path, label="first")
    with Session(consumer.engine) as session:
        for article_id in ids:
            record = session.get(ArticleAnalysisRecord, article_id)
            record.content_value, record.content_value_version = None, ""
            session.add(record)
        row = session.get(AppSettingRecord, remote_sync_service.REMOTE_SYNC_STATE_KEY)
        state = json.loads(row.value)
        state["targets"][base_url].pop("v2_analysis_payload_generation")
        row.value = json.dumps(state, ensure_ascii=False)
        session.add(row)
        session.commit()

    def pull(label, handler):
        target = remote_sync_service.load_sync_state(consumer.engine)["targets"][base_url]
        return asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine, base_url=base_url, username="admin", password="secret",
            media_root=tmp_path / f"{label}-media", page_size=2,
            checkpoints=target.get("v2_streams") or {},
            on_stream_complete=lambda stream, checkpoint: remote_sync_service.record_v2_stream_success(
                consumer.engine, base_url=base_url, username="admin", stream=stream,
                checkpoint=checkpoint, synced_at=now,
            ),
            transport=httpx.MockTransport(handler),
        ))

    def failing_second_analyses_page(request):
        if request.url.path.endswith("/analyses.jsonl") and request.url.params.get("after"):
            return httpx.Response(500, json={"detail": "network glitch"})
        return remote.handler(request)

    with Session(consumer.engine) as session:
        remote_sync_service.prepare_transaction_revision_consumer(
            session, base_url=base_url, username="admin",
            authority_id=archive_sync_v2.producer_authority_id(producer.engine),
            schema_version=archive_sync_v2.SCHEMA_VERSION, prepared_at=now,
            capabilities=list(archive_sync_v2.ADVERTISED_CAPABILITIES),
        )
        session.commit()
    with pytest.raises(remote_sync_service.RemoteSyncError):
        pull("broken", failing_second_analyses_page)
    target = remote_sync_service.load_sync_state(consumer.engine)["targets"][base_url]
    assert "analyses" not in target["v2_streams"]
    with Session(consumer.engine) as session:
        restored = [aid for aid in ids if session.get(ArticleAnalysisRecord, aid).content_value is not None]
    assert len(restored) == 2   # 第一页已提交

    result = _prepare_and_pull(consumer, producer, remote, tmp_path, label="retry")
    assert result["streams"]["analyses"]["count"] == 3          # 从头重新下载
    with Session(consumer.engine) as session:
        assert all(session.get(ArticleAnalysisRecord, aid).content_value == 7.0 for aid in ids)
    assert "analyses" in remote_sync_service.load_sync_state(consumer.engine)["targets"][base_url]["v2_streams"]
