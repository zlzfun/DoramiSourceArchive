"""部署前只读自检(issue #102 自动部署流水线,PR-1)。

两个入口都由 ``docker/entrypoint.py`` 的命令行模式调用,在**目标镜像**里、切换容器之前执行:

- ``--check-config``   配置与当前状态预检:settings 可加载 → 安全检查(按 posture 分级)→ taxonomy 姿态与
                       目录自校验 → 只读迁移计划 → (DB 已在目标 revision 集合时)只读 taxonomy 状态校验。
                       覆盖「新增 ini 节 / 生产安全配置 / taxonomy 冲突」类失败,避免新容器起不来的停机窗;
                       **不承诺**覆盖迁移之后才暴露的 reconcile 失败(那需要快照演练,见方案 §4.6)。
- ``--plan-migrations`` 只读迁移计划(``storage.migrations.plan_migrations``),给部署脚本判「领先 / 落后 /
                       全新 / 待收养 / 不兼容」。

纪律:
- 只 import 无副作用模块——**绝不 import ``api.app``**(装配阶段会建 storage、种账号);``config`` 也延迟到
  函数内 import,配置无效时报成 JSON 错误而不是 traceback。
- 数据库只以 query_only 连接打开;不跑 ``ensure_migrated``,不写。正式 entrypoint 的 migration + reconcile
  仍是最终权威,本自检通过不代表可以跳过它们。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

CHECK_CONFIG = "--check-config"
PLAN_MIGRATIONS = "--plan-migrations"
CLI_MODES = (CHECK_CONFIG, PLAN_MIGRATIONS)


def _load_settings():
    """按当前 ``DORAMI_CONFIG_FILE`` 重新加载配置(不用模块级单例,它可能是旧环境下装的)。"""
    import config as config_module

    return config_module.load_config()


def _redact_url(db_url: str) -> str:
    try:
        from sqlalchemy.engine import make_url

        return make_url(db_url).render_as_string(hide_password=True)
    except Exception:  # pragma: no cover - 非法 URL 也别把原文吐出去
        return "<unparseable>"


def check_config(
    *,
    config_path: Optional[str] = None,
    database_url: Optional[str] = None,
) -> dict[str, Any]:
    """跑完整套只读自检,返回可 JSON 化的报告:``status`` 为 ``ok`` / ``error``。"""
    report: dict[str, Any] = {"status": "ok", "errors": [], "warnings": [], "checks": {}}

    def error(message: str) -> None:
        report["errors"].append(message)

    def warn(message: str) -> None:
        report["warnings"].append(message)

    def finish() -> dict[str, Any]:
        report["status"] = "error" if report["errors"] else "ok"
        return report

    if config_path is not None:
        os.environ["DORAMI_CONFIG_FILE"] = str(config_path)
    declared = os.environ.get("DORAMI_CONFIG_FILE", "").strip()
    report["checks"]["config_file"] = declared or "(默认 config/backend.ini)"
    if declared and not Path(declared).is_file():
        # load_config 对缺失文件会静默回落默认值:dev posture、无 secret——自检会误报 ok,故显式拦下。
        error(f"config: DORAMI_CONFIG_FILE 指向的文件不存在: {declared}")
        return finish()

    try:
        cfg = _load_settings()
    except Exception as exc:  # 非法姿态值等在 load_config 内抛 ValueError
        error(f"config: {exc}")
        return finish()

    # ── 安全配置(与 lifespan 里的 enforce_security_config 同一纯函数,按 posture 分级)──
    from api.security_checks import evaluate_security_config

    sec_errors, sec_warnings = evaluate_security_config(cfg)
    report["checks"]["security"] = {
        "posture": "production" if cfg.auth.cookie_secure else "dev",
        "errors": list(sec_errors),
        "warnings": list(sec_warnings),
    }
    for message in sec_errors:
        error(f"security: {message}")
    for message in sec_warnings:
        warn(f"security: {message}")

    # ── Taxonomy 姿态与目录自校验 ──
    from services.taxonomy_deployment import (
        DEPLOYMENT_ACTOR,
        DEPLOYMENT_MODES,
        TaxonomyDeploymentError,
        load_catalog,
        validate_catalog_session,
    )

    mode = str(cfg.taxonomy.mode or "").strip().lower()
    taxonomy_check: dict[str, Any] = {"mode": mode, "catalog": None, "database_state": None}
    report["checks"]["taxonomy"] = taxonomy_check
    catalog = None
    if mode not in DEPLOYMENT_MODES:
        error(f"taxonomy: 非法部署姿态 {mode!r},应为 authority / replica / manual")
    elif mode == "authority":
        catalog_path = Path(cfg.taxonomy.catalog_path)
        try:
            catalog = load_catalog(catalog_path)
            taxonomy_check["catalog"] = {
                "path": str(catalog_path),
                "entries": len(catalog["entries"]),
                "manifest_sha256": catalog["manifest_sha256"],
            }
        except TaxonomyDeploymentError as exc:
            error(f"taxonomy: {exc}")

    # ── 只读迁移计划 ──
    from storage.migrations import plan_migrations, readonly_engine

    db_url = database_url or cfg.storage.database_url
    report["checks"]["database"] = {"url": _redact_url(db_url)}
    plan: Optional[dict[str, Any]] = None
    try:
        plan = plan_migrations(db_url)
    except Exception as exc:
        error(f"migrations: 迁移计划失败: {exc}")
    report["checks"]["migrations"] = plan

    # ── Taxonomy 数据库状态(只读):只在 DB 已在目标 revision 集合时才有意义 ──
    if mode == "authority" and catalog is not None and plan is not None:
        if plan["status"] == "compatible" and not plan["pending"]:
            from sqlmodel import Session

            engine = readonly_engine(db_url)
            try:
                with Session(engine) as session:
                    outcome = validate_catalog_session(session, catalog, actor_id=DEPLOYMENT_ACTOR)
                taxonomy_check["database_state"] = {
                    "status": outcome["status"],
                    "missing": int(outcome.get("missing", 0)),
                }
            except TaxonomyDeploymentError as exc:
                taxonomy_check["database_state"] = {"status": "conflict"}
                error(f"taxonomy: {exc}")
            finally:
                engine.dispose()
        else:
            taxonomy_check["database_state"] = {
                "status": "skipped",
                "reason": (
                    f"数据库不在目标 revision 集合(迁移计划 {plan['status']},待执行 {plan['pending_count']}),"
                    "迁移后由 entrypoint 的 reconcile 判定"
                ),
            }

    return finish()


def run_cli(argv: Sequence[str]) -> int:
    """命令行入口(由 docker/entrypoint.py 转发):打印 JSON,返回退出码。

    退出码:0 = 可继续部署;1 = 自检失败 / 迁移计划不可部署;2 = 用法或环境错误。
    """
    mode = argv[0] if argv else ""
    if mode == CHECK_CONFIG:
        report = check_config()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ok" else 1
    if mode == PLAN_MIGRATIONS:
        try:
            cfg = _load_settings()
        except Exception as exc:
            print(json.dumps({"status": "error", "detail": f"config: {exc}"}, ensure_ascii=False))
            return 2
        from storage.migrations import PLAN_DEPLOYABLE_STATUSES, plan_migrations

        try:
            plan = plan_migrations(cfg.storage.database_url)
        except Exception as exc:
            print(json.dumps({"status": "error", "detail": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0 if plan["status"] in PLAN_DEPLOYABLE_STATUSES else 1
    print(f"unknown mode: {mode!r}; expected one of {', '.join(CLI_MODES)}", file=sys.stderr)
    return 2


__all__ = ["CHECK_CONFIG", "CLI_MODES", "PLAN_MIGRATIONS", "check_config", "run_cli"]
