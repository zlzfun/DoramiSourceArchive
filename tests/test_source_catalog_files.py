"""策展源目录文件(issue #262 R2):预设源的身份写在 ``src/sources/catalog/*.toml``,类定义时回投到类属性。

守四件事:目录与注册表一一对应且类属性等于目录值;预设类上不再手写身份;目录文件的结构校验
(缺键、未知键、取值、重复 id、放错文件)在加载时就拦下;目录坏了或预设类缺条目时起不来,
而不是带着缺省身份跑。
"""

import ast
import glob
import importlib
import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers import catalog_files  # noqa: E402
from fetchers.base import BaseFetcher, apply_catalog_identity  # noqa: E402
from fetchers.registry import FetcherRegistry, fetcher_registry  # noqa: E402

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


_SCOPE_BOUNDARIES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _target_names(target):
    """赋值目标里的名字(展开元组 / 列表解包与星号)。"""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _target_names(element)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _class_scope_assignments(node):
    """类体执行作用域里被赋值的名字:递归进条件 / 循环 / with / try 块,不进函数体与嵌套类。"""
    names = set()
    stack = list(node.body)
    while stack:
        statement = stack.pop()
        if isinstance(statement, _SCOPE_BOUNDARIES):
            continue
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                names.update(_target_names(target))
        elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
            names.update(_target_names(statement.target))
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.NamedExpr):
                names.update(_target_names(child.target))
            if isinstance(child, (ast.stmt, ast.ExceptHandler)):
                stack.append(child)
    return names


def identity_writes(source, kinds):
    """一段模块源码里「把身份写在代码里」的全部位置。``kinds``:类名 → preset / intermediate / template。

    - 预设类的类体(含条件块、解包、增量与带注解的赋值)里不许出现任何身份属性;
    - 未注册的中间父类只许声明抓取器家族的事实(形态 / 平台 / 内容类型先验);
    - 类体之外,对任何抓取器类(含 ``cls``)写身份属性——``X.name = …`` 或 ``setattr(X, "name", …)``——同样不许:
      模板的名称写在它自己的类体里,不在别处改。
    """
    tree = ast.parse(source)
    offenders = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and kinds.get(node.name) in ("preset", "intermediate"):
            allowed = set() if kinds[node.name] == "preset" else FAMILY_ATTRS
            forbidden = (_class_scope_assignments(node) & IDENTITY_ATTRS) - allowed
            if forbidden:
                offenders.append((node.name, "类体", sorted(forbidden)))
    class_like = set(kinds) | {"cls"}
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            for leaf in ([target] if not isinstance(target, (ast.Tuple, ast.List)) else target.elts):
                if (
                    isinstance(leaf, ast.Attribute) and leaf.attr in IDENTITY_ATTRS
                    and isinstance(leaf.value, ast.Name) and leaf.value.id in class_like
                ):
                    offenders.append((leaf.value.id, "类体外赋值", [leaf.attr]))
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr"
            and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in class_like
            and isinstance(node.args[1], ast.Constant) and node.args[1].value in IDENTITY_ATTRS
        ):
            offenders.append((node.args[0].id, "setattr", [node.args[1].value]))
    return offenders


def test_preset_classes_do_not_declare_identity_in_code():
    """身份不写回代码:扫 ``src/fetchers/impl/`` 的每个模块(规则见 ``identity_writes``)。"""
    catalog_ids = set(catalog_files.catalog())
    registered = {cls: sid for sid, cls in fetcher_registry._fetchers.items()}
    offenders = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "src", "fetchers", "impl", "*.py"))):
        module = importlib.import_module(f"fetchers.impl.{os.path.splitext(os.path.basename(path))[0]}")
        kinds = {}
        for name, defined in vars(module).items():
            if not (isinstance(defined, type) and issubclass(defined, BaseFetcher)) or defined is BaseFetcher:
                continue                                                  # 只看抓取器类(同文件里还有解析用的数据类)
            source_id = registered.get(defined)
            if source_id in catalog_ids:
                kinds[name] = "preset"
            elif source_id is None:
                kinds[name] = "intermediate"
            else:
                kinds[name] = "template"
        with open(path, encoding="utf-8") as handle:
            offenders += [(os.path.basename(path), *found) for found in identity_writes(handle.read(), kinds)]
    assert not offenders, f"这些位置在代码里写了身份属性,请挪到 src/sources/catalog/*.toml:{offenders}"


