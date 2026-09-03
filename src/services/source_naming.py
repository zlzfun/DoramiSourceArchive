"""来源取名与角色运行时单点 (src/services/source_naming.py)。

v3.31 退役清仓波自 storage/impl/vector_storage.py 迁出(原模块随向量层退役,
取名职责与向量存储本就无关)。v3.22.5 源命名核对波拍板的机制不变:

现役源一律经 :func:`friendly_source_name` 从抓取器注册表现取(改名零维护);
``SOURCE_FRIENDLY_NAMES`` 只登记注册表查不到的 id——日报特殊源(非抓取器)与
「删类下线但归档文章仍留库」的历史源(名单同步 ``registry.DECOMMISSIONED_FETCHER_IDS``)。

v3.35 起本模块同时是**信息角色**的后端单点::func:`source_role` 以运行时
SourceConfig/注册表/内建目录元数据(source_scope / provenance_tier)判定
官方/媒体/个人/榜单,与前端
``sourceTaxonomy.js`` 的 ``sourceRoleOf`` 同一套判定序(个人→榜单→媒体→官方),
供日报权威机械层(同事件代表权/同分排序/跨天查重官方例外)消费。
"""

from typing import Dict, Optional

SOURCE_FRIENDLY_NAMES: Dict[str, str] = {
    "dorami_daily_brief": "哆啦美·AI资讯日报",
    # 已下线源(历史归档仍在,来源展示需要可读名)
    "docs_gemini_api_changelog":               "Gemini API Changelog",
    "docs_openai_api_changelog":               "OpenAI API Changelog",
    "docs_alibaba_model_studio_announcements": "阿里云百炼公告",
    "github_qwen_code_releases":               "Qwen Code Releases",
    "web_bytedance_seed_models":               "ByteDance Seed Models",
    "x_ai_at_meta":                            "X · AI at Meta",
    "x_openrouter":                            "X · OpenRouter",
}


# ── 信息角色(官方/媒体/个人/榜单)──
# 三个 scope 集合与判定序**必须**与 frontend/src/sourceTaxonomy.js 保持逐字一致
# (前端是展示半边,这里是日报权威机械层的判定半边;改一处须同步另一处)。
PERSONAL_SCOPES = frozenset({
    "personal_commentary", "expert_commentary", "executive_commentary", "expert_newsletter",
})
LEADERBOARD_SCOPES = frozenset({
    "community", "developer_community", "research_community", "forum",
    "ai_benchmark_platform", "ai_benchmark_analysis",
})
MEDIA_SCOPES = frozenset({"ai_media", "tech_media"})

# 缺少任何可考策展元数据的源不给官方待遇,统一按媒体处理。
DEFAULT_SOURCE_ROLE = "media"


def _role_from_metadata(scope: str, tier: str, *, fallback: str) -> str:
    if tier == "tier2_personal_social" or scope in PERSONAL_SCOPES:
        return "personal"
    if scope in LEADERBOARD_SCOPES:
        return "leaderboard"
    if scope in MEDIA_SCOPES or tier == "tier1_curated":
        return "media"
    return fallback


def source_role(
    source_id: str,
    *,
    source_scope: Optional[str] = None,
    provenance_tier: Optional[str] = None,
) -> str:
    """source_id → 信息角色 'official' | 'media' | 'personal' | 'leaderboard'。

    判定序与前端 sourceRoleOf 一致:个人→榜单→媒体→官方。调用方已持有的
    SourceConfig 元数据优先，其次查注册表，再以 Podcast 内建目录兜底；三处都
    缺失才回落 media，权威加成只授予身份可考的策展源。
    """
    if not source_id:
        return DEFAULT_SOURCE_ROLE
    # Runtime SourceConfig metadata is authoritative when the caller already has
    # it.  Empty metadata on an arbitrary config source must not grant the
    # registry-only official fallback.
    if source_scope is not None or provenance_tier is not None:
        normalized_scope = str(source_scope or "")
        normalized_tier = str(provenance_tier or "")
        return _role_from_metadata(
            normalized_scope,
            normalized_tier,
            fallback=(
                "official"
                if normalized_scope or normalized_tier
                else DEFAULT_SOURCE_ROLE
            ),
        )
    try:
        from fetchers.registry import fetcher_registry

        fetcher_class = fetcher_registry.get_class(source_id)
    except Exception:  # noqa: BLE001 - 判定失败绝不阻断日报主流程
        fetcher_class = None
    if fetcher_class is not None:
        return _role_from_metadata(
            str(getattr(fetcher_class, "source_scope", "") or ""),
            str(getattr(fetcher_class, "provenance_tier", "") or ""),
            fallback="official",
        )

    # Curated Podcast IDs deliberately are not registry classes: all execute via
    # the shared generic_podcast_rss template.  The immutable catalog is a cheap
    # fallback for callers that do not already hold their SourceConfig row.
    try:
        from services.podcast_catalog import catalog_by_id

        catalog_source = catalog_by_id().get(source_id)
    except Exception:  # noqa: BLE001 - role lookup must never block a digest
        catalog_source = None
    if catalog_source is not None:
        return _role_from_metadata(
            str(catalog_source.source_scope or ""),
            str(catalog_source.provenance_tier or ""),
            fallback="official",
        )
    return DEFAULT_SOURCE_ROLE


def friendly_source_name(source_id: str) -> str:
    """source_id → 读者可读来源名:现役源取注册表 name,其余查兜底映射,最后回落原 id。

    延迟导入注册表(触发 impl/ 扫描),避免服务层在 import 期背上抓取器依赖。
    """
    if not source_id:
        return source_id
    try:
        from fetchers.registry import fetcher_registry

        fetcher_class = fetcher_registry.get_class(source_id)
        name = getattr(fetcher_class, "name", "") if fetcher_class else ""
        if name:
            return str(name)
    except Exception:  # noqa: BLE001 - 取名失败绝不阻断展示/检索主流程
        pass
    if source_id in SOURCE_FRIENDLY_NAMES:
        return SOURCE_FRIENDLY_NAMES[source_id]
    # 第三级兜底(v3.40):配置源(用户自定源/X config 源等)不在注册表与兜底表,
    # 查 source_configs 行取展示名——否则问答上下文块头/引用出处显示裸 id。
    name = _config_source_name(source_id)
    return name or source_id


def _config_source_name(source_id: str) -> str:
    """SourceConfigRecord.name 兜底查询;任何失败(无 DB/无行)静默返回空串。"""
    try:
        from sqlmodel import Session

        from api import deps
        from models.db import SourceConfigRecord

        sink = deps.get_db_sink()
        if sink is None:
            return ""
        with Session(sink.engine) as session:
            record = session.get(SourceConfigRecord, source_id)
            return str(record.name) if record is not None and record.name else ""
    except Exception:  # noqa: BLE001 - 同上,兜底取名绝不抛
        return ""
