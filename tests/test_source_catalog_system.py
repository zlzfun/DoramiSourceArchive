"""策展源目录的系统级条目(issue #262 R2):系统源、墓碑、默认订阅的缺省名单、源合集。

这些以前是散在代码里的常量(``ESSENTIAL_FETCHER_IDS`` / ``DECOMMISSIONED_FETCHER_IDS`` /
``SOURCE_FRIENDLY_NAMES`` / ``SOURCE_COLLECTIONS`` / ``DEFAULT_SUBSCRIPTION_SOURCE_IDS``),现在写在
``src/sources/catalog/system.toml`` 与 ``collections.toml``。守三件事:

- 身份没有被搬错:七个墓碑的 id 与名称、公共日报的视图,对照的是搬迁前代码里的字面量与算法
  (下面的 ``TOMBSTONES_AT_MIGRATION`` 与日报那条用例里的字面量),不是拿目录自己比自己;
- 各消费方(取名、读者目录、收录保留 id、对账、合集、默认订阅)读的是目录。合集内容与默认订阅名单是会随
  策展改动的数据,这里不再抄一份——它们搬迁时与旧常量逐字相等由基线对拍确认(方案 §12 末),
  测试只守「消费方读目录」与引用完整性;
- 保留文件缺失、写坏、或引用了目录里没有的源,加载就失败——注册表扫描前会把整个目录读一遍,
  所以进程起不来,而不是带着「没有墓碑」「没有默认订阅」的半份目录跑。
"""

import os
import shutil
import subprocess
import sys
import textwrap

import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers import catalog_files  # noqa: E402
from fetchers.registry import FetcherRegistry, fetcher_registry  # noqa: E402
from services import source_catalog  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 搬迁时(2026-11-24)的七个已下线源:id 来自 registry.DECOMMISSIONED_FETCHER_IDS,名称来自
# source_naming.SOURCE_FRIENDLY_NAMES(两个常量在基线 18fad018 里的字面量,逐字抄在这里)。
# 墓碑只增不减:这里列的 id 必须一直在墓碑里、名称不变,永远不回到现役源——历史归档还挂在这些 id 上。
# 以后下线新的源不必改这张表。
TOMBSTONES_AT_MIGRATION = {
    "docs_gemini_api_changelog": "Gemini API Changelog",
    "docs_openai_api_changelog": "OpenAI API Changelog",
    "github_qwen_code_releases": "Qwen Code Releases",
    "docs_alibaba_model_studio_announcements": "阿里云百炼公告",
    "web_bytedance_seed_models": "ByteDance Seed Models",
    "x_ai_at_meta": "X · AI at Meta",
    "x_openrouter": "X · OpenRouter",
}


# ── 身份没有被搬错:对照搬迁前的字面量与算法 ───────────────────────────────────


def test_tombstones_only_grow_and_never_overlap_live_sources():
    loaded = catalog_files.loaded()
    assert set(TOMBSTONES_AT_MIGRATION) <= set(loaded.tombstones) == source_catalog.tombstone_ids()
    assert not set(loaded.tombstones) & set(loaded.sources)
    assert not set(loaded.tombstones) & set(fetcher_registry._fetchers)        # 下线 = 类也删了
    assert all(entry.name.strip() for entry in loaded.tombstones.values())


@pytest.mark.parametrize("source_id", sorted(TOMBSTONES_AT_MIGRATION))
def test_tombstone_view_equals_what_the_code_used_to_derive(source_id):
    """搬迁前:名称查 ``SOURCE_FRIENDLY_NAMES``,形态 / 平台 / 条目类型按 ``x_`` 前缀推;现在逐条写明。"""
    from services import source_naming

    is_x = source_id.startswith("x_")
    legacy_name = TOMBSTONES_AT_MIGRATION[source_id]
    view = source_catalog._builtin_view(source_id)
    assert view.origin == source_catalog.ORIGIN_CATALOG
    assert view.name == legacy_name
    assert catalog_files.loaded().tombstones[source_id].name == legacy_name
    assert source_naming.friendly_source_name(source_id) == legacy_name
    assert (view.shape, view.platform, view.content_type) == (
        ("social", "x", "social_post") if is_x else ("article", "", "")
    )
    assert view.retired and view.is_active is False and view.fetcher_id == ""


