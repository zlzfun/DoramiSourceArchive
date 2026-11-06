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
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from api import deps
from fetchers.registry import fetcher_registry
from models.content import SocialPostContent
from models.db import SourceConfigRecord
from services import article_analysis as article_analysis_service
from services import ingest_policy, source_identity, sync_consumer_policy
from services.media_store import SSRFError
from storage.impl import db_storage

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
    params: Dict[str, Any] = PydanticField(default_factory=dict)


@router.post("/api/source-builder/analyze")
async def source_builder_analyze(
    req: SourceBuilderAnalyzeRequest, request: Request, session: Session = Depends(deps.get_session)
):
    """输入一个列表页 URL → 判类型 + 分析 + (LLM) 生成抓取节点配置建议。"""
    from services import source_builder

    existing = {row.source_id for row in session.exec(select(SourceConfigRecord.source_id)).all()}
    existing |= set(fetcher_registry._fetchers.keys())
    try:
        result = await source_builder.analyze_url(
            req.url, session=session, existing_ids=existing,
            usage_username=_app().current_username(request) or None,
        )
    except SSRFError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "分析失败"))
    return result


@router.post("/api/source-builder/preview")
async def source_builder_preview(req: SourceBuilderPreviewRequest):
    """用建议配置试抓样例条目做验证（不落库）。"""
    from services import source_builder

    try:
        result = await source_builder.preview_config(req.model_dump())
    except SSRFError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "试抓失败"))
    return result


# ==================== 社交动态导入 ====================

class SocialPostReference(BaseModel):
    """跨平台引用/转载帖摘要，与 SocialPostContent extensions 契约一致。"""
    author_name: str = ""
    author_handle: str = ""
    author_avatar_url: str = ""
    author_avatar_url_large: str = ""
    text: str = ""
    url: str = ""
    media_urls: List[str] = PydanticField(default_factory=list)


class SocialPostImportItem(BaseModel):
    platform: str = "x"
    post_id: str
    source_id: str = ""
    author_id: str = ""
    author_handle: str = ""
    author_name: str = ""
    author_avatar_url: str = ""
    author_avatar_url_large: str = ""
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
    quoted: Optional[SocialPostReference] = None
    reposted: Optional[SocialPostReference] = None
    raw_data: Dict[str, Any] = PydanticField(default_factory=dict)


class SocialPostImportParams(BaseModel):
    # 批级 source_id 只兜底「帖子没有作者信息」的情况;一账号一 source_id
    # (issue #262 B1:旧默认值 import_social_posts 把所有账号塞进一个源)。
    source_id: str = ""
    posts: List[SocialPostImportItem]


def resolve_social_post_source_id(
    session: Session,
    post: SocialPostImportItem,
    batch_source_id: str,
    *,
    index: Optional[dict] = None,
) -> str:
    """帖子级 source_id > 作者身份(认回已有源,否则派生) > 批级 source_id。"""
    explicit = (post.source_id or "").strip()
    if explicit:
        return explicit
    platform = post.platform.strip() or "x"
    if source_identity.normalize_handle(post.author_handle):
        return source_identity.resolve_social_source(
            session,
            platform=platform,
            handle=post.author_handle,
            user_id=post.author_id,
            index=index,
        ).source_id
    if (post.author_id or "").strip():
        matched = source_identity.match_social_source(
            session, platform=platform, user_id=post.author_id, index=index,
        )
        if matched:
            return matched
    fallback = (batch_source_id or "").strip()
    if fallback:
        return fallback
    raise ValueError("缺少作者 handle,无法确定来源")


def social_source_hint(post: SocialPostImportItem) -> dict:
    """未登记账号建临时源时的出处描述(名称 / 头像 / 主页 / 身份键,issue #262)。"""
    platform = (post.platform or "x").strip().lower() or "x"
    handle = source_identity.normalize_handle(post.author_handle)
    homepage = f"https://x.com/{post.author_handle.strip().lstrip('@')}" if platform == "x" and handle else ""
    display = (post.author_name or "").strip() or (post.author_handle or "").strip().lstrip("@")
    return {
        "origin": "agent",
        "admission_status": "provisional",
        "name": f"X · {display}" if platform == "x" and display else display,
        "platform": platform,
        "avatar_url": (post.author_avatar_url_large or post.author_avatar_url or "").strip(),
        "homepage_url": homepage,
        "identity_keys": source_identity.social_identity_keys(
            platform, handle=post.author_handle, user_id=post.author_id
        ),
    }


