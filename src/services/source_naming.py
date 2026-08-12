"""来源取名运行时单点 (src/services/source_naming.py)。

v3.31 退役清仓波自 storage/impl/vector_storage.py 迁出(原模块随向量层退役,
取名职责与向量存储本就无关)。v3.22.5 源命名核对波拍板的机制不变:

现役源一律经 :func:`friendly_source_name` 从抓取器注册表现取(改名零维护);
``SOURCE_FRIENDLY_NAMES`` 只登记注册表查不到的 id——日报特殊源(非抓取器)与
「删类下线但归档文章仍留库」的历史源(名单同步 ``registry.DECOMMISSIONED_FETCHER_IDS``)。
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