def test_daily_brief_system_source_equals_the_old_constant():
    view = source_catalog._builtin_view(source_catalog.DAILY_BRIEF_SOURCE_ID)
    assert (view.origin, view.name, view.shape, view.content_type) == (
        "system", "哆啦美·AI资讯日报", "article", "daily_brief",
    )
    assert (view.source_owner, view.source_scope, view.provenance_tier) == ("dorami", "aggregator", "tier1_curated")
    assert view.fetcher_id == "" and not view.retired and view.role == "media"
    assert set(catalog_files.REQUIRED_SYSTEM_IDS) == {source_catalog.DAILY_BRIEF_SOURCE_ID}


# ── 消费方读的是目录 ─────────────────────────────────────────────────────────


def test_names_of_builtin_ids_come_from_the_catalog():
    from services import source_naming

    loaded = catalog_files.loaded()
    for entry in (*loaded.system.values(), *loaded.tombstones.values()):
        assert source_catalog.builtin_name(entry.id) == entry.name
        assert source_naming.builtin_source_name(entry.id) == entry.name
        assert source_naming._static_source_name(entry.id) == entry.name
        assert source_naming.friendly_source_name(entry.id) == entry.name
    assert source_naming.friendly_source_name("x_openrouter") == "X · OpenRouter"
    # 现役预设源的名称仍取类上的(= 目录回投的)值,不走内建名
    assert source_catalog.builtin_name("rss_openai_news") == ""
    assert source_naming._static_source_name("rss_openai_news") == fetcher_registry.get_class("rss_openai_news").name
    assert source_naming.builtin_source_name("zz_not_a_source") == ""
    assert not hasattr(source_naming, "SOURCE_FRIENDLY_NAMES")


def test_ingest_never_writes_into_a_tombstone():
    from services import ingest_policy

    for source_id in sorted(source_catalog.tombstone_ids()):
        assert ingest_policy.reserved_source_id_reason(source_id) == "该来源已下线"
        assert ingest_policy.reserved_source_id_reason(source_id, allow_daily_brief=True) == "该来源已下线"
    assert ingest_policy.reserved_source_id_reason("rss_openai_news") is None
    assert ingest_policy.reserved_source_id_reason("zz_brand_new_source") is None


def test_reserved_id_check_does_not_swallow_a_broken_catalog(monkeypatch):
    """墓碑名单读不到时要报错,不能当成「没有墓碑」放行写入。"""
    from services import ingest_policy

    def broken():
        raise catalog_files.CatalogError("system.toml:TOML 解析失败")

    monkeypatch.setattr(catalog_files, "loaded", broken)
    with pytest.raises(catalog_files.CatalogError):
        ingest_policy.reserved_source_id_reason("zz_brand_new_source")


def test_reconcile_writes_tombstone_and_system_rows_from_the_catalog(tmp_path):
    from models.db import SourceConfigRecord
    from services import source_rows
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'tombstones.db'}")
    loaded = catalog_files.loaded()
    with Session(sink.engine) as session:
        summary = source_rows.reconcile_source_rows(session)
        session.commit()
        assert summary["tombstones"] == len(loaded.tombstones) and summary["system"] == "created"
        for entry in loaded.tombstones.values():
            row = session.get(SourceConfigRecord, entry.id)
            assert (row.name, row.shape, row.platform) == (entry.name, entry.shape, entry.platform)
            assert row.origin == "catalog" and row.retired_at and row.is_active is False and not row.fetcher_id
        system = loaded.system[source_catalog.DAILY_BRIEF_SOURCE_ID]
        row = session.get(SourceConfigRecord, system.id)
        assert (row.name, row.shape, row.origin) == (system.name, system.shape, "system")
        assert (row.source_owner, row.source_scope, row.provenance_tier) == (system.owner, system.scope, system.tier)
        # 幂等
        again = source_rows.reconcile_source_rows(session)
        assert again["tombstones"] == 0 and again["system"] == "unchanged"


def test_backfilled_row_takes_a_builtin_name_or_falls_back_to_the_id():
    from services import source_rows

    assert source_rows._legacy_row("x_openrouter", "social_post", now="T").name == "X · OpenRouter"
    assert source_rows._legacy_row("zz_only_in_archive", "", now="T").name == "zz_only_in_archive"