@pytest.mark.parametrize("snippet, expected", [
    ('class P(Base):\n    source_id = "x_a"\n    name = "写回来"\n', [("P", "类体", ["name"])]),
    ('class P(Base):\n    source_id = "x_a"\n    admission_status: str = "admitted"\n',
     [("P", "类体", ["admission_status"])]),
    ('class P(Base):\n    source_id = "x_a"\n    if True:\n        icon = "X"\n', [("P", "类体", ["icon"])]),
    ('class P(Base):\n    source_id = "x_a"\n    try:\n        source_owner = "o"\n    except Exception:\n        noise_risk = "n"\n',
     [("P", "类体", ["noise_risk", "source_owner"])]),
    ('class P(Base):\n    source_id = "x_a"\n    name, icon = "a", "b"\n', [("P", "类体", ["icon", "name"])]),
    ('class P(Base):\n    source_id = "x_a"\n    handle = source_scope = "s"\n', [("P", "类体", ["source_scope"])]),
    ('class P(Base):\n    source_id = "x_a"\n    description += "尾巴"\n', [("P", "类体", ["description"])]),
    ('class P(Base):\n    source_id = "x_a"\n    content_shape = "social"\n', [("P", "类体", ["content_shape"])]),
    ('class M(Base):\n    content_shape = "social"\n    platform = "x"\n', []),                 # 中间父类:家族事实可以
    ('class M(Base):\n    admission_status = "incubating"\n', [("M", "类体", ["admission_status"])]),
    ('class T(Base):\n    is_template = True\n    name = "通用模板"\n', []),                     # 模板:名称是它自己的
    ('class P(Base):\n    source_id = "x_a"\n\nP.name = "类体外"\n', [("P", "类体外赋值", ["name"])]),
    ('class P(Base):\n    source_id = "x_a"\n\ndef f():\n    P.source_url = "u"\n', [("P", "类体外赋值", ["source_url"])]),
    ('class P(Base):\n    source_id = "x_a"\n\nsetattr(P, "admission_status", "admitted")\n',
     [("P", "setattr", ["admission_status"])]),
    ('class T(Base):\n    is_template = True\n\nT.name = "别处改模板"\n', [("T", "类体外赋值", ["name"])]),
    ('class P(Base):\n    source_id = "x_a"\n    @classmethod\n    def f(cls):\n        cls.icon = "i"\n',
     [("cls", "类体外赋值", ["icon"])]),
    # 不该误伤:方法里的局部变量、内容对象的同名字段、抓取相关的类属性
    ('class P(Base):\n    source_id = "x_a"\n    feed_url = "https://f"\n    def run(self, item):\n        name = "local"\n        item.source_url = "u"\n        return name\n', []),
])
def test_identity_scanner_catches_every_way_of_writing_identity_in_code(snippet, expected):
    kinds = {"P": "preset", "M": "intermediate", "T": "template"}
    assert sorted(identity_writes(snippet, kinds)) == sorted(expected)


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
    cls = type("Fake_child", (parent,), {"source_id": "rss_fake_projection"})   # 类定义的那一刻就回投了
    assert (cls.name, cls.description, cls.admission_status, cls.source_owner) == (
        "假源", "只在测试里", "incubating", "fake_owner"
    )
    assert "content_shape" not in vars(cls)                              # 继承来的值本来就对:不重复写
    # 目录里没写的键取 BaseFetcher 的缺省,父类上的值不会漏下来
    assert cls.icon == BaseFetcher.icon and cls.source_scope == ""
    assert parent.icon == "P" and parent.source_scope == "parent_scope"  # 父类不动
    assert apply_catalog_identity(cls) is True                           # 再调一次是幂等的

    # 继承 source_id 的子类(测试替身)不单独回投,沿用父类已回投的值
    double = type("Fake_double", (cls,), {"name": "替身自己的名字"})
    assert apply_catalog_identity(double) is False
    assert double.name == "替身自己的名字" and double.source_owner == "fake_owner"

    template = _fake("rss_fake_projection", is_template=True, name="模板自己的名字")
    assert apply_catalog_identity(template) is False and template.name == "模板自己的名字"
    stranger = _fake("rss_not_in_catalog", is_template=False, name="不在目录里")      # 测试模块里的类:没有条目就不回投
    assert apply_catalog_identity(stranger) is False and stranger.name == "不在目录里"


def test_impl_preset_without_a_catalog_entry_cannot_be_defined():
    """``fetchers.impl`` 里的预设类必须有目录条目:缺一条就在类定义时抛错,不带着「未命名 + admitted」上线。"""
    base = fetcher_registry.get_class("generic_rss")
    with pytest.raises(catalog_files.CatalogError) as raised:
        type("OrphanPreset", (base,), {
            "source_id": "rss_orphan_without_entry", "is_template": False, "__module__": "fetchers.impl.orphan",
        })
    assert "rss_orphan_without_entry" in str(raised.value) and "没有条目" in str(raised.value)
    # 模板、以及同一个包里继承 source_id 的子类,不受这条约束
    type("OrphanTemplate", (base,), {
        "source_id": "generic_orphan_template", "is_template": True, "__module__": "fetchers.impl.orphan",
    })
    type("InheritsId", (fetcher_registry.get_class("rss_openai_news"),), {"__module__": "fetchers.impl.orphan"})


