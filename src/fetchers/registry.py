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


FOCUSED_FETCHER_CURATION: Dict[str, Dict[str, str | bool]] = {
    # Core AI news, AI applications, model vendors, and agent/product signals.
    "rss_openai_news": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "OpenAI 官方产品、模型、Codex/Agent 相关新闻核心源。",
    },
    "web_anthropic_news": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "Anthropic 公司级新闻、模型、安全与企业动态。",
    },
    "web_claude_blog": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "Claude、Claude Code 与 Agent 产品更新。",
    },
    "rss_google_ai_blog": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "Google AI/Gemini 相关官方动态。",
    },
    "rss_google_deepmind_news": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "DeepMind 模型、研究与产品新闻。",
    },
    "web_mistral_news": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "Mistral 模型与产品发布。",
    },
    "rss_microsoft_ai_blog": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "Copilot、Azure AI 与企业 AI 应用动态。",
    },
    "web_elevenlabs_blog": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "语音 AI 应用和 Agent 场景。",
    },
    "web_runway_news": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "视频生成与创作 AI 应用层动态。",
    },
    "web_stability_news": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "图像、视频生成模型与产品动态。",
    },
    "github_claude_code_releases": {
        "curation_tier": "core",
        "default_visible": True,
        "curation_reason": "Claude Code 属于 Agent 产品工具，保留 release 信号。",
    },

    # Useful but noisier sources. Keep visible, but label as watchlist.
    "rss_huggingface_blog": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "可能包含新模型和生态新闻，但会混入工程/社区内容。",
    },
    "github_openai_agents_python_releases": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "OpenAI Agent 方向相关，但 SDK release 仍偏框架信号。",
    },
    "github_comfyui_releases": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "AI 创作工具生态，可按图像/视频应用关注度决定。",
    },
    "github_open_webui_releases": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "本地 AI 应用壳，偏产品工具但噪声较高。",
    },
    "wechat_jiqizhixin": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "中文 AI 新闻覆盖面强，需要后续过滤重复和泛行业内容。",
    },
    "wechat_qbitai": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "中文 AI 新闻覆盖面强，需要后续过滤重复和泛行业内容。",
    },
    "wechat_xinzhiyuan": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "中文 AI 新闻覆盖面强，需要后续过滤重复和泛行业内容。",
    },
    "wechat_ai_tech_review": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "中文 AI 研究/产业媒体，需先用真实抓取验证输出质量。",
    },
    "wechat_infoq_ai": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "中文 AI 工程/产业媒体，需先用真实抓取验证输出质量。",
    },
    "wechat_zhidx": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "AI 硬件与产业新闻，需先用真实抓取验证输出质量。",
    },
    "wechat_founder_park": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "AI 创业与产品生态，需先用真实抓取验证输出质量。",
    },
    "wechat_silicon_star": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "硅谷与 AI 产品生态，需先用真实抓取验证输出质量。",
    },
    "wechat_xixiaoyao": {
        "curation_tier": "watch",
        "default_visible": True,
        "curation_reason": "技术解读和论文趋势，需确认是否过于论文化。",
    },

    # Hidden by product focus: infrastructure, frameworks, generic paper/community firehoses,
    # and workflow nodes remain callable but stay out of the focused catalog.
    "generic_rss": {
        "curation_tier": "advanced",
        "default_visible": False,
        "curation_reason": "通用 RSS 能力入口，适合手工扩展，不作为聚焦资讯源展示。",
    },
    "generic_github_releases": {
        "curation_tier": "advanced",
        "default_visible": False,
        "curation_reason": "通用 GitHub Releases 能力入口，适合孵化热门工具源，默认隐藏以降低目录噪声。",
    },
    "webhook_dify_workflow": {
        "curation_tier": "system",
        "default_visible": False,
        "curation_reason": "后置工作流触发节点，不是入站资讯来源。",
    },
    "rss_langchain_blog": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "应用搭建框架动态，和当前 AI 资讯/模型/Agent 产品聚焦弱相关。",
    },
    "rss_langchain_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "框架 release 噪声较高，默认隐藏。",
    },
    "github_langchain_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "框架 release 噪声较高，默认隐藏。",
    },
    "rss_transformers_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "底层模型库 release，不属于默认 AI 资讯源。",
    },
    "github_transformers_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "底层模型库 release，不属于默认 AI 资讯源。",
    },
    "rss_pytorch_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "底层训练框架 release，不属于默认 AI 资讯源。",
    },
    "github_pytorch_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "底层训练框架 release，不属于默认 AI 资讯源。",
    },
    "rss_vllm_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "推理框架 release，不属于默认 AI 资讯源。",
    },
    "github_vllm_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "推理框架 release，不属于默认 AI 资讯源。",
    },
    "rss_llama_cpp_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "本地推理底座 release，默认隐藏。",
    },
    "github_llama_cpp_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "本地推理底座 release，默认隐藏。",
    },
    "github_litellm_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "LLM 网关/开发基础设施 release，默认隐藏。",
    },
    "rss_ollama_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "本地模型运行工具 release，偏基础设施，默认隐藏。",
    },
    "github_ollama_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "本地模型运行工具 release，偏基础设施，默认隐藏。",
    },
    "rss_nvidia_developer_blog": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "偏硬件、训练、推理优化等工程内容，默认隐藏。",
    },
    "rss_github_blog": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "开发平台泛流，AI 相关浓度不稳定，默认隐藏。",
    },
    "rss_hn_ai": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "社区搜索流噪声较高，默认隐藏。",
    },
    "rss_dify_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "应用搭建平台 release，默认隐藏。",
    },
    "github_dify_releases": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "应用搭建平台 release，默认隐藏。",
    },
    "rss_arxiv_cs_ai": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "泛论文流不等于 AI 资讯，默认隐藏。",
    },
    "rss_arxiv_cs_cl": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "泛论文流不等于 AI 资讯，默认隐藏。",
    },
    "rss_arxiv_cs_cv": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "泛论文流不等于 AI 资讯，默认隐藏。",
    },
    "rss_arxiv_cs_lg": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "泛论文流不等于 AI 资讯，默认隐藏。",
    },
    "rss_arxiv_eess_iv": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "泛论文流不等于 AI 资讯，默认隐藏。",
    },
    "rss_arxiv_stat_ml": {
        "curation_tier": "hidden",
        "default_visible": False,
        "curation_reason": "泛论文流不等于 AI 资讯，默认隐藏。",
    },
}


def focused_curation_for(source_id: str, category: str) -> Dict[str, str | bool]:
    if source_id in FOCUSED_FETCHER_CURATION:
        return FOCUSED_FETCHER_CURATION[source_id]
    if category == "advanced":
        return {
            "curation_tier": "advanced",
            "default_visible": False,
            "curation_reason": "通用扩展能力，不属于默认 AI 资讯源。",
        }
    if category == "workflow":
        return {
            "curation_tier": "system",
            "default_visible": False,
            "curation_reason": "工作流触发节点，不是入站资讯来源。",
        }
    return {
        "curation_tier": "hidden",
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
                **curation,
            })
        return metadata_list


# ==========================================
# 单例实例化与自启动
# ==========================================
fetcher_registry = FetcherRegistry()

# 当此文件被 app.py 导入时，立刻执行一次全目录扫描发现
fetcher_registry.discover()
