"""跨 Router 复用的「内容源元数据」工具与常量。

阶段1 拆分时，把原本散落在 app.py、被 admin/content/reader 多个域共用的源目录元数据
助手集中到此。仅依赖注册中心、向量层的友好名映射与 ORM 模型，无 app 级可变全局
依赖（不引用 db_sink 等），因此可被任意 Router 安全 import，且不与 api.app 形成环。
"""

import json
from typing import Any, Dict, List, Optional

from fetchers.registry import fetcher_registry
from models.db import ReaderSubscriptionRecord
from services import daily_brief as daily_brief_service
from storage.impl.vector_storage import SOURCE_FRIENDLY_NAMES


# content_type -> 中文分类名（内容源目录/看板展示用）。
CONTENT_TYPE_CATEGORY = {
    "rss_article": "RSS 资讯",
    "web_article": "网页文章",
    "wechat_article": "微信公众号",
    "arxiv": "arXiv 论文",
    "github_release": "GitHub 发布",
    "github_repository": "代码仓库",
    "hf_model": "模型",
    "huggingface_model": "模型",
    "tech_conference": "技术会议",
    "social_post": "社交动态",
    "webhook_trigger": "工作流",
    "daily_brief": "AI 日报",
}

# 日报作为「特殊源」的展示元数据。日报不是抓取器（不进 FetcherRegistry），
# 在内容源目录里直接特判其名称/图标/简介，避免被采集触发流程误调。
DAILY_BRIEF_SOURCE_ID = daily_brief_service.DAILY_BRIEF_SOURCE_ID
DAILY_BRIEF_SOURCE_META = {
    "name": "哆啦美·AI资讯日报",
    "icon": "🤖",
    "desc": "由后端大模型每日自动生成的 AI 资讯日报，汇总择优近期归档内容。",
    "content_type": "daily_brief",
    "category": "AI 日报",
    # 归到哆啦美自有品牌身份，使前端徽标走品牌色「美」字而非通用齿轮兜底。
    "source_owner": "dorami",
}


def subscription_source_ids(subscription: ReaderSubscriptionRecord) -> List[str]:
    """Extract the source_id scope a subscription filters on (source_ids/source_id)."""
    raw = subscription.filters_json
    try:
        filters = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        filters = {}
    ids: List[str] = []
    for key in ("source_ids", "source_id"):
        value = filters.get(key)
        if value:
            ids.extend(part.strip() for part in str(value).split(",") if part.strip())
    return ids


def _source_category(content_type: Optional[str]) -> str:
    if not content_type:
        return "其它"
    return CONTENT_TYPE_CATEGORY.get(content_type, content_type)


# ==================== 内容形态（阅读器分流轴，迭代 2）====================
# 形态是**源级标记**（fetcher.content_shape），registry 是第一事实源;
# 对注册表之外的历史归档源（已下线节点、导入源），按 content_type 兜底:
# 这三类结构化监控产物必然是动态形。
BULLETIN_CONTENT_TYPES = frozenset({
    "github_release", "github_repository", "hf_model", "huggingface_model",
})


def source_shape(
    source_id: Optional[str],
    content_type: Optional[str],
    registry_meta: Dict[str, Dict[str, Any]],
) -> str:
    """解析某源的内容形态："article"（文章）| "bulletin"（动态）。"""
    meta = registry_meta.get(source_id or "")
    if meta is not None:
        return meta.get("shape") or "article"
    if (content_type or "") in BULLETIN_CONTENT_TYPES:
        return "bulletin"
    return "article"


def bulletin_registry_source_ids() -> List[str]:
    """注册表中动态形源的 source_id 集合（articles 的 shape= 过滤用）。"""
    return sorted(
        sid for sid, meta in _registry_source_meta().items()
        if (meta.get("shape") or "article") == "bulletin"
    )


def _registry_source_meta() -> Dict[str, Dict[str, Any]]:
    """source_id -> 抓取器注册元数据（名称/简介/图标），用于内容源目录展示。"""
    return {meta["id"]: meta for meta in fetcher_registry.get_all_metadata()}


def _friendly_source_name(source_id: str, registry_meta: Dict[str, Dict[str, Any]]) -> str:
    meta = registry_meta.get(source_id)
    if meta and meta.get("name"):
        return meta["name"]
    return SOURCE_FRIENDLY_NAMES.get(source_id, source_id)
