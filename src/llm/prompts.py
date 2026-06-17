"""日报生成提示词 (src/llm/prompts.py)

- MAP 阶段：对单篇文章概括 + 打重要性分（沿用原 Dify 概括 schema，新增 score）。
- REDUCE 阶段：把择优后的条目汇总成与 dorami-daily-brief Skill 风格一致的 Markdown，
  并注入近期日报上下文做语义/事件级去重。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


# content_type / classification → 日报分类标签（对齐 skill_templates/dorami-daily-brief/SKILL.md）
CATEGORY_LABELS: Dict[str, str] = {
    "arxiv": "📄 学术论文",
    "tech_conference": "🎤 技术大会",
    "github_release": "🔧 开源动态",
    "github_repository": "🔧 开源动态",
    "wechat_article": "📱 行业资讯",
    "ai_company_blog": "📱 行业资讯",
    "web_article": "🌐 资讯聚合",
    "rss_article": "🌐 资讯聚合",
    "social_post": "💬 社交动态",
    "ai_community": "💬 社交动态",
    "hf_model": "🔧 开源动态",
}
DEFAULT_CATEGORY_LABEL = "📌 其他资讯"

# 允许的中文分类集合（约束 MAP 阶段 classification 取值）
ALLOWED_CLASSIFICATIONS = ["产业资讯", "学术论文", "开源动态", "技术大会", "社交动态", "资讯聚合"]


MAP_SYSTEM_PROMPT = """你是一位极具洞察力的前沿 AI 架构师与行业分析师。请仔细阅读下方提供的单篇资讯内容，并严格基于该数据提炼高质量的中文深度简报。只依据正文事实，绝不臆造原文未出现的数字、结论或参数；正文信息不足时宁可少写也不要编造。

【核心任务要求】
 1. title_cn: 提取核心主旨。原标题为英文需信达雅地翻译；若无实际标题需精准提炼。务必具体（点出主体/产品/数字），不要用「某公司发布新模型」这类空泛标题。
 2. classification: 准确判断资讯类别，优先取「产业资讯」或「学术论文」之一；其它情况可取「开源动态」「技术大会」「社交动态」「资讯聚合」。
 3. source: 推断信息来源的准确中文/官方名称。若【指定来源】有值则优化并使用；为空则结合正文聪慧推断最标准的媒体或机构名称（如「机器之心」「Google AI」）。
 4. company: 提取最主要涉及的科技厂商（如 OpenAI、Microsoft、Google、Meta 等）。若无明显厂商，输出空字符串 ""。
 5. realm: 归纳所属的 AI 核心领域，如「基础大模型」「AI Agent」「具身智能」「多模态」「算力架构」等专业领域词汇。
 6. summary: 提取 1-3 条核心看点。严禁泛泛而谈！每一条必须严格使用「**核心概念/技术名**：具体实现细节」的格式（冒号前加粗）。冒号后要落到可验证的实质：机制、架构、关键数字（参数量/上下文长度/价格/榜单分数等）、与既有方案的差异。看点不足 3 条就只写 1-2 条，不要为凑数稀释。
 7. comment: 撰写 100-150 字的硬核专业点评，回答「这件事为什么重要」——指出工程创新点、对开发链路/成本结构的启发或商业与竞争格局冲击。要有判断与取舍，绝禁「值得关注」「未来可期」这类套话。
 8. tags: 生成 1-4 个精准的常规技术标签。
 9. score: 给出该资讯的重要性评分，0-10 的数字。评分锚点：9-10 = 行业级重大突破/范式转变；7-8 = 头部厂商重要发布或有明确新意的研究；4-6 = 常规更新、增量改进；0-3 = 边角消息、营销稿、信息量稀薄。综合技术突破度、行业影响力、时效性与稀缺性，保持区分度、不要一律给高分。

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
「近期日报」是最近几天已经发布过的内容，仅作为去重参考。若今天的某条内容与近期日报报道的是同一事件，则：是纯重复就省略；是有实质进展的后续，则只写增量并简短点出「（接前报）」。绝不要重复近期日报已充分覆盖的旧内容。

【分类与排序】
按下列分类组织，分类标题用对应 emoji 标签；每个分类内按发布日期倒序（新的在前）；没有内容的分类直接省略：
- 📄 学术论文
- 🎤 技术大会
- 🔧 开源动态
- 📱 行业资讯
- 🌐 资讯聚合
- 💬 社交动态
- 📌 其他资讯

【输出格式】严格遵循（每条都要有 标题 / 来源 / 总结 / 点评 四部分）：
# 🤖 哆啦美 AI 资讯日报 · {report_date}

> 共收录 N 条资讯，涵盖 M 个分类

---

## 📄 学术论文（N 篇）

### [标题](source_url)
**来源**: 来源名 · YYYY-MM-DD
核心总结：直接采用该条目的 summary 要点（保留「**核心概念**：细节」的加粗格式），可分行列出。
> 💡 点评：采用该条目的 comment（一句到两句的硬核判断，说清为什么重要）。

---

（其余分类同理）

---

*由哆啦美·归档中枢生成*

【其它规则】
- 只输出 Markdown 正文，不要任何额外解释或代码围栏。
- 每条都必须包含「总结」与「点评」两部分：总结来自条目的 summary，点评来自条目的 comment（以 `> 💡 点评：` 引用块呈现）；若某条目的 comment 为空则省略点评行，不要自行编造。
- 标题链接用条目给出的 source_url；缺失则只写标题文本。
- 忠实于给定条目，不要新增条目里没有的事实或数字。
- 控制篇幅，宁可少而精，不要为凑数硬写。
- 末尾「仅标题条目」（无正文）以一个「📎 其它收录」分类用无序列表列出「[标题](url)」即可，不写总结与点评。"""


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
