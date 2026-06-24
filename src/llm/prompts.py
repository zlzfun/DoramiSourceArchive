"""日报生成提示词 (src/llm/prompts.py)

- MAP 阶段：对单篇文章概括 + 打重要性分（沿用原 Dify 概括 schema，新增 score）。
- REDUCE 阶段：把择优后的条目汇总成与 dorami-daily-brief Skill 风格一致的 Markdown，
  并注入近期日报上下文做语义/事件级去重。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


# content_type / classification → 日报分类标签（对齐 skill_templates/dorami-daily-brief/SKILL.md）
# 顺序即日报分类的呈现顺序：模型发布置顶、学术论文垫底。
CATEGORY_LABELS: Dict[str, str] = {
    "model_release": "🚀 模型发布",
    "wechat_article": "📱 行业资讯",
    "ai_company_blog": "📱 行业资讯",
    "github_release": "🔧 开源动态",
    "github_repository": "🔧 开源动态",
    "hf_model": "🚀 模型发布",
    "tech_conference": "🎤 技术大会",
    "social_post": "💬 社交动态",
    "ai_community": "💬 社交动态",
    "web_article": "🌐 资讯聚合",
    "rss_article": "🌐 资讯聚合",
    "arxiv": "📄 学术论文",
}
DEFAULT_CATEGORY_LABEL = "📌 其他资讯"

# 允许的中文分类集合（约束 MAP 阶段 classification 取值），顺序即日报呈现顺序。
# 注意：导出 shendeng 时由 export_shendeng_daily_news 原样透传分类（shendeng 已兼容多分类）。
ALLOWED_CLASSIFICATIONS = ["模型发布", "行业资讯", "开源动态", "技术大会", "社交动态", "资讯聚合", "学术论文"]


MAP_SYSTEM_PROMPT = """你是一位极具洞察力的前沿 AI 架构师与行业分析师，为一份面向 AI 从业者读者的资讯日报供稿。读者最关心的是：新模型/新能力发布、重要 AI 应用与产品更新、大厂与业界重大新闻、有明确新意的研究。请仔细阅读下方单篇资讯，严格基于正文事实提炼高质量中文简报。只依据正文事实，绝不臆造原文未出现的数字、结论或参数；正文信息不足时宁可少写也不要编造。

