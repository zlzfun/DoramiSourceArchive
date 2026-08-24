"""源合集(策展合集)注册表——发现页的策展视图 + 批量订阅动作。

定调(docs/source-collections-wave-plan.md §0):合集是目录呈现层的策展视图,
**不是订阅实体**——订阅粒度仍是 source_id,feed/MCP/未读/隐藏/内容交付链路
全部不感知合集。「订阅合集」= 一次性批量订阅其当前成员;合集后续新增成员
不会自动推给已订阅者(无绑定记录,「跟随合集」留作观察后的二期)。

策展存储走代码注册表(与 preset fetcher「代码即记录」同路线):成员本来就是
要改代码+部署才能加的 preset 源,合集与源同一 commit,git 即审计。将来若出现
非代码路径的策展者,再演进为 KV + 管理面 CRUD(照 source_visibility 五元素范式)。

命名纪律:`collection` 一词在本项目已被「采集任务 collection-jobs」占用,
后端模块一律 source_collections,API 路径域 /api/reader/collections,UI 中文词「合集」。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class SourceCollection:
    collection_id: str            # kebab 稳定标识(API 路径段/前端 key)
    name: str
    description: str              # 内容式一句话(取名规范沿 node_audit_playbook)
    provenance_note: str          # 策展来源注,可含 URL(前端白名单渲染为链接)
    source_ids: Tuple[str, ...]   # 成员,有序;同一源可出现在多个合集


SOURCE_COLLECTIONS: Tuple[SourceCollection, ...] = (
    SourceCollection(
        collection_id="hn-popular-blogs-2025",
        name="2025 人气独立博客",
        description="Hacker News 社区 2025 年最受欢迎的个人技术博客,AI 与软件工程一线从业者的深度个人写作。",
        # 注:note 支持受限 markdown 子集(**加粗** 与 [文字](http(s) 链接)),
        # 前端复用公告白名单渲染器(utils/announcementText.jsx),零注入面。
        provenance_note=(
            "Karpathy 分享了 Evan Schwartz 统计的"
            "[2025 Hacker News 最受欢迎 RSS 博客榜单](https://gist.github.com/emschwartz/e6d2bf860ccc367fe37ff953ba6de66b)"
            ",本合集基于该榜单拓展收录。"
        ),
        source_ids=(
            "rss_sean_goedecke",
            "rss_giles_thomas",
            "rss_max_woolf",
            "rss_geohot",
            "rss_geoffrey_litt",
            "rss_martin_alderson",
            "rss_anil_dash",
        ),
    ),
    SourceCollection(
        collection_id="frontier-labs-official",
        name="前沿实验室官方",
        description="头部大模型实验室的官方博客与新闻页,模型发布与研究进展的一手信息位。",
        provenance_note="站内现役官方源的策展重组:覆盖 Anthropic/OpenAI/DeepMind/Meta/Mistral 与国内头部实验室。",
        source_ids=(
            "web_anthropic_news",
            "rss_openai_news",
            "rss_deepmind_blog",
            "web_meta_ai_blog",
            "rss_mistral_news",
            "web_qwen_blog",
            "web_kimi_research",
            "web_minimax_research",
        ),
    ),
    SourceCollection(
        collection_id="cn-open-models",
        name="国产开源模型动态",
        description="国产大模型厂商的一手动态:官方博客、开源权重发布、代码仓库与 X 官方账号。",
        provenance_note=(
            "站内现役国产厂商源的策展重组:Qwen/DeepSeek/智谱/Kimi/MiniMax/字节 Seed 六家,"
            "博客·HF 权重·仓库·X 官号跨三形态混排,开源权重与官方宣发同框可追。"
        ),
        source_ids=(
            "web_qwen_blog",
            "hf_qwen_models",
            "x_alibaba_qwen",
            "docs_deepseek_api_changelog",
            "hf_deepseek_models",
            "github_deepseek_repositories",
            "x_deepseek_ai",
            "docs_zai_new_released",
            "x_zai_org",
            "web_kimi_research",
            "x_moonshot_ai",
            "web_minimax_research",
            "web_bytedance_seed_research",
        ),
    ),
    SourceCollection(
        collection_id="ai-coding-tools",
        name="AI 编程工具动态",
        description="主流 AI 编程工具的官方 Changelog 与 Releases,版本更新与新能力的一手位。",
        provenance_note=(
            "选取标准=agent 编程工具的官方更新通道:"
            "Claude Code/Codex/Cursor/OpenCode/OpenClaw/Hermes Agent。"
        ),
        source_ids=(
            "docs_claude_code_changelog",
            "docs_openai_codex_changelog",
            "web_cursor_changelog",
            "github_opencode_releases",
            "github_openclaw_releases",
            "github_hermes_agent_releases",
        ),
    ),
    SourceCollection(
        collection_id="ai-deep-writing",
        name="AI 深度写作",
        description="AI 领域公认深度作者的个人博客与 newsletter,方法综述、行业洞察与一线实践长文。",
        provenance_note=(
            "与「2025 人气独立博客」成对的另一半个人写作策展:领域深度作者(研究者/分析者)"
            "而非社区人气榜——Lilian Weng/Raschka/Interconnects/Import AI/Latent Space/Mollick/Simon Willison。"
        ),
        source_ids=(
            "rss_lilianweng",
            "rss_raschka",
            "rss_interconnects",
            "rss_import_ai",
            "rss_latent_space",
            "rss_oneusefulthing",
            "rss_simonwillison",
        ),
    ),
)


def list_collections() -> Tuple[SourceCollection, ...]:
    return SOURCE_COLLECTIONS


def get_collection(collection_id: str) -> Optional[SourceCollection]:
    for collection in SOURCE_COLLECTIONS:
        if collection.collection_id == collection_id:
            return collection
    return None


def serialize_collection(collection: SourceCollection) -> Dict[str, Any]:
    """API 轻载荷:成员的订阅态/卡片数据由前端与 GET /api/reader/sources 目录 join。"""
    return {
        "collection_id": collection.collection_id,
        "name": collection.name,
        "description": collection.description,
        "provenance_note": collection.provenance_note,
        "source_ids": list(collection.source_ids),
    }