def test_collections_are_the_catalog_entries_in_order():
    from services import source_collections

    entries = catalog_files.loaded().collections
    collections = source_collections.list_collections()
    assert [c.collection_id for c in collections] == [e.id for e in entries]
    for collection, entry in zip(collections, entries):
        assert isinstance(collection.source_ids, tuple)
        assert source_collections.serialize_collection(collection) == {
            "collection_id": entry.id,
            "name": entry.name,
            "description": entry.description,
            "provenance_note": entry.provenance_note,
            "source_ids": list(entry.sources),
        }
        assert source_collections.get_collection(entry.id) == collection
    assert source_collections.get_collection("") is None
    assert source_collections.get_collection("no-such-collection") is None
    assert not hasattr(source_collections, "SOURCE_COLLECTIONS")
    # 搬迁时的五个合集与它们的 id(API 路径段,定了就别改)都还在
    assert {
        "hn-popular-blogs-2025", "frontier-labs-official", "cn-open-models", "ai-coding-tools", "ai-deep-writing",
    } <= {c.collection_id for c in collections}


def test_collection_members_are_live_curated_sources():
    live = set(catalog_files.catalog())
    for collection in catalog_files.loaded().collections:
        assert set(collection.sources) <= live
        assert not set(collection.sources) & source_catalog.tombstone_ids()
        for source_id in collection.sources:                       # 现役 = 注册表里有非模板的类
            assert not getattr(fetcher_registry.get_class(source_id), "is_template", True), source_id


def test_default_subscriptions_are_the_catalog_list():
    import api.app as app_module
    from services import reader_defaults

    expected = list(catalog_files.loaded().default_subscriptions)
    assert expected and len(set(expected)) == len(expected)
    assert reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS == expected
    assert isinstance(reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS, list)      # 调用方按列表比较 / 拷贝
    assert app_module.DEFAULT_SUBSCRIPTION_SOURCE_IDS == expected
    assert list(source_catalog.default_subscription_ids()) == expected
    live = set(catalog_files.catalog()) | set(catalog_files.loaded().system)
    assert set(expected) <= live and not set(expected) & source_catalog.tombstone_ids()


# ── 结构校验 ─────────────────────────────────────────────────────────────────

_SOURCES = '''
[[source]]
id = "rss_one"
name = "一号"
description = "简介"
shape = "article"
admission = "admitted"

[[source]]
id = "rss_two"
name = "二号"
description = "简介"
shape = "article"
admission = "incubating"
'''

_SYSTEM = '''
[[system]]
id = "dorami_daily_brief"
name = "日报"
shape = "article"
owner = "dorami"

[[tombstone]]
id = "x_gone"
name = "X · Gone"
shape = "social"
platform = "x"

[reader_defaults]
subscriptions = ["dorami_daily_brief", "rss_one"]
'''

_COLLECTIONS = '''
[[collection]]
id = "a-pair"
name = "一对"
description = "两个源"
provenance_note = "测试"
sources = ["rss_two", "rss_one"]
'''


def _write_catalog(directory, *, sources=_SOURCES, system=_SYSTEM, collections=_COLLECTIONS):
    for name, body in (("rss.toml", sources), ("system.toml", system), ("collections.toml", collections)):
        if body is not None:
            (directory / name).write_text(body, encoding="utf-8")
    return directory


def test_loader_reads_the_reserved_files(tmp_path):
    loaded = catalog_files.load(_write_catalog(tmp_path))
    assert list(loaded.sources) == ["rss_one", "rss_two"]
    assert loaded.system == {
        "dorami_daily_brief": catalog_files.SystemEntry(
            id="dorami_daily_brief", name="日报", shape="article", owner="dorami",
        ),
    }
    assert loaded.tombstones == {
        "x_gone": catalog_files.TombstoneEntry(id="x_gone", name="X · Gone", shape="social", platform="x"),
    }
    assert loaded.default_subscriptions == ("dorami_daily_brief", "rss_one")
    assert loaded.collections == (
        catalog_files.CollectionEntry(
            id="a-pair", name="一对", description="两个源", provenance_note="测试", sources=("rss_two", "rss_one"),
        ),
    )
    # 源家族的加载不看保留文件:它们不是「system_* / collections_* 源」
    assert catalog_files.load_catalog(tmp_path) == loaded.sources