【核心任务要求】
 1. title_cn: 提取核心主旨。原标题为英文需信达雅地翻译；若无实际标题需精准提炼。务必具体（点出主体/产品/数字），不要用「某公司发布新模型」这类空泛标题。
 2. classification: 从以下类别中按优先级准确判断（取最贴切的一个中文词）：
    - 「模型发布」(最高优先)：任何新模型 / 新能力 / 新版本上线，闭源或开源均算，含 SOTA、新范式、具身/机器人/世界模型的真实模型发布（如 Qwen-Robot、GLM、MiniMax M3 这类带权重或可调用的模型）。
    - 「开源动态」：开源工具 / 框架 / 代码库 / 仓库的发布或重要更新，但其本体不是模型（如 coding agent CLI、SDK、库）。
    - 「学术论文」：arxiv / 论文 / Daily Papers 类研究。
    - 「技术大会」：线下大会 / 峰会演讲、发布会上的技术分享（如智源大会、各家 Dev Day）。
    - 「社交动态」：X/推特、社区讨论性质的零散消息。
    - 「行业资讯」：厂商动态、产品更新、融资、收购、政策、算力等其余业界新闻。
    - 「资讯聚合」：聚合类、确实无法归入上述任何一类的。
 3. source: 推断信息来源的准确中文/官方名称。若【指定来源】有值则优化并使用；为空则结合正文聪慧推断最标准的媒体或机构名称（如「机器之心」「Google AI」）。
 4. company: 提取最主要涉及的科技厂商（如 OpenAI、Microsoft、Google、Meta 等）。若无明显厂商，输出空字符串 ""。
 5. realm: 归纳所属的 AI 核心领域，如「基础大模型」「AI Agent」「具身智能」「多模态」「算力架构」等专业领域词汇。
 6. summary: 提取 1-3 条核心看点。严禁泛泛而谈！每一条必须严格使用「**核心概念/技术名**：具体实现细节」的格式（冒号前加粗）。冒号后要落到可验证的实质：机制、架构、关键数字（参数量/上下文长度/价格/榜单分数等）、与既有方案的差异。看点不足 3 条就只写 1-2 条，不要为凑数稀释。
 7. comment: 撰写 100-150 字的硬核专业点评，回答「这件事为什么重要」——指出工程创新点、对开发链路/成本结构的启发或商业与竞争格局冲击。要有判断与取舍，绝禁「值得关注」「未来可期」这类套话。
 8. tags: 生成 1-4 个精准的常规技术标签。
 9. score: 给出该资讯的重要性评分，0-10 的数字。综合技术突破度、行业影响力、时效性与稀缺性，保持区分度、不要一律给高分。评分锚点：
    - 9-10 = 行业级重大突破/范式转变（重磅模型发布、格局级大事件）。
    - 7-8 = 头部厂商重要的模型/产品发布、有明确新意的研究、业界重大新闻（重大融资/收购/政策）。
    - 4-6 = 常规更新、增量改进、二线消息。
    - 0-3 = 边角消息、信息量稀薄。
    【读者相关性调整（重要，会改变上面的锚点）】
    - 车载/智能座舱/自动驾驶类内容（如车机助手、智驾系统落地）**降权**：除非是行业级大事件，否则 score 上限压到 4-5。注意：通用具身/机器人/世界模型的真实模型发布**不**算车载，按其技术价值正常打分。
    - 公众号软广 / 营销通稿 / PR 稿、站台与赛事 / 招募 / 榜单认证类（典型信号：「XX 权威认证」「XX 大赛」「英雄帖」「返利」「邀你参加」「重磅亮相」却无实质技术或产品信息）、信息量稀薄的纯口播稿，**重罚 score 0-2**。判断标准：通篇是宣传口吻、缺乏可验证的技术细节或实质新闻，即视为软广/营销稿压分。

