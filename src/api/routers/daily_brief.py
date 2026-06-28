"""每日 AI 资讯日报 + 全局大模型配置 Router（collector/admin）。

阶段1 从 app.py 迁出。说明：
- 路径保持不变（/api/llm/* 与 /api/daily-brief/* 两个前缀，故本 router 不设 prefix，
  在装饰器里写全路径）；
- collector 网关仍由 app.py 中间件统一强制（COLLECTOR_API_PREFIXES 含 /api/llm、
  /api/daily-brief）；
- 数据访问经 Depends(deps.get_session)；少数仍留在 app.py 的编排 helper
  （scheduler 重载 / runtime 判定 / 当前用户名 / 抓取后自动向量化）经 _app() 延迟
  动态调用——既避免与 api.app 的导入环，也兼容测试对 auto_vectorize_after_fetch 等的
  monkeypatch。
"""

import importlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from api import deps
from api.sources import DAILY_BRIEF_SOURCE_ID
from llm.client import LLMError, LLMNotConfigured
from llm.client import ping as llm_ping
from models.db import ArticleRecord
from services import daily_brief as daily_brief_service

router = APIRouter(tags=["daily-brief"])


def _app():
    """延迟取 api.app（避免导入环 + 兼容测试 monkeypatch 的编排 helper）。"""
    return importlib.import_module("api.app")


# ==================== 全局大模型配置 ====================

def _llm_api_key_preview(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def _llm_config_response(session: Session) -> Dict[str, Any]:
    cfg = daily_brief_service.resolve_llm_config(session)
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "configured": cfg.configured,
        "api_key_set": bool(cfg.api_key),
        "api_key_preview": _llm_api_key_preview(cfg.api_key),
    }


class LLMConfigUpdate(BaseModel):
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


@router.get("/api/llm/config")
def get_llm_config(session: Session = Depends(deps.get_session)):
    """读取大模型有效配置（脱敏，绝不返回明文 api_key）。"""
    return _llm_config_response(session)


@router.post("/api/llm/config")
def set_llm_config(payload: LLMConfigUpdate, session: Session = Depends(deps.get_session)):
    """更新大模型运行期配置（写入 app_settings 覆盖 ini 默认）。

    api_key 留空（None 或空串）表示不修改；base_url/model 等同理按需覆盖。
    """
    if payload.base_url is not None:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_BASE_URL, payload.base_url.strip())
    if payload.model is not None:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_MODEL, payload.model.strip())
    if payload.api_key:  # 仅在非空时更新，避免清空已有机密
        daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_API_KEY, payload.api_key.strip())
    if payload.temperature is not None:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_TEMPERATURE, str(payload.temperature))
    if payload.max_tokens is not None:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_MAX_TOKENS, str(payload.max_tokens))
    return _llm_config_response(session)


@router.post("/api/llm/config/test")
async def test_llm_config(session: Session = Depends(deps.get_session)):
    """用当前有效配置测试连接。"""
    cfg = daily_brief_service.resolve_llm_config(session)
    if not cfg.configured:
        raise HTTPException(status_code=400, detail="LLM 未配置（需 base_url / api_key / model）")
    try:
        return await llm_ping(cfg)
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"连接失败: {exc}")


# ==================== 每日 AI 资讯日报 ====================

class DailyBriefConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    cron: Optional[str] = None
    cursor: Optional[str] = None  # 手动设置/重置增量游标；空串=重置（下次用近 1 天兜底窗口）
    top_n: Optional[int] = None   # 日报精选条数


class DailyBriefGenerateParams(BaseModel):
    report_date: Optional[str] = None
    dry_run: bool = False
    top_n: Optional[int] = None   # 本次生成的精选条数（不传则用配置值）


def _daily_brief_config_response(session: Session) -> Dict[str, Any]:
    return {
        "enabled": daily_brief_service.daily_brief_enabled(session),
        "cron": daily_brief_service.daily_brief_cron(session),
        "cursor": daily_brief_service.read_cursor(session),
        "top_n": daily_brief_service.daily_brief_top_n(session),
        "last_run": daily_brief_service.get_json_setting(session, daily_brief_service.KEY_LAST_RUN, None),
    }


@router.get("/api/daily-brief/config")
def get_daily_brief_config(session: Session = Depends(deps.get_session)):
    return _daily_brief_config_response(session)