def test_a_missing_catalog_file_stops_the_registry(tmp_path, monkeypatch):
    """少了一个目录文件 = 那一族预设类没有条目 = 定义不出来;不会悄悄变成正式源进每日采集。"""
    for path in catalog_files.CATALOG_DIR.glob("*.toml"):
        if path.name != "x.toml":
            (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    partial = catalog_files.load_catalog(tmp_path)
    assert partial and not any(sid.startswith("x_") for sid in partial)
    monkeypatch.setattr(catalog_files, "catalog", lambda: partial)

    x_base = next(cls for cls in fetcher_registry.get_class("x_openai").__mro__ if cls.__name__ == "PresetXTimelineFetcher")
    with pytest.raises(catalog_files.CatalogError):
        type("OpenAIXTimelineFetcher", (x_base,), {
            "source_id": "x_openai", "handle": "OpenAI", "user_id": "4398626122",
            "__module__": "fetchers.impl.x_timeline_fetcher",
        })


def test_discover_fails_loudly_when_the_catalog_is_broken(tmp_path, monkeypatch, capsys):
    """目录错误不走「单个模块加载失败只告警」的隔离:扫描前先加载目录,扫描中的 CatalogError 原样抛出。"""
    def broken():
        raise catalog_files.CatalogError("x.toml 的 x_openai:缺必填键 ['admission']")

    monkeypatch.setattr(catalog_files, "catalog", broken)
    registry = FetcherRegistry()
    with pytest.raises(catalog_files.CatalogError):
        registry.discover()
    assert registry._fetchers == {}                                      # 没有「只剩几个模板还照常启动」
    monkeypatch.undo()

    # 扫描途中某个模块抛 CatalogError(预设类缺条目)同样穿透;别的异常仍只告警、不影响其它模块
    package = tmp_path / "fake_fetcher_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "a_broken_dependency.py").write_text("raise RuntimeError('可选依赖没装')\n", encoding="utf-8")
    (package / "b_fine.py").write_text(textwrap.dedent("""
        from fetchers.registry import fetcher_registry
        class Fine(fetcher_registry.get_class("generic_rss")):
            source_id = "generic_fake_fine"
            is_template = True
    """), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    tolerant = FetcherRegistry()
    tolerant.discover("fake_fetcher_pkg")
    assert list(tolerant._fetchers) == ["generic_fake_fine"]
    assert "a_broken_dependency" in capsys.readouterr().out

    (package / "c_orphan_preset.py").write_text(
        "from fetchers import catalog_files\nraise catalog_files.CatalogError('预设抓取器 X 在目录里没有条目')\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    with pytest.raises(catalog_files.CatalogError):
        FetcherRegistry().discover("fake_fetcher_pkg")


def _run_python(code):
    env = dict(os.environ, PYTHONPATH=os.path.join(_ROOT, "src"))
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, env=env)


def test_importing_a_preset_class_directly_carries_catalog_identity():
    """不经注册表、直接导入具体类的入口(对比脚本、审计示例)拿到的也是目录里的身份。"""
    done = _run_python("""
        import sys
        from fetchers.impl.x_timeline_fetcher import SamAltmanXTimelineFetcher as preset
        assert "fetchers.registry" not in sys.modules
        print(preset.name, preset.admission_status, preset.source_owner, sep="|")
    """)
    assert done.returncode == 0, done.stderr
    entry = catalog_files.catalog()["x_sama"].fields
    assert done.stdout.strip() == f"{entry['name']}|{entry['admission']}|{entry['owner']}"


def test_loader_falls_back_to_tomli_without_tomllib():
    """Python 3.10 没有 ``tomllib``:加载器改用 ``tomli``(这里把标准库的那份挡掉,再冒名顶上去模拟)。"""
    done = _run_python("""
        import sys, tomllib as real
        sys.modules["tomli"] = real
        sys.modules["tomllib"] = None            # import tomllib → ImportError
        from fetchers import catalog_files
        assert catalog_files.tomllib is real
        print(len(catalog_files.load_catalog()))
    """)
    assert done.returncode == 0, done.stderr
    assert int(done.stdout.strip()) == len(catalog_files.catalog())
    with open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8") as handle:
        assert "\"tomli>=2.0; python_version < '3.11'\"" in handle.read()


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
    ("rss.toml", "# 只有注释\n", "没有任何 [[source]]"),
    ("rss.toml", "source = []\n", "没有任何 [[source]]"),
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
    with open(os.path.join(_ROOT, "docker", "backend.Dockerfile"), encoding="utf-8") as handle:
        assert "COPY src ./src" in handle.read()
    with open(os.path.join(_ROOT, ".dockerignore"), encoding="utf-8") as handle:
        ignored = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    assert not [line for line in ignored if "toml" in line or line.rstrip("/") in {"src", "src/sources"}]
    assert not (catalog_files.CATALOG_DIR.parent / "__init__.py").exists()   # src/sources 只放数据,不是 Python 包

    on_disk = sorted(os.path.basename(path) for path in glob.glob(str(catalog_files.CATALOG_DIR / "*.toml")))
    tracked = subprocess.run(
        ["git", "ls-files", "src/sources/catalog"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert on_disk and sorted(os.path.basename(path) for path in tracked) == on_disk