【极其重要的格式输出要求】
你必须且只能输出一个合法的、纯净的 JSON 对象，直接以 { 开始、以 } 结束。
绝对禁止在开头结尾添加 ```json 标记，绝对禁止输出任何无关解释文字！

期望的 JSON 结构：
{
  "title_cn": "string",
  "classification": "string",
  "source": "string",
  "company": "string",
  "realm": "string",
  "summary": ["string"],
  "comment": "string",
  "tags": ["string"],
  "score": 0
}"""


def build_map_user_prompt(*, title: str, source_name: str, body: str, max_body_chars: int = 6000) -> str:
    """构造 MAP 阶段的单篇输入。body 截断以控 token。"""
    clipped = (body or "").strip()
    if len(clipped) > max_body_chars:
        clipped = clipped[:max_body_chars] + "\n...(正文已截断)"
    return (
        "【输入数据】\n"
        f"原标题：{title or '（无标题）'}\n"
        f"指定来源：{source_name or ''}\n"
        f"正文内容：{clipped or '（无正文）'}"
    )


REDUCE_SYSTEM_PROMPT = """你是哆啦美·归档中枢的资深 AI 资讯主编。下面会给你今天择优后的若干条结构化简报，以及最近几天已发布的日报正文。请把今天的内容汇编成一篇结构化的中文 Markdown 日报。

【去重要求（重要）】
分两种去重，都要做：
1. 对比近期日报（跨天去重）：「近期日报」是最近几天已发布过的内容，仅作去重参考。若今天某条与近期日报报道的是同一事件：纯重复就省略；有实质进展的后续，只写增量并简短点出「（接前报）」。绝不重复近期日报已充分覆盖的旧内容。
2. 当天批次内合并（同日去重）：若今天有多条讲的是**同一事件**（哪怕标题措辞不同、来源不同），必须**合并为一条**，不得重复出现。合并时：标题取信息量最全的那条，总结取各条要点的并集并去重，「来源」行列出全部来源（用「· 」分隔多个来源名）。条目自带的 extra_sources（附加来源链接）也并入来源行。

【分类与排序】
按下列分类顺序组织，分类标题用对应 emoji 标签；每个分类内**按重要性（score）降序**（最重要的在前）；没有内容的分类直接省略：
- 🚀 模型发布
- 📱 行业资讯
- 🔧 开源动态
- 🎤 技术大会
- 💬 社交动态
- 🌐 资讯聚合
- 📄 学术论文
- 📌 其他资讯

【输出格式】严格遵循（每条都要有 标题 / 来源 / 总结 / 点评 四部分）：
# 🤖 哆啦美 AI 资讯日报 · {report_date}

> 共收录 N 条资讯，涵盖 M 个分类

---

## 🚀 模型发布（N 篇）

### [标题](source_url)
**来源**: 来源名 · YYYY-MM-DD
核心总结：直接采用该条目的 summary 要点（保留「**核心概念**：细节」的加粗格式），可分行列出。
> 💡 点评：采用该条目的 comment（一句到两句的硬核判断，说清为什么重要）。

---

（其余分类同理，顺序见上）

---

*由哆啦美·归档中枢生成*

【其它规则】
- 只输出 Markdown 正文，不要任何额外解释或代码围栏。
- 每条都必须包含「总结」与「点评」两部分：总结来自条目的 summary，点评来自条目的 comment（以 `> 💡 点评：` 引用块呈现）；若某条目的 comment 为空则省略点评行，不要自行编造。
- 标题链接用条目给出的 source_url；缺失则只写标题文本。合并条目时主链接用信息量最全那条，其余来源在「来源」行体现。
- 忠实于给定条目，不要新增条目里没有的事实或数字。
- 控制篇幅，宁可少而精，不要为凑数硬写。
- 末尾「仅标题条目」（无正文）以一个「📎 其它收录」分类用无序列表列出「[标题](url)」即可，不写总结与点评。
- 务必输出完整日报直到结尾的「*由哆啦美·归档中枢生成*」，不要中途截断。"""


def build_reduce_user_prompt(
    *,
    report_date: str,
    selected_items: List[Dict[str, Any]],
    title_only_items: List[Dict[str, Any]],
    recent_briefs: List[str],
) -> str:
    """构造 REDUCE 阶段输入：今日择优条目 JSON + 仅标题附录 + 近期日报正文。"""
    parts: List[str] = [f"报告日期：{report_date}", ""]

    parts.append("【今日择优条目（JSON 数组）】")
    parts.append(json.dumps(selected_items, ensure_ascii=False, indent=2))
    parts.append("")

    if title_only_items:
        parts.append("【仅标题条目（无正文，放入「📎 其它收录」）】")
        parts.append(json.dumps(title_only_items, ensure_ascii=False, indent=2))
        parts.append("")

    if recent_briefs:
        parts.append("【近期日报正文（仅供去重参考，不要复述）】")
        for idx, brief in enumerate(recent_briefs, 1):
            clipped = brief.strip()
            if len(clipped) > 4000:
                clipped = clipped[:4000] + "\n...(已截断)"
            parts.append(f"--- 近期日报 #{idx} ---")
            parts.append(clipped)
        parts.append("")

    return "\n".join(parts)


# ==========================================
# 同事件去重聚类（map 之后、select 之前的一次性 LLM 调用）
# ==========================================

DEDUP_SYSTEM_PROMPT = """你是一位 AI 资讯编辑，负责识别同一天里报道**同一事件**的重复条目。下面会给你今天的一批资讯条目，每条带一个数字 idx、标题、涉及厂商与一句话要点。

【任务】把报道**同一核心事件**的条目聚成一组（哪怕标题措辞不同、来源不同、角度略有差异，只要是同一件事就算）。判断同一事件的依据：同一产品/模型/公司的同一次发布或同一新闻事件。不同事件、仅主题相近但并非同一件事的，不要合并。

【输出】只输出一个合法纯净的 JSON 对象，以 { 开始、} 结束，禁止代码围栏与多余文字。结构：
{"clusters": [[idx, idx, ...], ...]}
每个子数组是一组同事件条目的 idx。只需输出包含 2 个及以上 idx 的重复组；单独成事件的条目不必列出。若没有任何重复，输出 {"clusters": []}。"""


def build_dedup_user_prompt(entries: List[Dict[str, Any]]) -> str:
    """构造去重聚类输入。entries 元素形如 {idx, title, company, hint}。"""
    lines: List[str] = ["【今日条目】"]
    for e in entries:
        company = (e.get("company") or "").strip()
        hint = (e.get("hint") or "").strip()
        suffix = f"（{company}）" if company else ""
        lines.append(f"idx={e.get('idx')}: {e.get('title') or ''}{suffix}")
        if hint:
            lines.append(f"    要点：{hint}")
    return "\n".join(lines)


def build_daily_brief_skill_style_guide() -> str:
    """Return the live daily-brief generation contract embedded into the downloadable Skill.

    The Skill is packaged at request time, so keeping this guide derived from the
    same prompt constants makes downstream Skill instructions follow future prompt
    changes without manually editing the template.
    """
    category_lines = "\n".join(
        f"- `{content_type}` → {label}"
        for content_type, label in sorted(CATEGORY_LABELS.items())
    )
    return f"""## Shared daily brief generation style

This Skill must follow the same editorial contract as Dorami's built-in daily brief generator. The live backend prompt source is `src/llm/prompts.py`; this section is generated from that module when the Skill zip is downloaded.

### Category mapping

{category_lines}
- anything else → {DEFAULT_CATEGORY_LABEL}

### Map-stage editorial standard

When summarizing individual articles, follow this exact backend system prompt:

```text
{MAP_SYSTEM_PROMPT}
```

### Reduce-stage Markdown style

When assembling the final report, follow this exact backend system prompt:

```text
{REDUCE_SYSTEM_PROMPT}
```
"""


# ==========================================
# 高级目标：URL → LLM 生成抓取节点配置
# ==========================================

# 治理字段的受控取值（与现有 fetcher/source-config 取值对齐，约束 LLM 输出）。
SOURCE_CONFIG_CATEGORIES = ["official", "official_web", "media", "community", "paper", "blog"]
SOURCE_CONFIG_SCOPES = [
    "company", "model_family", "product_family", "api_platform",
    "open_model_family", "developer_tool", "tech_media", "research_lab",
]
SOURCE_CONFIG_CHANNELS = ["newsroom", "blog", "changelog", "release_notes", "website_category", "docs", "community"]
SOURCE_CONFIG_TIERS = ["tier0_primary", "tier1_curated", "tier2_aggregator"]
SOURCE_CONFIG_CONTENT_TAGS = [
    "model_release", "product_update", "api_platform", "research_paper",
    "developer_tool", "market_news", "safety_policy", "tutorial_or_practice",
]
SOURCE_CONFIG_SIGNALS = ["high_signal", "medium_signal", "low_signal"]
SOURCE_CONFIG_NOISE = ["low_noise", "medium_noise", "high_noise"]


SOURCE_CONFIG_SYSTEM_PROMPT = """你是哆啦美·归档中枢的数据源接入工程师。给你一个网页列表页（文章/博客/新闻列表）的结构信号，请推断一份用于通用网页抓取器（generic_web）的抓取配置，并输出**纯 JSON 对象**（不要任何解释文字、不要代码围栏）。

判断要点：
- article_url_patterns：从给定候选链接里归纳出「文章详情页 URL 的稳定子串」（如 "/news/"、"/blog/"、"example.com/20"），用于把详情页和导航/分类/分页链接区分开。可给多个，命中任一即视为文章。
- exclude_url_patterns：需要排除的噪声链接子串（如 "/category/"、"/tag/"、"#"、分页 "/page/"）。
- listing_css：仅当启发式锚点不可靠（如列表项结构特殊）时给出 {item,url,title,date,summary} CSS 选择器；否则留空对象 {} 让抓取器走通用启发式。
- 治理字段从给定枚举里择优选择，拿不准就留空字符串或合理缺省。

只依据给定信号推断，不要臆造站点不存在的栏目。"""


def build_source_config_user_prompt(signals: Dict[str, Any]) -> str:
    """构造「URL → 抓取配置」的 LLM 输入。signals 由 source_builder.collect_html_signals 产出。"""
    sample_links = signals.get("sample_links", [])[:25]
    link_lines = "\n".join(
        f"- {item.get('url', '')}  |  {(item.get('title') or '')[:60]}"
        for item in sample_links
    ) or "（无候选链接）"
    sample_item_html = (signals.get("sample_item_html") or "")[:1500]

    return (
        "【页面信号】\n"
        f"URL：{signals.get('url', '')}\n"
        f"域名：{signals.get('domain', '')}\n"
        f"页面标题：{signals.get('page_title', '')}\n"
        f"站点名(og:site_name)：{signals.get('site_name', '')}\n"
        f"描述：{(signals.get('description') or '')[:200]}\n"
        f"语言：{signals.get('lang', '')}\n"
        f"启发式推断的 URL 模式候选：{signals.get('pattern_candidates', [])}\n"
        f"候选文章链接（最多 25 条）：\n{link_lines}\n"
        f"\n条目容器 HTML 样例：\n{sample_item_html}\n"
        "\n【受控取值】\n"
        f"category ∈ {SOURCE_CONFIG_CATEGORIES}\n"
        f"source_scope ∈ {SOURCE_CONFIG_SCOPES}\n"
        f"source_channel ∈ {SOURCE_CONFIG_CHANNELS}\n"
        f"provenance_tier ∈ {SOURCE_CONFIG_TIERS}\n"
        f"content_tags ⊆ {SOURCE_CONFIG_CONTENT_TAGS}\n"
        f"signal_strength ∈ {SOURCE_CONFIG_SIGNALS}\n"
        f"noise_risk ∈ {SOURCE_CONFIG_NOISE}\n"
        "\n【输出 JSON 结构】\n"
        "{\n"
        '  "name": "节点展示名",\n'
        '  "site_name": "站点名",\n'
        '  "category": "official_web",\n'
        '  "description": "一句话说明该源抓什么",\n'
        '  "article_url_patterns": ["/news/"],\n'
        '  "exclude_url_patterns": ["/category/"],\n'
        '  "listing_css": {},\n'
        '  "source_owner": "如 anthropic/openai/空",\n'
        '  "source_brand": "",\n'
        '  "source_scope": "company",\n'
        '  "source_channel": "newsroom",\n'
        '  "provenance_tier": "tier0_primary",\n'
        '  "content_tags": ["product_update"],\n'
        '  "signal_strength": "high_signal",\n'
        '  "noise_risk": "low_noise"\n'
        "}"
    )


DETAIL_PROFILE_SYSTEM_PROMPT = """你是网页正文抽取专家。给你一篇文章详情页的 HTML（可能已被截断），请推断用于 crawl4ai 的正文抓取 Profile，并输出**纯 JSON 对象**（无解释、无代码围栏）。

判断要点：
- target_elements：能精确圈定正文主体的 CSS 选择器（如 "article"、".post-content"、".entry-content article"）。优先最贴正文的容器，避免选到 main/body 这种过宽的；可给 1~3 个备选。
- excluded_selector：正文容器内仍需剔除的噪声（相关推荐、分享、作者卡、订阅、评论等），逗号分隔的 CSS。
- wait_for：若正文明显由 JS 渲染（初始 HTML 缺正文），给 "css:正文选择器" 或留空。
- use_browser：该页是否需要浏览器渲染（JS 重/反爬）才能拿到正文，给 true/false。

只依据给定 HTML 推断。"""


def build_detail_profile_user_prompt(sample_html: str, *, max_chars: int = 6000) -> str:
    clipped = (sample_html or "").strip()
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars] + "\n...(HTML 已截断)"
    return (
        "【文章详情页 HTML】\n"
        f"{clipped or '（空）'}\n"
        "\n【输出 JSON 结构】\n"
        "{\n"
        '  "use_browser": false,\n'
        '  "target_elements": ["article", ".post-content"],\n'
        '  "excluded_selector": ".related, .share, .comments",\n'
        '  "wait_for": ""\n'
        "}"
    )
