"""来源取名与角色运行时单点 (src/services/source_naming.py)。

v3.31 退役清仓波自 storage/impl/vector_storage.py 迁出(原模块随向量层退役,
取名职责与向量存储本就无关)。v3.22.5 源命名核对波拍板的机制不变:

现役源一律经 :func:`friendly_source_name` 从抓取器注册表现取(改名零维护);
``SOURCE_FRIENDLY_NAMES`` 只登记注册表查不到的 id——日报特殊源(非抓取器)与
「删类下线但归档文章仍留库」的历史源(名单同步 ``registry.DECOMMISSIONED_FETCHER_IDS``)。

v3.35 起本模块同时是**信息角色**的后端单点::func:`source_role` 以注册表元数据
(source_scope / provenance_tier)判定 官方/媒体/个人/榜单,与前端
``sourceTaxonomy.js`` 的 ``sourceRoleOf`` 同一套判定序(个人→榜单→媒体→官方),
供日报权威机械层(同事件代表权/同分排序/跨天查重官方例外)消费。
"""

from typing import Dict

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

# 注册表查不到的源(config 自建源/已下线源)不给官方待遇,统一按媒体处理。
DEFAULT_SOURCE_ROLE = "media"


def source_role(source_id: str) -> str:
    """source_id → 信息角色 'official' | 'media' | 'personal' | 'leaderboard'。

    判定序与前端 sourceRoleOf 一致:个人→榜单→媒体→官方(官方是注册表源的
    兜底档——策展 preset 未标 scope 即默认官方口径)。注册表查不到一律 media,
    权威加成只授予身份可考的策展源。
    """
    if not source_id:
        return DEFAULT_SOURCE_ROLE
    try:
        from fetchers.registry import fetcher_registry

        fetcher_class = fetcher_registry.get_class(source_id)
    except Exception:  # noqa: BLE001 - 判定失败绝不阻断日报主流程
        fetcher_class = None
    if fetcher_class is None:
        return DEFAULT_SOURCE_ROLE
    scope = str(getattr(fetcher_class, "source_scope", "") or "")
    tier = str(getattr(fetcher_class, "provenance_tier", "") or "")
    if tier == "tier2_personal_social" or scope in PERSONAL_SCOPES:
        return "personal"
    if scope in LEADERBOARD_SCOPES:
        return "leaderboard"
    if scope in MEDIA_SCOPES or tier == "tier1_curated":
        return "media"
    return "official"


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
    return SOURCE_FRIENDLY_NAMES.get(source_id, source_id)