def test_empty_default_subscriptions_and_no_tombstones_are_legal(tmp_path):
    system = '''
[[system]]
id = "dorami_daily_brief"
name = "日报"
shape = "article"

[reader_defaults]
subscriptions = []
'''
    loaded = catalog_files.load(_write_catalog(tmp_path, system=system))
    assert loaded.tombstones == {} and loaded.default_subscriptions == ()
    assert loaded.system["dorami_daily_brief"].owner == ""


@pytest.mark.parametrize("override, message", [
    # 保留文件缺失
    ({"system": None}, "system.toml"),
    ({"collections": None}, "collections.toml"),
    # system.toml
    ({"system": _SYSTEM + '\n[[source]]\nid = "system_x"\n'}, "顶层只允许"),
    ({"system": _SYSTEM.replace('id = "dorami_daily_brief"', 'id = "some_other_system"')}, "缺系统源"),
    ({"system": _SYSTEM.replace('[[system]]\nid = "dorami_daily_brief"\nname = "日报"\nshape = "article"\nowner = "dorami"\n', "")},
     "没有任何 [[system]]"),
    ({"system": _SYSTEM.replace('name = "日报"\n', "")}, "缺必填键"),
    ({"system": _SYSTEM.replace('name = "日报"', 'name = " "')}, "不能为空"),
    ({"system": _SYSTEM.replace('owner = "dorami"', 'daily = "yes"')}, "未知键"),
    ({"system": _SYSTEM.replace('owner = "dorami"', "owner = 1")}, "必须是字符串"),
    ({"system": _SYSTEM.replace('shape = "social"', 'shape = "bulletin"')}, "不在"),
    ({"system": _SYSTEM.replace('id = "x_gone"', 'id = " x_gone"')}, "首尾不带空白"),
    ({"system": _SYSTEM.replace('id = "x_gone"', 'id = "dorami_daily_brief"')}, "与系统源重 id"),
    ({"system": _SYSTEM.replace('id = "x_gone"', 'id = "rss_one"')}, "已下线的 id 也不能再当现役源用"),
    ({"system": _SYSTEM + '\n[[tombstone]]\nid = "x_gone"\nname = "又一个"\nshape = "social"\n'}, "id 重复"),
    ({"system": _SYSTEM + '\n[[system]]\nid = "dorami_daily_brief"\nname = "又一个"\nshape = "article"\n'}, "id 重复"),
    ({"system": _SYSTEM.replace("[[tombstone]]", "[[tombstone]\nbroken", 1)}, "TOML 解析失败"),
    ({"system": _SYSTEM.replace('[reader_defaults]\nsubscriptions = ["dorami_daily_brief", "rss_one"]\n', "")},
     "缺 [reader_defaults]"),
    ({"system": _SYSTEM.replace('subscriptions = ["dorami_daily_brief", "rss_one"]', "limit = 3")}, "未知键"),
    ({"system": _SYSTEM.replace('subscriptions = ["dorami_daily_brief", "rss_one"]', "")}, "缺必填键"),
    ({"system": _SYSTEM.replace('["dorami_daily_brief", "rss_one"]', '"rss_one"')}, "必须是字符串数组"),
    ({"system": _SYSTEM.replace('["dorami_daily_brief", "rss_one"]', '["rss_one", 3]')}, "每一项必须是"),
    ({"system": _SYSTEM.replace('["dorami_daily_brief", "rss_one"]', '["rss_one", "rss_one"]')}, "重复"),
    ({"system": _SYSTEM.replace('["dorami_daily_brief", "rss_one"]', '["rss_one", "rss_missing"]')}, "不是目录里的现役策展源或系统源"),
    ({"system": _SYSTEM.replace('["dorami_daily_brief", "rss_one"]', '["x_gone"]')}, "不是目录里的现役策展源或系统源"),
    # 系统源不能与现役源重 id
    ({"system": _SYSTEM + '\n[[system]]\nid = "rss_one"\nname = "撞 id"\nshape = "article"\n'}, "现役源重 id"),
    # collections.toml
    ({"collections": _COLLECTIONS + '\n[reader_defaults]\nsubscriptions = []\n'}, "顶层只允许"),
    ({"collections": "# 只有注释\n"}, "没有任何 [[collection]]"),
    ({"collections": "collection = []\n"}, "没有任何 [[collection]]"),
    ({"collections": 'collection = "nope"\n'}, "数组"),
    ({"collections": _COLLECTIONS.replace('provenance_note = "测试"\n', "")}, "缺必填键"),
    ({"collections": _COLLECTIONS.replace('sources = ["rss_two", "rss_one"]\n', "")}, "缺必填键"),
    ({"collections": _COLLECTIONS.replace('id = "a-pair"', 'id = "A_Pair"')}, "kebab"),
    ({"collections": _COLLECTIONS + _COLLECTIONS}, "id 重复"),
    ({"collections": _COLLECTIONS.replace('["rss_two", "rss_one"]', "[]")}, "sources 不能为空"),
    ({"collections": _COLLECTIONS.replace('["rss_two", "rss_one"]', '["rss_two", "rss_two"]')}, "重复"),
    ({"collections": _COLLECTIONS.replace('["rss_two", "rss_one"]', '["rss_two", "rss_missing"]')}, "不是目录里的现役策展源"),
    ({"collections": _COLLECTIONS.replace('["rss_two", "rss_one"]', '["x_gone"]')}, "不是目录里的现役策展源"),
    ({"collections": _COLLECTIONS.replace('["rss_two", "rss_one"]', '["dorami_daily_brief"]')}, "不是目录里的现役策展源"),
    ({"collections": _COLLECTIONS.replace('name = "一对"', 'name = ""')}, "不能为空"),
    ({"collections": _COLLECTIONS.replace('description = "两个源"', 'tags = "x"\ndescription = "两个源"')}, "未知键"),
])
def test_loader_rejects_malformed_reserved_files(tmp_path, override, message):
    _write_catalog(tmp_path, **override)
    with pytest.raises(catalog_files.CatalogError) as raised:
        catalog_files.load(tmp_path)
    assert message in str(raised.value), str(raised.value)


