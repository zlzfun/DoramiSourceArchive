"""数据接入 Router（collector/admin）：AI 节点构建 + 社交动态导入。

阶段1 从 app.py 迁出的「把内容/源接进来」的 collector 端点：
- /api/source-builder/analyze|preview —— 由列表页 URL 生成配置节点建议 + 试抓预览；
- /api/import/social-posts —— 外部社交动态批量入库（幂等）。

说明：路径不变；collector 网关仍由中间件统一强制（COLLECTOR_API_PREFIXES 含
/api/source-builder、/api/import/social-posts）。数据访问经 Depends(deps.get_session)/
deps.get_db_sink()；current_username 经 _app() 延迟动态调用（避免导入环）。
本域专用的请求模型与社交内容构建助手随迁入本文件。
"""

import datetime
import importlib
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from api import deps
from fetchers.registry import fetcher_registry
from models.content import SocialPostContent
from models.db import SourceConfigRecord

router = APIRouter(tags=["ingest"])


def _app():
    """延迟取 api.app（避免导入环；动态调用其留守的 current_username）。"""
    return importlib.import_module("api.app")


# ==================== AI 节点构建（source-builder）====================

class SourceBuilderAnalyzeRequest(BaseModel):
    url: str


class SourceBuilderPreviewRequest(BaseModel):
    source_id: str = ""
    name: str = ""
    source_type: str = "web"
    url: str = ""
    category: str = ""
    params: Dict[str, Any] = PydanticField(default_factory=dict)


@router.post("/api/source-builder/analyze")
async def source_builder_analyze(
    req: SourceBuilderAnalyzeRequest, request: Request, session: Session = Depends(deps.get_session)
):
    """输入一个列表页 URL → 判类型 + 分析 + (LLM) 生成抓取节点配置建议。"""
    from services import source_builder

    existing = {row.source_id for row in session.exec(select(SourceConfigRecord.source_id)).all()}
    existing |= set(fetcher_registry._fetchers.keys())
    result = await source_builder.analyze_url(
        req.url, session=session, existing_ids=existing,
        usage_username=_app().current_username(request) or None,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "分析失败"))
    return result


@router.post("/api/source-builder/preview")
async def source_builder_preview(req: SourceBuilderPreviewRequest):
    """用建议配置试抓样例条目做验证（不落库）。"""
    from services import source_builder

    result = await source_builder.preview_config(req.model_dump())
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "试抓失败"))
    return result


# ==================== 社交动态导入 ====================

class SocialPostImportItem(BaseModel):
    platform: str = "x"
    post_id: str
    source_id: str = ""
    author_id: str = ""
    author_handle: str = ""
    author_name: str = ""
    text: str = ""
    title: str = ""
    source_url: str = ""
    publish_date: str = ""
    conversation_id: str = ""
    in_reply_to_id: str = ""
    quoted_post_id: str = ""
    reposted_post_id: str = ""
    lang: str = ""
    tags: List[str] = PydanticField(default_factory=list)
    media_urls: List[str] = PydanticField(default_factory=list)
    metrics: Dict[str, Any] = PydanticField(default_factory=dict)
    raw_data: Dict[str, Any] = PydanticField(default_factory=dict)


class SocialPostImportParams(BaseModel):
    source_id: str = "import_social_posts"
    posts: List[SocialPostImportItem]


def normalize_social_source_id(platform: str, author_handle: str, fallback: str) -> str:
    if fallback:
        return fallback.strip()
    safe_platform = (platform or "social").strip().lower().replace("/", "_")
    safe_handle = (author_handle or "unknown").strip().lower().lstrip("@").replace("/", "_")
    return f"{safe_platform}_{safe_handle}"


def build_social_post_content(post: SocialPostImportItem, batch_source_id: str) -> SocialPostContent:
    platform = post.platform.strip() or "x"
    post_id = post.post_id.strip()
    if not post_id:
        raise ValueError("post_id 不能为空")

    source_id = normalize_social_source_id(platform, post.author_handle, post.source_id or batch_source_id)
    title = post.title.strip() or (post.text.strip()[:80] if post.text else f"{platform} post {post_id}")
    source_url = post.source_url.strip()
    if not source_url and post.author_handle:
        source_url = f"https://x.com/{post.author_handle.lstrip('@')}/status/{post_id}"

    publish_date = post.publish_date.strip() or datetime.datetime.now().isoformat()
    raw_data = dict(post.raw_data or {})
    raw_data.setdefault("import_source_id", batch_source_id)

    return SocialPostContent(
        id=f"{source_id}_{post_id}",
        title=title,
        source_url=source_url,
        publish_date=publish_date,
        source_id=source_id,
        content=post.text,
        has_content=bool(post.text),
        platform=platform,
        author_id=post.author_id,
        author_handle=post.author_handle,
        author_name=post.author_name,
        post_id=post_id,
        conversation_id=post.conversation_id,
        in_reply_to_id=post.in_reply_to_id,
        quoted_post_id=post.quoted_post_id,
        reposted_post_id=post.reposted_post_id,
        lang=post.lang,
        tags=post.tags,
        media_urls=post.media_urls,
        metrics=post.metrics,
        raw_data=raw_data,
    )


@router.post("/api/import/social-posts")
async def import_social_posts(params: SocialPostImportParams):
    """外部社交动态批量入库（按 source_id + post_id 幂等）。"""
    db_sink = deps.get_db_sink()
    saved_count = 0
    skipped_count = 0
    errors = []

    for index, post in enumerate(params.posts):
        try:
            content = build_social_post_content(post, params.source_id)
            if await db_sink.save(content):
                saved_count += 1
            else:
                skipped_count += 1
        except Exception as e:  # noqa: BLE001 - 单条失败不阻断整批，收集进 errors
            errors.append({
                "index": index,
                "post_id": post.post_id,
                "error": str(e),
            })

    return {
        "status": "partial_success" if errors else "success",
        "received_count": len(params.posts),
        "saved_count": saved_count,
        "skipped_count": skipped_count,
        "error_count": len(errors),
        "errors": errors,
    }
