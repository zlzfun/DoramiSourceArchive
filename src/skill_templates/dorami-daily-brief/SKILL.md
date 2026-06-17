---
name: dorami-daily-brief
description: Generate an AI news daily briefing (AI资讯日报) from the Dorami Archive platform. Use this skill whenever the user asks to generate a daily brief, news summary, or 日报; wants to know what AI/tech articles were published today or recently; says things like 生成今天的日报, 最近的AI资讯, 给我看看今天有什么新内容, or asks for a roundup of recent content from the archive. Trigger even if the user doesn't say "daily brief" explicitly — any request to summarize, browse, or surface recent content from the Dorami platform should use this skill.
---

# 哆啦美·AI资讯日报 (Dorami AI Daily Brief)

Generate a structured Markdown daily briefing from articles in the Dorami Archive platform. The result should match the built-in Dorami daily brief style.

## Platform access

**Base URL**: `{BASE_URL}`

Use whichever access method is available, in order of preference.

Access scope is the user's subscriptions when a `dfeed_` personal feed token or `dsub_` subscription token is provided. If a token is not available, ask the user for their Dorami access token instead of silently using unscoped data.

**1. MCP tools** (if `dorami-archive` MCP Server is connected):

| Tool | Purpose |
|---|---|
| `list_sources()` | List all source IDs — call this first if you need to know what's available |
| `browse_articles(publish_date_start, publish_date_end, limit=100, subscription_token=token)` | Fetch articles by date range within the user's subscription scope |
| `get_article(article_id, subscription_token=token)` | Get full body text for a specific article after subscription-scope validation |
| `search_articles(query, subscription_token=token)` | Search articles within the user's subscription scope when RAG is enabled |
| `get_rag_context(query, subscription_token=token)` | Build scoped RAG context when RAG is enabled |

**2. REST API** (if MCP is not connected, or to supplement). Prefer this endpoint for daily brief generation because it is token-authenticated and scoped to the user's active subscriptions:

```
GET {BASE_URL}/api/public/feed/articles
Authorization: Bearer <dfeed_token>

  ?publish_date_start=YYYY-MM-DD
  &publish_date_end=YYYY-MM-DD
  &include_content=true
  &limit=200
  &content_types=arxiv,wechat_article   # optional, comma-separated
  &source_ids=wechat_jiqizhixin         # optional, comma-separated
```

## Steps

1. **Clarify the date range.** Default to today. Recognize natural expressions like 昨天, 最近3天, last week, 2025-01-15.
2. **Fetch articles.** Use `browse_articles` with `subscription_token` or the scoped REST endpoint above. For large date ranges, paginate with `skip`.
3. **Deduplicate.** Drop any repeated article IDs. Keep articles with `has_content=false` — list their title and link, but skip the summary.
4. **Categorize.** Group articles by `content_type` (see table below).
5. **Summarize and comment.** For articles with body text, produce concise factual summary bullets and a short professional comment following the shared backend style below.
6. **Output.** Produce the report in the format below.

## Categories

| content_type | Section label |
|---|---|
| `arxiv` | 📄 学术论文 |
| `tech_conference` | 🎤 技术大会 |
| `github_release` | 🔧 开源动态 |
| `github_repository` | 🔧 开源动态 |
| `wechat_article` | 📱 行业资讯 |
| `ai_company_blog` | 📱 行业资讯 |
| `web_article` | 🌐 资讯聚合 |
| `rss_article` | 🌐 资讯聚合 |
| `social_post` | 💬 社交动态 |
| `ai_community` | 💬 社交动态 |
| `hf_model` | 🔧 开源动态 |
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
> 💡 点评：One or two sentence professional comment.

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
| 不要点评 / no commentary / `--no-commentary` | Omit the `> 💡 点评：` line |
| 用英文 / in English / `--lang en` | Output the report in English |
| 简报版 / brief mode | List title + link only, no summaries |

Commentary is on by default to match Dorami's built-in daily brief. Omit it only when the user explicitly asks.

{DAILY_BRIEF_STYLE_GUIDE}

## Edge cases

- If no articles are found for the requested period, say so clearly. Don't generate an empty report.
- If a multi-day range is requested, merge into one report. Include the publish date on each article so the reader can tell when things appeared.
- When article count is high, prioritize quality summaries over total coverage. It's better to summarize 20 articles well than to list 60 titles with thin descriptions.
