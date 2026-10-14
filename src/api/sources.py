"""跨 Router 复用的「内容源元数据」工具与常量。

阶段1 拆分时，把原本散落在 app.py、被 admin/content/reader 多个域共用的源目录元数据
助手集中到此。仅依赖注册中心、向量层的友好名映射与 ORM 模型，无 app 级可变全局
依赖（不引用 db_sink 等），因此可被任意 Router 安全 import，且不与 api.app 形成环。
"""

import json
from typing import Any, Dict, List, Optional

from fetchers.registry import fetcher_registry
from models.db import ReaderSubscriptionRecord
from services import content_classification
from services import daily_brief as daily_brief_service
from services.source_naming import friendly_source_name



# 日报作为「特殊源」的展示元数据。日报不是抓取器（不进 FetcherRegistry），
# 在内容源目录里直接特判其名称/图标/简介，避免被采集触发流程误调。
DAILY_BRIEF_SOURCE_ID = daily_brief_service.DAILY_BRIEF_SOURCE_ID
DAILY_BRIEF_SOURCE_META = {
    "name": "哆啦美·AI资讯日报",
    "icon": "🤖",
    "desc": "由后端大模型每日自动生成的 AI 资讯日报，汇总择优近期归档内容。",
    "content_type": "daily_brief",
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
    """读者源目录 / 管理端内容看板的 ``category``:来源的数据结构中文名。

    issue #178 起与 ``content_classification.CONTENT_TYPE_LABELS`` 同一张词表(此前另有一张
    映射表,把 RSS / 网页条目叫成「RSS 资讯」「网页文章」)。
    """
    if not content_type:
        return "其它"
    return content_classification.content_type_label(content_type)


# ==================== 内容形态（阅读器容器，迭代 3 / issue #178）====================
# 形态是**源级标记**（fetcher.content_shape），registry 是第一事实源;
# 对注册表之外的历史归档源（已下线节点、导入源），按 content_type 兜底:
# 社交帖属于 social，播客单集属于 podcast，其余都是 article。
# issue #178 撤销 bulletin「动态」容器并入 article:版本更新 / 项目改由条目的内容类型
# (services/content_classification.py)表达,列表里用紧凑行。旧客户端仍可能传 shape=bulletin,
# 入参经 normalize_shape 视同 article——绝不能当作未知值忽略,否则 mark-all-read?shape=bulletin
# 会把全部订阅标读、列表会混进社交与播客。
VALID_CONTENT_SHAPES = frozenset({"article", "social", "podcast"})
LEGACY_SHAPE_ALIASES = {"bulletin": "article"}
SOCIAL_CONTENT_TYPES = frozenset({"social_post"})
PODCAST_CONTENT_TYPES = frozenset({"podcast_episode"})
CONTENT_TYPES_BY_SHAPE = {
    "social": SOCIAL_CONTENT_TYPES,
    "podcast": PODCAST_CONTENT_TYPES,
}


def normalize_shape(shape: Optional[str]) -> str:
    """入参形态归一:合法值原样返回,旧别名(bulletin)映射到 article,其余返回空串(= 不过滤)。"""
    value = (shape or "").strip().lower()
    value = LEGACY_SHAPE_ALIASES.get(value, value)
    return value if value in VALID_CONTENT_SHAPES else ""


X_SOURCE_TYPES = frozenset({"x", "x_timeline"})
PODCAST_SOURCE_TYPES = frozenset({"podcast", "podcast_rss"})


def source_shape(
    source_id: Optional[str],
    content_type: Optional[str],
    registry_meta: Dict[str, Dict[str, Any]],
) -> str:
    """解析某源的内容形态：article | social | podcast。"""
    meta = registry_meta.get(source_id or "")
    if meta is not None:
        return normalize_shape(str(meta.get("shape") or "article")) or "article"
    if (content_type or "") in SOCIAL_CONTENT_TYPES:
        return "social"
    if (content_type or "") in PODCAST_CONTENT_TYPES:
        return "podcast"
    return "article"


def configured_source_shape(source_type: str, fetcher_id: str = "") -> str:
    """解析 SourceConfigRecord 的形态，无需给配置表增加派生列。

    显式 fetcher_id 的类属性优先；未显式绑定时，X 配置源稳定归入 social。
    """
    if fetcher_id:
        fetcher_class = fetcher_registry.get_class(fetcher_id)
        if fetcher_class is not None:
            shape = normalize_shape(str(getattr(fetcher_class, "content_shape", "article")))
            if shape:
                return shape
    if (source_type or "").strip().lower() in X_SOURCE_TYPES:
        return "social"
    if (source_type or "").strip().lower() in PODCAST_SOURCE_TYPES:
        return "podcast"
    return "article"


def configured_source_content_type(source_type: str, fetcher_id: str = "") -> str:
    """解析配置源的预期 content_type，供零产出的阅读器目录使用。"""
    if fetcher_id:
        fetcher_class = fetcher_registry.get_class(fetcher_id)
        if fetcher_class is not None:
            return str(getattr(fetcher_class, "content_type", "") or "")
    source_type_value = (source_type or "").strip().lower()
    if source_type_value in X_SOURCE_TYPES:
        return "social_post"
    if source_type_value in PODCAST_SOURCE_TYPES:
        return "podcast_episode"
    if source_type_value in {"rss", "atom"}:
        return "rss_article"
    if source_type_value in {"web", "webpage"}:
        return "web_article"
    return ""


def configured_source_platform(source_type: str, fetcher_id: str = "") -> str:
    """SourceConfig 的平台标识：显式 fetcher 优先，X 类型兜底为 x。"""
    if fetcher_id:
        fetcher_class = fetcher_registry.get_class(fetcher_id)
        if fetcher_class is not None:
            return str(getattr(fetcher_class, "platform", "") or "").strip().lower()
    if (source_type or "").strip().lower() in X_SOURCE_TYPES:
        return "x"
    return ""


def registry_source_ids_for_shape(shape: str) -> List[str]:
    """注册表中指定形态的 source_id 集合（shape= SQL 过滤用）。"""
    shape_value = normalize_shape(shape)
    if not shape_value:
        return []
    return sorted(
        sid for sid, meta in _registry_source_meta().items()
        if (normalize_shape(str(meta.get("shape") or "article")) or "article") == shape_value
    )


def social_registry_source_ids() -> List[str]:
    """注册表中 social 源的 source_id 集合。"""
    return registry_source_ids_for_shape("social")


def _registry_source_meta() -> Dict[str, Dict[str, Any]]:
    """source_id -> 抓取器注册元数据（名称/简介/图标），用于内容源目录展示。"""
    return {meta["id"]: meta for meta in fetcher_registry.get_all_metadata()}


def _friendly_source_name(source_id: str, registry_meta: Dict[str, Dict[str, Any]]) -> str:
    meta = registry_meta.get(source_id)
    if meta and meta.get("name"):
        return meta["name"]
    return friendly_source_name(source_id)
