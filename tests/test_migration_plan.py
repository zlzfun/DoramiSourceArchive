"""只读迁移计划 `storage.migrations.plan_migrations`(issue #102 自动部署,PR-1)。

部署脚本在切换容器之前要知道「这个库对目标代码来说是全新 / 待收养 / 落后 / 领先(即不兼容)」,
且 **不能** 借道 `alembic current`(alembic/env.py 在线路径会动 Archive Sync 触发器)。本文件锁定:
四种状态各一例(fresh / legacy / compatible / incompatible)、pending 拓扑序、多头按集合、以及「只读」——不建库文件、不给老库加 alembic_version。
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alembic import command  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from storage.migrations import (  # noqa: E402
    BASELINE_REVISION,
    PLAN_DEPLOYABLE_STATUSES,
    make_alembic_config,
    plan_migrations,
)

ROOT = Path(__file__).resolve().parents[1]


def _script(script_location=None) -> ScriptDirectory:
    cfg = make_alembic_config()
    if script_location:
        cfg.set_main_option("script_location", str(script_location))
    return ScriptDirectory.from_config(cfg)


def _all_revisions_ascending(script: ScriptDirectory) -> list:
    return [rev.revision for rev in reversed(list(script.walk_revisions("base", "heads")))]


def _upgrade(db_url: str, target: str) -> None:
    command.upgrade(make_alembic_config(db_url), target)


def _copy_alembic(tmp_path: Path) -> Path:
    dest = tmp_path / "alembic"
    shutil.copytree(ROOT / "alembic", dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def test_fresh_when_database_file_is_absent_and_file_is_not_created(tmp_path):
    db_file = tmp_path / "absent.db"
    plan = plan_migrations(f"sqlite:///{db_file}")
    assert plan["status"] == "fresh"
    assert plan["database_exists"] is False
    assert plan["current_heads"] == []
    assert plan["pending"] == _all_revisions_ascending(_script())
    assert plan["pending_count"] == len(plan["pending"])
    assert plan["pending"][-1] == _script().get_current_head()
    assert not db_file.exists(), "只读计划不得把库文件建出来"


def test_memory_url_is_fresh():
    plan = plan_migrations("sqlite:///:memory:")
    assert plan["status"] == "fresh"
    assert plan["database_exists"] is False


def test_legacy_database_without_version_table_is_adoption_required_and_untouched(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    storage = DatabaseStorage(db_url=db_url)  # create_all 老库形态:有表无 alembic_version
    storage.engine.dispose()

    plan = plan_migrations(db_url)
    assert plan["status"] == "legacy_adoption_required"
    assert plan["current_heads"] == []
    # 待执行的是基线之后的全部迁移(基线由 ensure_migrated 收养时 stamp)
    assert plan["pending"][0] != BASELINE_REVISION
    assert plan["pending"][-1] == _script().get_current_head()
    assert BASELINE_REVISION not in plan["pending"]

    engine = create_engine(db_url)
    try:
        assert "alembic_version" not in inspect(engine).get_table_names(), "只读计划不得给老库打版本戳"
    finally:
        engine.dispose()


def test_database_at_head_is_compatible_with_nothing_pending(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'head.db'}"
    _upgrade(db_url, "head")
    plan = plan_migrations(db_url)
    assert plan["status"] == "compatible"
    assert plan["current_heads"] == [_script().get_current_head()]
    assert plan["target_heads"] == [_script().get_current_head()]
    assert plan["pending"] == []
    assert plan["extra"] == []


def test_database_behind_head_lists_pending_in_topological_order(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'behind.db'}"
    _upgrade(db_url, BASELINE_REVISION)
    plan = plan_migrations(db_url)
    script = _script()
    assert plan["status"] == "compatible"
    assert plan["current_heads"] == [BASELINE_REVISION]
    assert plan["pending"], "基线之后应有待执行迁移"
    assert BASELINE_REVISION not in plan["pending"]
    assert script.get_revision(plan["pending"][0]).down_revision == BASELINE_REVISION
    assert plan["pending"][-1] == script.get_current_head()
    # 拓扑序:每个 revision 的 down_revision 要么已在前面出现,要么是基线本身
    seen = {BASELINE_REVISION}
    for revision in plan["pending"]:
        down = script.get_revision(revision).down_revision
        downs = down if isinstance(down, (tuple, list)) else (down,)
        assert all(d in seen for d in downs), f"{revision} 的 down_revision {downs} 未先于它出现"
        seen.add(revision)
    assert plan["pending_count"] == len(plan["pending"])


def test_unknown_revision_in_database_is_incompatible(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'unknown.db'}"
    _upgrade(db_url, "head")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = 'deadbeefcafe'"))
    finally:
        engine.dispose()
    plan = plan_migrations(db_url)
    assert plan["status"] == "incompatible"
    assert plan["current_heads"] == ["deadbeefcafe"]
    assert plan["extra"] == ["deadbeefcafe"]
    assert plan["status"] not in PLAN_DEPLOYABLE_STATUSES


def test_database_ahead_of_target_scripts_is_incompatible(tmp_path):
    """模拟 dispatch 一个旧 tag:目标代码的脚本目录缺最新 revision,而 DB 已经走过它。

    对目标图而言 DB 的 head 是未知 revision → `incompatible`(fail closed)。没有单独的
    downgrade 状态:目标图认识的 revision 必是某个 head 的祖先,不存在「已知但不在目标闭包」。
    """
    db_url = f"sqlite:///{tmp_path / 'ahead.db'}"
    _upgrade(db_url, "head")
    real_head = _script().get_current_head()

    older_scripts = _copy_alembic(tmp_path)
    head_file = Path(_script(older_scripts).get_revision(real_head).path)
    head_file.unlink()
    older_head = _script(older_scripts).get_current_head()
    assert older_head != real_head

    plan = plan_migrations(db_url, script_location=str(older_scripts))
    assert plan["status"] == "incompatible"
    assert plan["target_heads"] == [older_head]
    assert plan["current_heads"] == [real_head]
    assert plan["extra"] == [real_head]
    assert plan["status"] not in PLAN_DEPLOYABLE_STATUSES
    assert "领先" in plan["detail"]


def test_multi_head_target_treats_heads_as_a_set(tmp_path):
    """下游分叉仓形态:目标有两个 head,DB 只走完主线——合法落后,pending 为另一支线。"""
    db_url = f"sqlite:///{tmp_path / 'multihead.db'}"
    _upgrade(db_url, "head")
    real_head = _script().get_current_head()

    forked_scripts = _copy_alembic(tmp_path)
    (forked_scripts / "versions" / "feedfacefeed_fake_branch.py").write_text(
        '"""fake downstream branch (test only)"""\n'
        'revision = "feedfacefeed"\n'
        f'down_revision = "{BASELINE_REVISION}"\n'
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade():\n    pass\n\n\n"
        "def downgrade():\n    pass\n",
        encoding="utf-8",
    )
    plan = plan_migrations(db_url, script_location=str(forked_scripts))
    assert plan["status"] == "compatible"
    assert sorted(plan["target_heads"]) == sorted([real_head, "feedfacefeed"])
    assert plan["current_heads"] == [real_head]
    assert plan["pending"] == ["feedfacefeed"]


def test_deployable_statuses_constant_matches_semantics():
    assert PLAN_DEPLOYABLE_STATUSES == {"fresh", "legacy_adoption_required", "compatible"}
