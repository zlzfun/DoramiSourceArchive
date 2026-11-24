"""策展源目录文件(issue #262 R2):预设源的身份写在 ``src/sources/catalog/*.toml``,注册时回投到类属性。

守三件事:目录与注册表一一对应且类属性等于目录值;预设类上不再手写身份;目录文件的结构校验
(缺键、未知键、取值、重复 id、放错文件)在加载时就拦下。
"""

import ast
import glob
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sources as catalog_files  # noqa: E402
from fetchers.base import BaseFetcher  # noqa: E402
from fetchers.registry import fetcher_registry, project_catalog_identity  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IDENTITY_ATTRS = set(catalog_files.FIELD_TO_ATTR.values())
# 抓取器家族自己的事实(中间父类可以声明):X 时间线抓到的就是社交帖。
FAMILY_ATTRS = {"content_shape", "platform", "default_kind"}


def _presets():
    return {
        sid: cls for sid, cls in fetcher_registry._fetchers.items() if not getattr(cls, "is_template", False)
    }


def test_catalog_and_registry_cover_the_same_sources():
    catalog = catalog_files.catalog()
    presets = _presets()
    assert len(catalog) >= 60
    assert set(catalog) == set(presets), {
        "目录里有、没有抓取器类": sorted(set(catalog) - set(presets)),
        "有抓取器类、目录里没有": sorted(set(presets) - set(catalog)),
    }
    templates = {sid for sid, cls in fetcher_registry._fetchers.items() if getattr(cls, "is_template", False)}
    assert templates and not templates & set(catalog)                    # 模板是执行基座,不是来源


def test_class_attributes_equal_the_catalog():
    """回投的结果:每个预设类上生效的身份属性 = 目录里写的值(没写的键 = BaseFetcher 的缺省)。"""
    for source_id, entry in catalog_files.catalog().items():
        cls = fetcher_registry.get_class(source_id)
        expected = catalog_files.identity_attributes(entry, BaseFetcher)
        assert {attr: getattr(cls, attr) for attr in expected} == expected, source_id
        assert entry.fields["admission"] in catalog_files.ADMISSIONS     # 准入每条必填、显式
        assert entry.file == f"{source_id.split('_')[0]}.toml"


def test_preset_classes_do_not_declare_identity_in_code():
    """身份不写回代码:预设类(带目录里的 source_id)的类体里不许出现身份属性;没注册的中间父类只许
    声明抓取器家族的事实(形态 / 平台 / 内容类型先验),不许带名称、准入、策展维度。"""
    catalog_ids = set(catalog_files.catalog())
    registered = {cls.__name__: sid for sid, cls in fetcher_registry._fetchers.items()}
    offenders = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "src", "fetchers", "impl", "*.py"))):
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        module = importlib.import_module(f"fetchers.impl.{os.path.splitext(os.path.basename(path))[0]}")
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            defined = getattr(module, node.name, None)
            if not (isinstance(defined, type) and issubclass(defined, BaseFetcher)):
                continue                                                  # 只看抓取器类(同文件里还有解析用的数据类)
            assigned = {
                target.id
                for statement in node.body
                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                for target in (statement.targets if isinstance(statement, ast.Assign) else [statement.target])
                if isinstance(target, ast.Name)
            }
            source_id = registered.get(node.name)
            if source_id in catalog_ids:
                forbidden = assigned & IDENTITY_ATTRS
            elif source_id is None:                                       # 未注册的中间父类
                forbidden = assigned & (IDENTITY_ATTRS - FAMILY_ATTRS)
            else:                                                         # 模板:名称等是它自己的
                forbidden = set()
            if forbidden:
                offenders.append((os.path.basename(path), node.name, sorted(forbidden)))
    assert not offenders, f"这些类在代码里写了身份属性,请挪到 src/sources/catalog/*.toml:{offenders}"


def test_no_catalog_class_inherits_from_another_catalog_class():
    """回投「只在值不同时才写」与注册先后无关的前提:目录里的类不继承自另一个目录里的类。"""
    presets = _presets()
    preset_classes = set(presets.values())
    nested = [
        (sid, parent.source_id) for sid, cls in presets.items()
        for parent in cls.__mro__[1:] if parent in preset_classes
    ]
    assert not nested, f"预设类继承了另一个预设类,回投结果会依赖注册顺序:{nested}"


def test_inherited_family_facts_are_not_duplicated_onto_each_class():
    """形态继承自抓取器家族且与目录一致时,不在每个子类上各落一份。"""
    x_openai = fetcher_registry.get_class("x_openai")
    assert x_openai.content_shape == "social" and "content_shape" not in vars(x_openai)
    assert vars(x_openai)["admission_status"] == "incubating"            # 准入来自目录,落在类自己身上
    assert vars(x_openai)["name"] == catalog_files.catalog()["x_openai"].fields["name"]


def _fake(source_id, base="generic_rss", **attrs):
    return type(f"Fake_{source_id}", (fetcher_registry.get_class(base),), {"source_id": source_id, **attrs})


