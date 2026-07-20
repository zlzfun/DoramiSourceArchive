"""X API 管理配置、低成本连通性探针与本地配额账本。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api import deps
from fetchers.registry import fetcher_registry
from models.db import SourceStateRecord
from services import x_api_config as x_config_service
from services.x_api_quota import (
    POST_READ_MICROS,
    USER_READ_MICROS,
    XApiQuotaExceeded,
    XApiQuotaGuard,
    read_x_api_usage,
    resource_seen_today,
)


router = APIRouter(prefix="/api/x-api", tags=["x-api"])
_PROBE_USER_FIELDS = "id,name,username,profile_image_url"


def _token_preview(token: str) -> str:
    value = (token or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"••••{value[-4:]}"


def _config_response(session: Session) -> Dict[str, Any]:
    cfg = x_config_service.resolve_x_api_config(session)
    field_sources = x_config_service.config_field_sources(session)
    return {
        "configured": cfg.configured,
        "bearer_token_set": bool(cfg.bearer_token),
        "bearer_token_preview": _token_preview(cfg.bearer_token),
        "base_url": cfg.base_url,
        "timeout_seconds": cfg.timeout_seconds,
        "max_results": cfg.max_results,
        "monthly_budget_usd": cfg.monthly_budget_usd,
        "source": x_config_service.overall_config_source(field_sources),
        "field_sources": field_sources,
    }


class XApiConfigUpdate(BaseModel):
    bearer_token: Optional[str] = None
    base_url: Optional[str] = None
    timeout_seconds: Optional[int] = None
    max_results: Optional[int] = None
    monthly_budget_usd: Optional[float] = None


@router.get("/config")
def get_x_api_config(session: Session = Depends(deps.get_session)):
    """返回有效配置；Bearer Token 只返回是否已设置与尾部脱敏预览。"""
    return _config_response(session)


@router.post("/config")
def set_x_api_config(
    payload: XApiConfigUpdate,
    session: Session = Depends(deps.get_session),
):
    """写入 AppSettingRecord 运行时覆盖；空 token 表示不修改既有机密。"""
    updates: Dict[str, str] = {}
    if payload.bearer_token and payload.bearer_token.strip():
        updates[x_config_service.KEY_BEARER_TOKEN] = payload.bearer_token.strip()
    if payload.base_url is not None:
        base_url = payload.base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="base_url 必须是 http(s) URL")
        updates[x_config_service.KEY_BASE_URL] = base_url
    if payload.timeout_seconds is not None:
        if not 1 <= payload.timeout_seconds <= 300:
            raise HTTPException(status_code=400, detail="timeout_seconds 必须在 1..300 之间")
        updates[x_config_service.KEY_TIMEOUT_SECONDS] = str(payload.timeout_seconds)
    if payload.max_results is not None:
        if not 5 <= payload.max_results <= 100:
            raise HTTPException(status_code=400, detail="max_results 必须在 5..100 之间")
        updates[x_config_service.KEY_MAX_RESULTS] = str(payload.max_results)
    if payload.monthly_budget_usd is not None:
        if payload.monthly_budget_usd <= 0:
            raise HTTPException(status_code=400, detail="monthly_budget_usd 必须大于 0")
        updates[x_config_service.KEY_MONTHLY_BUDGET_USD] = str(
            payload.monthly_budget_usd
        )
    for key, value in updates.items():
        x_config_service.set_setting(session, key, value)
    return _config_response(session)


def _probe_resource(session: Session) -> Dict[str, Any]:
    """优先重复读取已归档 Post（$0.005），无 Post 才回退 User（$0.010）。"""
    caches = x_config_service.all_user_caches(session)
    x_source_ids = set(caches)
    for meta in fetcher_registry.get_all_metadata():
        if meta.get("platform") == "x" and not meta.get("is_template"):
            x_source_ids.add(str(meta["id"]))

    post_candidates = []
    if x_source_ids:
        states = session.exec(
            select(SourceStateRecord).where(SourceStateRecord.source_id.in_(x_source_ids))
        ).all()
        for state in states:
            post_id = str(state.last_cursor_value or "").strip()
            if not post_id.isdigit():
                continue
            seen_today = resource_seen_today(session, "posts", post_id)
            post_candidates.append((not seen_today, state.source_id, post_id))
    if post_candidates:
        # 当天已见者排在前面：测试仍访问 X，但日去重下通常不再产生费用。
        _, source_id, post_id = min(post_candidates)
        return {
            "source_id": source_id,
            "resource_type": "post",
            "resource_id": post_id,
            "seen_key": "posts",
            "path": f"tweets/{post_id}",
            "params": {"tweet.fields": "id"},
            "primary_resource": "post",
            "minimum_cost_micros": POST_READ_MICROS,
            "probe": "cached_post_lookup",
        }

    for source_id, cached in sorted(caches.items()):
        user_id = str(cached.get("user_id") or "").strip()
        if user_id.isdigit():
            return {
                "source_id": source_id,
                "resource_type": "user",
                "resource_id": user_id,
                "seen_key": "users",
                "path": f"users/{user_id}",
                "params": {"user.fields": _PROBE_USER_FIELDS},
                "primary_resource": "user",
                "minimum_cost_micros": USER_READ_MICROS,
                "probe": "stable_user_lookup",
            }
    # 首次尚无缓存时使用策展 preset 的稳定 ID，避免额外 username 解析。
    for meta in fetcher_registry.get_all_metadata():
        if meta.get("platform") != "x" or meta.get("is_template"):
            continue
        fetcher_class = fetcher_registry.get_class(meta["id"])
        user_id = str(getattr(fetcher_class, "user_id", "") or "").strip()
        if user_id.isdigit():
            return {
                "source_id": meta["id"],
                "resource_type": "user",
                "resource_id": user_id,
                "seen_key": "users",
                "path": f"users/{user_id}",
                "params": {"user.fields": _PROBE_USER_FIELDS},
                "primary_resource": "user",
                "minimum_cost_micros": USER_READ_MICROS,
                "probe": "stable_user_lookup",
            }
    raise HTTPException(status_code=400, detail="没有可用的 X User ID，请先配置或抓取一个 X 源")


@router.post("/config/test")
async def test_x_api_config(session: Session = Depends(deps.get_session)):
    """用已归档 Post（优先）或稳定 User ID 做最小连通性探针。

    资源当日已见时，X UTC 日去重通常使本次为 $0；否则成功返回
    一个 Post 约 $0.005，回退 User 约 $0.010。响应明确返回本地账本增量。
    """
    cfg = x_config_service.resolve_x_api_config(session)
    if not cfg.configured:
        raise HTTPException(status_code=400, detail="X API 未配置 Bearer Token")
    probe = _probe_resource(session)
    source_id = str(probe["source_id"])
    resource_id = str(probe["resource_id"])
    already_seen = resource_seen_today(session, probe["seen_key"], resource_id)
    guard = XApiQuotaGuard(
        deps.get_db_sink().engine,
        monthly_budget_usd=cfg.monthly_budget_usd,
    )
    if not already_seen:
        try:
            guard.ensure_available(
                minimum_cost_micros=int(probe["minimum_cost_micros"])
            )
        except XApiQuotaExceeded as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    before = guard.snapshot()
    try:
        async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
            response = await client.get(
                f"{cfg.base_url.rstrip('/')}/{probe['path']}",
                params=probe["params"],
                headers={
                    "Authorization": f"Bearer {cfg.bearer_token}",
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": f"X API 连接失败: {exc}", "estimated_cost_usd": 0.0},
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"X API 连通性测试失败（HTTP {response.status_code}）",
                "estimated_cost_usd": 0.0,
            },
        )
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail={"message": "X API 返回非 JSON 响应", "estimated_cost_usd": 0.0},
        )
    resource = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(resource, dict) or not resource.get("id"):
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"X API 未返回探针 {probe['resource_type']} 资源",
                "estimated_cost_usd": 0.0,
            },
        )
    after = guard.record_response(
        payload,
        primary_resource=probe["primary_resource"],
        source_id=source_id,
    )
    if probe["resource_type"] == "user":
        x_config_service.write_user_cache(
            session,
            source_id,
            handle=str(resource.get("username") or ""),
            user_id=str(resource["id"]),
            user=resource,
        )
    incremental_cost_usd = max(after.estimated_cost_usd - before.estimated_cost_usd, 0.0)
    return {
        "ok": True,
        "status": "success",
        "probe": probe["probe"],
        "source_id": source_id,
        "resource_type": probe["resource_type"],
        "resource_id": str(resource["id"]),
        "estimated_cost_usd": round(incremental_cost_usd, 6),
        "deduplicated_today": incremental_cost_usd == 0,
        "billing_note": (
            f"本地账本显示该 {probe['resource_type']} 当日已返回；X UTC 日去重为软保证。"
            if incremental_cost_usd == 0
            else f"本次首次返回 1 个 {probe['resource_type']} 资源，按本地账本计费。"
        ),
    }


@router.get("/quota")
def get_x_api_quota(session: Session = Depends(deps.get_session)):
    """返回当前 UTC 月本地计费估算，含按源归因。"""
    cfg = x_config_service.resolve_x_api_config(session)
    return read_x_api_usage(session, monthly_budget_usd=cfg.monthly_budget_usd).as_dict()