def build_social_post_content(
    post: SocialPostImportItem, batch_source_id: str, source_id: str
) -> SocialPostContent:
    platform = post.platform.strip() or "x"
    post_id = post.post_id.strip()
    if not post_id:
        raise ValueError("post_id 不能为空")
    title = post.title.strip() or (post.text.strip()[:80] if post.text else f"{platform} post {post_id}")
    source_url = post.source_url.strip()
    if not source_url and post.author_handle:
        source_url = f"https://x.com/{post.author_handle.lstrip('@')}/status/{post_id}"

    publish_date = post.publish_date.strip() or datetime.datetime.now().isoformat()
    raw_data = dict(post.raw_data or {})
    if batch_source_id:
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
        author_avatar_url=post.author_avatar_url,
        author_avatar_url_large=post.author_avatar_url_large,
        post_id=post_id,
        conversation_id=post.conversation_id,
        in_reply_to_id=post.in_reply_to_id,
        quoted_post_id=post.quoted_post_id,
        reposted_post_id=post.reposted_post_id,
        lang=post.lang,
        tags=post.tags,
        media_urls=post.media_urls,
        metrics=post.metrics,
        quoted=post.quoted.model_dump() if post.quoted else None,
        reposted=post.reposted.model_dump() if post.reposted else None,
        raw_data=raw_data,
    )


@router.post("/api/import/social-posts")
async def import_social_posts(params: SocialPostImportParams):
    """外部社交动态批量入库（按 source_id + post_id 幂等）。"""
    db_sink = deps.get_db_sink()
    saved_count = 0
    skipped_count = 0
    errors = []
    saved_ids: list[str] = []
    sources_by_index: dict[int, str] = {}

    with Session(db_sink.engine) as session:
        if sync_consumer_policy.v2_consumer_mode_active(session):
            # 接收端的公共内容只经 Archive Sync 到达(issue #262 B5:旧实现静默计入 skipped)。
            raise HTTPException(
                status_code=409,
                detail="v2 接收端的公共内容由远端权威节点管理，本机不能导入",
            )
        identity_index = source_identity.x_identity_index(session)
        for index, post in enumerate(params.posts):
            try:
                source_id = resolve_social_post_source_id(
                    session, post, params.source_id, index=identity_index
                )
                reserved = ingest_policy.reserved_source_id_reason(source_id)
                if reserved:
                    raise ValueError(reserved)
                sources_by_index[index] = source_id
            except ValueError as exc:
                errors.append({"index": index, "post_id": post.post_id, "error": str(exc)})

    for index, post in enumerate(params.posts):
        source_id = sources_by_index.get(index)
        if source_id is None:
            continue
        try:
            content = build_social_post_content(post, params.source_id, source_id)
            content._source_hint = social_source_hint(post)
            content._ingest_channel = "import"
            outcome = await db_sink.save_with_outcome(content)
            if outcome in db_storage.SAVE_STORED_OUTCOMES:
                saved_count += 1
                saved_ids.append(content.id)
            elif outcome in {db_storage.SAVE_DUPLICATE, db_storage.SAVE_REFRESHED}:
                skipped_count += 1
            else:
                _status, detail = ingest_policy.outcome_error(outcome) or (400, outcome)
                errors.append({"index": index, "post_id": post.post_id, "error": detail})
        except Exception as e:  # noqa: BLE001 - 单条失败不阻断整批，收集进 errors
            errors.append({
                "index": index,
                "post_id": post.post_id,
                "error": str(e),
            })

    if saved_ids:
        try:
            with Session(db_sink.engine) as session:
                if article_analysis_service.read_feature_flag(
                    session,
                    article_analysis_service.ARTICLE_ANALYSIS_ENABLED_KEY,
                    default=False,
                ):
                    for article_id in saved_ids:
                        article_analysis_service.queue_article_analysis(session, article_id)
                    session.commit()
        except Exception as exc:  # noqa: BLE001 - imported posts are already committed
            logging.getLogger("dorami.article_analysis").warning(
                "社交导入后的分析入队失败，将由补偿扫描恢复: %s",
                article_analysis_service.sanitize_error(exc),
            )

    return {
        "status": "partial_success" if errors else "success",
        "received_count": len(params.posts),
        "saved_count": saved_count,
        "skipped_count": skipped_count,
        "error_count": len(errors),
        "errors": errors,
    }
