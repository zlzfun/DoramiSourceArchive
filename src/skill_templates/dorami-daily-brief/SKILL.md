---
name: dorami-daily-brief
description: Generate an AI news daily briefing (AI资讯日报) from the Dorami Archive platform. Use this skill whenever the user asks to generate a daily brief, news summary, or 日报; wants to know what AI/tech articles were published today or recently; says things like 生成今天的日报, 最近的AI资讯, 给我看看今天有什么新内容, or asks for a roundup of recent content from the archive. Trigger even if the user doesn't say "daily brief" explicitly — any request to summarize, browse, or surface recent content from the Dorami platform should use this skill.
---

# 哆啦美·AI资讯日报 (Dorami AI Daily Brief)

Generate a structured Markdown daily briefing from articles in the Dorami Archive platform.

## Platform access

**Base URL**: `{BASE_URL}`

Use whichever access method is available, in order of preference:

**1. MCP tools** (if `dorami-archive` MCP Server is connected):

| Tool | Purpose |
|---|---|
| `list_sources()` | List all source IDs — call this first if you need to know what's available |
| `browse_articles(publish_date_start, publish_date_end, limit=100)` | Fetch articles by date range |
| `get_article(article_id)` | Get full body text for a specific article |

**2. REST API** (if MCP is not connected, or to supplement):

```
GET {BASE_URL}/api/dify/articles
  ?publish_date_start=YYYY-MM-DD
  &publish_date_end=YYYY-MM-DD
  &include_content=true
  &limit=200
  &content_types=arxiv,wechat_article   # optional, comma-separated
  &source_ids=wechat_jiqizhixin         # optional, comma-separated
```

## Steps

1. **Clarify the date range.** Default to today. Recognize natural expressions like 昨天, 最近3天, last week, 2025-01-15.
2. **Fetch articles.** Use `browse_articles` or the REST endpoint above. For large date ranges, paginate with `skip`.
3. **Deduplicate.** Drop any repeated article IDs. Keep articles with `has_content=false` — list their title and link, but skip the summary.
4. **Categorize.** Group articles by `content_type` (see table below).
5. **Summarize.** For articles with body text, write a 1–2 sentence summary focusing on the key finding or contribution. Don't pad.
6. **Output.** Produce the report in the format below.

## Categories

| content_type | Section label |
|---|---|
| `arxiv` | 📄 学术论文 |
| `tech_conference` | 🎤 技术大会 |
| `github_release` | 🔧 开源动态 |
| `wechat_article` | 📱 行业资讯 |
| `rss` | 🌐 资讯聚合 |
| `social_post` | 💬 社交动态 |
| anything else | 📌 其他资讯 |

Within each section, sort by `publish_date` descending (newest first). Omit sections that have no articles for the requested period.

## Output format

```
# 🤖 哆啦美 AI 资讯日报 · YYYY-MM-DD

> 共收录 N 条资讯，涵盖 M 个分类

---

## 📄 学术论文（N 篇）

### [Article title](source_url)
**来源**: source_name · YYYY-MM-DD
One or two sentence summary.

---

## 🔧 开源动态（N 条）

...

---

*由哆啦美·归档中枢生成*
```

## Options

Recognize these in the user's message and adjust accordingly:

| What the user says | What to do |
|---|---|
| 昨天 / 最近3天 / 2025-01-15 | Use that date range |
| 只要论文 / only arxiv / `--content-types arxiv` | Filter to those content types |
| 加点评 / with commentary / `--with-commentary` | After each summary, add a 1–2 sentence opinion prefixed with `> 💡 点评：` |
| 用英文 / in English / `--lang en` | Output the report in English |
| 简报版 / brief mode | List title + link only, no summaries |

Commentary is off by default — only add it when the user explicitly asks.

## Edge cases

- If no articles are found for the requested period, say so clearly. Don't generate an empty report.
- If a multi-day range is requested, merge into one report. Include the publish date on each article so the reader can tell when things appeared.
- When article count is high, prioritize quality summaries over total coverage. It's better to summarize 20 articles well than to list 60 titles with thin descriptions.