def test_projection_sets_only_what_differs_and_fills_defaults(monkeypatch):
    entry = catalog_files.CatalogEntry(
        id="rss_fake_projection", file="rss.toml",
        fields={"name": "假源", "description": "只在测试里", "shape": "article", "admission": "incubating",
                "owner": "fake_owner"},
    )
    monkeypatch.setattr(catalog_files, "catalog", lambda: {entry.id: entry})

    parent = _fake("unknown_source", is_template=False, icon="P", source_scope="parent_scope")
    cls = type("Fake_child", (parent,), {"source_id": "rss_fake_projection"})
    assert project_catalog_identity(cls) is True
    assert (cls.name, cls.description, cls.admission_status, cls.source_owner) == (
        "假源", "只在测试里", "incubating", "fake_owner"
    )
    assert "content_shape" not in vars(cls)                              # 继承来的值本来就对:不重复写
    # 目录里没写的键取 BaseFetcher 的缺省,父类上的值不会漏下来
    assert cls.icon == BaseFetcher.icon and cls.source_scope == ""
    assert parent.icon == "P" and parent.source_scope == "parent_scope"  # 父类不动

    template = _fake("rss_fake_projection", is_template=True, name="模板自己的名字")
    assert project_catalog_identity(template) is False and template.name == "模板自己的名字"
    stranger = _fake("rss_not_in_catalog", is_template=False, name="不在目录里")
    assert project_catalog_identity(stranger) is False and stranger.name == "不在目录里"


def _write(directory, name, body):
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


_VALID = '''
[[source]]
id = "rss_ok"
name = "一个源"
description = "简介"
shape = "article"
admission = "admitted"
owner = "someone"
'''


def test_loader_reads_entries_in_file_order(tmp_path):
    _write(tmp_path, "rss.toml", _VALID + '''
[[source]]
id = "rss_second"
name = "第二个"
description = "简介"
shape = "podcast"
admission = "incubating"
''')
    _write(tmp_path, "x.toml", '''
[[source]]
id = "x_someone"
name = "X · Someone"
description = "简介"
shape = "social"
admission = "incubating"
platform = "x"
''')
    entries = catalog_files.load_catalog(tmp_path)
    assert list(entries) == ["rss_ok", "rss_second", "x_someone"]
    assert entries["rss_ok"].fields == {
        "name": "一个源", "description": "简介", "shape": "article", "admission": "admitted", "owner": "someone",
    }
    attrs = catalog_files.identity_attributes(entries["x_someone"], BaseFetcher)
    assert attrs["content_shape"] == "social" and attrs["platform"] == "x"
    assert attrs["icon"] == BaseFetcher.icon and attrs["source_owner"] == ""


@pytest.mark.parametrize("name, body, message", [
    ("rss.toml", _VALID.replace('admission = "admitted"\n', ""), "缺必填键"),
    ("rss.toml", _VALID.replace('shape = "article"', 'shape = "bulletin"'), "不在"),
    ("rss.toml", _VALID.replace('admission = "admitted"', 'admission = "provisional"'), "不在"),
    ("rss.toml", _VALID + 'daily = "yes"\n', "未知键"),
    ("rss.toml", _VALID.replace('owner = "someone"', "owner = 3"), "必须是字符串"),
    ("rss.toml", _VALID.replace('name = "一个源"', 'name = "  "'), "不能为空"),
    ("rss.toml", _VALID.replace('id = "rss_ok"', 'id = " rss_ok"'), "首尾不带空白"),
    ("rss.toml", _VALID + _VALID, "id 重复"),
    ("web.toml", _VALID, "前缀与文件名不符"),
    ("rss.toml", _VALID + '\n[collections]\nx = 1\n', "顶层只允许"),
    ("rss.toml", "[[source]\nid = ", "TOML 解析失败"),
    ("rss.toml", 'source = "not-a-list"\n', "数组"),
])
def test_loader_rejects_malformed_catalogs(tmp_path, name, body, message):
    _write(tmp_path, name, body)
    with pytest.raises(catalog_files.CatalogError) as raised:
        catalog_files.load_catalog(tmp_path)
    assert message in str(raised.value)


def test_loader_rejects_duplicates_across_files_and_an_empty_directory(tmp_path):
    with pytest.raises(catalog_files.CatalogError) as raised:
        catalog_files.load_catalog(tmp_path)
    assert "没有 *.toml" in str(raised.value)

    # 同一个 id 不可能合法地出现在两个文件里(前缀规则先拦),重复只会发生在同文件;这里确认报错带出两处
    _write(tmp_path, "rss.toml", _VALID + _VALID.replace("一个源", "又一个"))
    with pytest.raises(catalog_files.CatalogError) as raised:
        catalog_files.load_catalog(tmp_path)
    assert "rss_ok" in str(raised.value) and "rss.toml" in str(raised.value)


def test_catalog_files_ship_with_both_deployment_shapes():
    """目录文件是运行时必需的数据:镜像整目录拷 src,裸机走 git archive(只带已入库文件)。"""
    import subprocess

    with open(os.path.join(_ROOT, "docker", "backend.Dockerfile"), encoding="utf-8") as handle:
        assert "COPY src ./src" in handle.read()
    with open(os.path.join(_ROOT, ".dockerignore"), encoding="utf-8") as handle:
        ignored = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    assert not [line for line in ignored if "toml" in line or line.rstrip("/") in {"src", "src/sources"}]

    on_disk = sorted(os.path.basename(path) for path in glob.glob(str(catalog_files.CATALOG_DIR / "*.toml")))
    tracked = subprocess.run(
        ["git", "ls-files", "src/sources/catalog"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert on_disk and sorted(os.path.basename(path) for path in tracked) == on_disk
