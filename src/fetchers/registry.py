"""
动态抓取器注册中心 (src/fetchers/registry.py)

利用 Python 反射与包扫描机制，实现抓取器的零配置热插拔。
只要在 impl 目录下继承了 BaseFetcher 并填写了必要的类属性，即会自动在此注册。
"""

import importlib
import pkgutil
import inspect
from typing import Type, Dict, List, Any, Optional
from fetchers.base import BaseFetcher


ESSENTIAL_FETCHER_IDS = frozenset({
    # OpenAI / ChatGPT / Codex
    "rss_openai_news",
    "docs_openai_codex_changelog",
    # Anthropic / Claude
    "web_anthropic_news",
    "web_claude_blog",
    "docs_claude_code_changelog",
    # Google / Gemini / Gemma / Antigravity
    "rss_google_gemini_models",
    "docs_gemma_release_notes",
    # xAI / Grok
    "docs_xai_release_notes",
    # Alibaba / Qwen
    "web_qwen_blog",
    # DeepSeek
    "docs_deepseek_api_changelog",
    "github_deepseek_repositories",
    "hf_deepseek_models",
    # Zhipu / Z.ai / GLM
    "docs_zai_new_released",
    # ByteDance Seed
    "web_bytedance_seed_models",
    "web_bytedance_seed_research",
    # Tier1 media / community / paper signal
    "web_qbitai",
    "web_aiera",
    "web_jiqizhixin",
    "web_ithome_ai",
    "rss_hn_ai",
    "web_huggingface_daily_papers",
    # Agent coding tools
    "web_cursor_changelog",
    "github_opencode_releases",
    "github_openclaw_releases",
    "github_hermes_agent_releases",
})


FOCUSED_FETCHER_CURATION: Dict[str, Dict[str, str | bool]] = {}


def focused_curation_for(source_id: str, category: str) -> Dict[str, str | bool]:
    if source_id in ESSENTIAL_FETCHER_IDS:
        curation = FOCUSED_FETCHER_CURATION.get(source_id, {})
        reason = str(curation.get("curation_reason") or "")
        if reason.startswith("候选待加入："):
            reason = reason.replace("候选待加入：", "推荐审查源：", 1)
        if not reason:
            reason = "推荐审查源：已按 v1.0 维度完成书面化标注，进入从 0 做加法的当前批次。"
        return {
            **curation,
            "default_visible": True,
            "curation_reason": reason,
        }
    if source_id in FOCUSED_FETCHER_CURATION:
        curation = FOCUSED_FETCHER_CURATION[source_id]
        if curation.get("default_visible") is True:
            return {
                **curation,
                "default_visible": False,
                "curation_reason": f"{curation.get('curation_reason', '')} 未列入精品源白名单，默认隐藏。",
            }
        return curation
    if category == "advanced":
        return {
            "default_visible": False,
            "curation_reason": "通用扩展能力，不属于默认 AI 资讯源。",
        }
    if category == "workflow":
        return {
            "default_visible": False,
            "curation_reason": "工作流触发节点，不是入站资讯来源。",
        }
    return {
        "default_visible": False,
        "curation_reason": "与当前 AI 资讯/应用/模型/Agent 聚焦目标弱相关，默认隐藏。",
    }


class FetcherRegistry:
    def __init__(self):
        # 内部维护的映射表： { "source_id": FetcherClass }
        self._fetchers: Dict[str, Type[BaseFetcher]] = {}

    def register(self, fetcher_class: Type[BaseFetcher]):
        """手动注册抓取器类"""
        if not issubclass(fetcher_class, BaseFetcher) or fetcher_class is BaseFetcher:
            return

        source_id = getattr(fetcher_class, 'source_id', 'unknown')
        if source_id == "unknown_source" or source_id == "unknown":
            return  # 跳过没有正确定义源ID的中间抽象类

        self._fetchers[source_id] = fetcher_class

    def discover(self, package_name: str = "fetchers.impl"):
        """
        动态扫描并注册指定包下的所有抓取器。
        默认扫描 src/fetchers/impl 文件夹。
        """
        try:
            # 导入包本身
            package = importlib.import_module(package_name)

            # 遍历包目录下的所有模块 (.py 文件)
            for _, module_name, _ in pkgutil.iter_modules(package.__path__):
                full_module_name = f"{package_name}.{module_name}"
                try:
                    module = importlib.import_module(full_module_name)

                    # 提取模块中定义的所有类
                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        # 判断是否为 BaseFetcher 的子类（且排除自身）
                        if issubclass(obj, BaseFetcher) and obj is not BaseFetcher:
                            self.register(obj)
                except Exception as module_err:
                    # ✨ 修复：隔离单个模块的加载错误，防止一颗老鼠屎坏了一锅粥
                    print(f"⚠️ 无法加载抓取器模块 [{full_module_name}]: {module_err}")

            print(f"🔌 抓取器注册中心就绪，成功挂载 {len(self._fetchers)} 个数据源节点。")

        except ImportError as e:
            print(f"⚠️ 无法扫描抓取器包 {package_name}: {e}")

    def get_class(self, source_id: str) -> Optional[Type[BaseFetcher]]:
        """根据 ID 获取抓取器类"""
        return self._fetchers.get(source_id)

    def get_all_metadata(self) -> List[Dict[str, Any]]:
        """
        生成给前端的注册表大纲，前端据此渲染出所有的面板和表单。
        """
        metadata_list = []
        for source_id, cls in self._fetchers.items():
            category = getattr(cls, "category", "general")
            curation = focused_curation_for(source_id, category)
            metadata_list.append({
                "id": source_id,
                "name": cls.name,
                "icon": cls.icon,
                "desc": cls.description,
                "category": category,
                "content_type": cls.content_type,
                "active": True,
                "parameters": cls.get_parameter_schema(),
                "source_owner": getattr(cls, "source_owner", ""),
                "source_brand": getattr(cls, "source_brand", ""),
                "source_scope": getattr(cls, "source_scope", ""),
                "source_channel": getattr(cls, "source_channel", ""),
                "source_url": getattr(cls, "source_url", ""),
                "base_url": getattr(cls, "source_url", "") or getattr(cls, "listing_url", "") or getattr(cls, "feed_url", ""),
                "provenance_tier": getattr(cls, "provenance_tier", ""),
                "content_tags": list(getattr(cls, "content_tags", []) or []),
                "signal_strength": getattr(cls, "signal_strength", ""),
                "noise_risk": getattr(cls, "noise_risk", ""),
                "fetch_reliability": getattr(cls, "fetch_reliability", ""),
                **curation,
            })
        return metadata_list


# ==========================================
# 单例实例化与自启动
# ==========================================
fetcher_registry = FetcherRegistry()

# 当此文件被 app.py 导入时，立刻执行一次全目录扫描发现
fetcher_registry.discover()