@router.post("/api/daily-brief/config")
def set_daily_brief_config(payload: DailyBriefConfigUpdate, session: Session = Depends(deps.get_session)):
    if payload.cron is not None:
        cron_expr = payload.cron.strip()
        if len(cron_expr.split()) != 5:
            raise HTTPException(status_code=400, detail="cron 表达式必须是 5 段，例如：30 8 * * *")
    if payload.top_n is not None and not (
        daily_brief_service.TOP_N_MIN <= payload.top_n <= daily_brief_service.TOP_N_MAX
    ):
        raise HTTPException(
            status_code=400,
            detail=f"精选条数需在 {daily_brief_service.TOP_N_MIN}–{daily_brief_service.TOP_N_MAX} 之间",
        )
    if payload.enabled is not None:
        daily_brief_service.set_setting(
            session, daily_brief_service.KEY_ENABLED, "true" if payload.enabled else "false"
        )
    if payload.cron is not None:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_CRON, payload.cron.strip())
    if payload.cursor is not None:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_CURSOR, payload.cursor.strip())
    if payload.top_n is not None:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_TOP_N, str(payload.top_n))
    # 仅在 collector 运行角色下有调度引擎；reader 角色不接日报 cron。
    app = _app()
    if app.runtime_collector_enabled():
        app.reload_daily_brief_schedule()
    return _daily_brief_config_response(session)


@router.post("/api/daily-brief/generate")
async def generate_daily_brief_endpoint(
    request: Request, payload: Optional[DailyBriefGenerateParams] = None
):
    """手动触发日报生成（同步等待，耗时数十秒到数分钟）。"""
    app = _app()
    params = payload or DailyBriefGenerateParams()
    if params.top_n is not None and not (
        daily_brief_service.TOP_N_MIN <= params.top_n <= daily_brief_service.TOP_N_MAX
    ):
        raise HTTPException(
            status_code=400,
            detail=f"精选条数需在 {daily_brief_service.TOP_N_MIN}–{daily_brief_service.TOP_N_MAX} 之间",
        )
    try:
        result = await daily_brief_service.generate_daily_brief(
            storage=deps.get_db_sink(),
            report_date=params.report_date,
            trigger="manual",
            triggered_by=app.current_username(request) or None,
            dry_run=params.dry_run,
            top_n=params.top_n,
        )
    except LLMNotConfigured as exc:
        daily_brief_service.set_progress("error", str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        daily_brief_service.set_progress("error", f"生成失败: {exc}")
        raise HTTPException(status_code=502, detail=f"日报生成失败: {exc}")
    except Exception as exc:  # noqa: BLE001 兜底：让进度反映失败，再抛出
        daily_brief_service.set_progress("error", f"生成失败: {exc}")
        raise
    if not params.dry_run and result.get("article_id"):
        await app.auto_vectorize_after_fetch([result["article_id"]])
    return result


@router.get("/api/daily-brief/runs")
def get_daily_brief_runs(session: Session = Depends(deps.get_session)):
    last_run = daily_brief_service.get_json_setting(session, daily_brief_service.KEY_LAST_RUN, None)
    rows = session.exec(
        select(ArticleRecord)
        .where(ArticleRecord.source_id == DAILY_BRIEF_SOURCE_ID)
        .order_by(ArticleRecord.publish_date.desc())
        .limit(30)
    ).all()
    history = [
        {
            "id": row.id,
            "report_date": row.publish_date,
            "title": row.title,
            "fetched_date": row.fetched_date,
        }
        for row in rows
    ]
    return {"last_run": last_run, "history": history}


@router.get("/api/daily-brief/progress")
def get_daily_brief_progress():
    """当前日报生成的实时阶段进度（内存态，供前端轮询）。"""
    return daily_brief_service.get_progress()


@router.get("/api/daily-brief/pipeline")
def get_daily_brief_pipeline(session: Session = Depends(deps.get_session)):
    """日报生成管线的真实提示词与关键参数，供前端流程图展示（与代码同步，不在前端硬抄）。"""
    prompts = daily_brief_service.prompts
    cfg = daily_brief_service.resolve_llm_config(session)
    top_n = daily_brief_service.daily_brief_top_n(session)
    return {
        "model": cfg.model,
        "configured": cfg.configured,
        "params": {
            "top_n": top_n,
            "max_total": 120,          # collect_candidates 默认总量上限
            "per_source_cap": 15,      # collect_candidates 每来源候选上限
            "map_concurrency": cfg.map_concurrency,
            "map_max_body_chars": 6000,  # MAP 单篇正文截断
            "recent_brief_days": 3,    # REDUCE 注入的近期日报天数
        },
        "allowed_classifications": prompts.ALLOWED_CLASSIFICATIONS,
        "map_system_prompt": prompts.MAP_SYSTEM_PROMPT,
        "reduce_system_prompt": prompts.REDUCE_SYSTEM_PROMPT,
    }