# ── 目录坏了就起不来 ─────────────────────────────────────────────────────────


def _copy_repo_catalog(tmp_path, *, skip=()):
    for path in catalog_files.CATALOG_DIR.glob("*.toml"):
        if path.name not in skip:
            shutil.copy(path, tmp_path / path.name)
    return tmp_path


@pytest.mark.parametrize("skip", ["system.toml", "collections.toml"])
def test_registry_scan_stops_when_a_reserved_file_is_missing(tmp_path, monkeypatch, skip):
    """扫描前先取目录 = 把整个目录读一遍:保留文件缺了同样起不来,不是只校验 ``[[source]]``。"""
    broken_dir = _copy_repo_catalog(tmp_path, skip=(skip,))
    monkeypatch.setattr(catalog_files, "loaded", lambda: catalog_files.load(broken_dir))
    registry = FetcherRegistry()
    with pytest.raises(catalog_files.CatalogError) as raised:
        registry.discover()
    assert skip in str(raised.value) and registry._fetchers == {}


def _import_registry_with_catalog(directory):
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {os.path.join(_ROOT, "src")!r})
        from fetchers import catalog_files
        catalog_files.loaded = lambda: catalog_files.load({str(directory)!r})
        from fetchers.registry import fetcher_registry
        print("registered", len(fetcher_registry._fetchers))
    """)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


def test_process_cannot_import_the_registry_with_a_dangling_reference(tmp_path):
    """真的起一个进程:合集引用了目录里没有的源 → 导入注册表就抛错;同一份目录不改就能正常导入(对照)。"""
    good = tmp_path / "good"
    good.mkdir()
    _copy_repo_catalog(good)
    done = _import_registry_with_catalog(good)
    assert done.returncode == 0, done.stderr
    assert f"registered {len(fetcher_registry._fetchers)}" in done.stdout

    dangling = tmp_path / "dangling"
    dangling.mkdir()
    _copy_repo_catalog(dangling)
    path = dangling / "collections.toml"
    text = path.read_text(encoding="utf-8")
    assert text.count('"rss_sean_goedecke"') == 1
    path.write_text(text.replace('"rss_sean_goedecke"', '"rss_no_such_source"'), encoding="utf-8")
    done = _import_registry_with_catalog(dangling)
    assert done.returncode != 0
    assert "CatalogError" in done.stderr and "rss_no_such_source" in done.stderr
    assert "registered" not in done.stdout
