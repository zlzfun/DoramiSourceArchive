# Node Audit Playbook

How to check whether a fetcher node is healthy — and how to fix it when it is
not. This distills the node-by-node availability audit so the same method can be
reused whenever a new node is added or an existing one degrades.

A node's job is to faithfully capture **a source's primary chronological
content**. Most node problems are not crashes — the fetch "succeeds" but the
records are wrong: stale, mis-scoped, undated, mis-ordered, polluted with
nav/sidebar junk, or collapsed into one blob. Eyeballing the UI rarely catches
these; you must compare the captured records against the live source.

## When to run it

- After adding a new node, before trusting it in the default catalog.
- When a node's content "looks off" (one giant article, missing dates, wrong order, HTML residue, suspicious counts).
- Periodically — third-party pages, APIs, and anti-bot defenses change under us, so a node that passed once can silently break (see 机器之心 in [node_catalog_and_risks.md](./node_catalog_and_risks.md)).

## How to inspect

Two complementary views. Use whichever is faster; use both when in doubt.

**1. Look at what's already archived** (fast, no network — the last fetch output):

```python
import sqlite3
con = sqlite3.connect("data/cms_data.db")
cur = con.cursor()
sid = "rss_hn_ai"
cur.execute("SELECT COUNT(*), MIN(publish_date), MAX(publish_date) FROM articles WHERE source_id=?", (sid,))
print(cur.fetchone())
cur.execute("SELECT title, publish_date, has_content, length(content), source_url "
            "FROM articles WHERE source_id=? ORDER BY publish_date DESC LIMIT 8", (sid,))
for row in cur.fetchall():
    print(row)
```

**2. Run the fetcher live** against the real source (catches breakage the
archived rows predate). Instantiate the class and drive `_run` directly:

```python
import asyncio, sys; sys.path.insert(0, "src")
import httpx
from fetchers.impl.webpage_fetcher import IThomeAiWebFetcher

async def main():
    f = IThomeAiWebFetcher()
    async with httpx.AsyncClient(timeout=40, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as c:
        async for it in f._run(c, limit=8, fetch_detail=True):
            print(it.publish_date[:10], "|", it.title[:60], "| len", len(it.content))

asyncio.run(main())
```

Then open the live source page / API payload in a browser and compare.

## What "healthy" looks like

Check the captured records against the source on every axis:

1. **Right content** — records match the page's *primary list / feed / release stream*, not its nav, hero banner, sidebar, popularity list, footer, or related-content block.
2. **Right granularity** — one record per source item (one release entry, one article, one paper), not the whole page collapsed into a single article, and not one item split into many.
3. **Real dates** — `publish_date` comes from the source's own timestamp. URL-derived or "now" dates are a fallback only; a wall of identical/empty dates is a red flag.
4. **Newest-first ordering** — the ledger is sorted by real publish/release time, descending.
5. **Clean body** — no raw HTML tags, no `Loading…`/placeholder text, no "related reading / hot / footer / ICP" boilerplate leaking into `content`.
6. **Plausible counts** — asking for `limit=20` and getting 5 usually means pagination or an over-tight filter (see Cursor pagination); getting 0 means the entry point is broken (see 机器之心 WAF).
7. **Relevance / signal** — the records are actually about the source's topic, not a noisy keyword-search firehose (see Hacker News points threshold).

## Common failure patterns (and the fix)

These recurred across the audit. Recognize the symptom, apply the known fix.

| Symptom | Pattern | Fix |
| --- | --- | --- |
| Whole page is one undated article | **Changelog mashing** — a `SinglePageDocumentFetcher` grabs the entire changelog/release-notes page as one record | Write a per-entry splitter: iterate the entry blocks (`<h2>` date headings, grid cards, `<article>` sections), emit one record each with its real date, sorted newest-first |
| Dates come out empty for "Dec 14"-style badges | **Abbreviated-month gap** — the month map only had full names | Add `jan`/`feb`/.../`dec` keys alongside the full names |
| 0 records, listing request returns an HTML shell | **WAF / Cloudflare / SPA wall** — `sitemap`/page is gated by an Aliyun `acw_sc__v2` challenge or a JS-rendered SPA that exposes no item URLs to httpx | If a reader proxy or browser path can't cheaply recover it, and the brand is already covered elsewhere, **remove the node** rather than maintain a fragile bypass |
| Body starts with `< img …>` / ends with 相关阅读·热门·footer | **Generic-extractor over-capture** — the shared `article_extractor` fell through to `article`/`main` and swallowed siblings | Override `_detail_for_url`/`_extract_*_detail` to scope to the precise body container and decompose known noise selectors (IThome / QbitAI precedent) |
| Stray `Loading…` line between title and body | **Render-snapshot placeholder** — Playwright captured an async-loading hint | Strip lines that are *exactly* the placeholder; never substring-match (would delete legitimate "Loading the model…" sentences) |
| Asked for N, got far fewer | **Single-page listing** — the newest items span multiple listing pages | Set `max_listing_pages` and implement `_next_listing_page_url()` to accumulate until `limit` |
| Feed full of 0-engagement / off-topic junk | **Noisy search source** — a keyword-search firehose (`hnrss ?q=AI`) | Add a quality gate the source supports natively (points/comments threshold) and/or treat it as a **discovery source** (keep title + link + metadata, drop unreliable external bodies) |
| GitHub releases dominated by betas | **Prerelease flood** | Set `default_include_prereleases = False` and fetch `per_page=100` so stable releases aren't starved |
| Empty `Description` on repo/model records | **Sparse upstream metadata** | Backfill from a secondary field (README excerpt, model card), dedup-gated so re-runs cost no extra quota |
| Nav/footer links captured as articles | **Link-scan over-reach** | Declare a precise list container and `exclude_url_patterns`; set `drop_empty_content=True` to discard bodyless nav entries |

## Content-quality proofing(v3.22.4 文章质量校对波沉淀)

推广前对 55 个源的全量抽查(每源 2–3 篇,查开头 800 字 + 中段 + 结尾 500 字)暴露的
问题几乎全部落在三个层,**修哪一层要先诊断清楚**:

| 层 | 典型症状 | 修在哪 |
| --- | --- | --- |
| ① markdown 转换(共享) | 链接/粗体前后空格丢失(`take shape inAbilene`)、`<code>` 空格塌陷、行内内容散块、零宽字符 | `article_extractor.py` —— 一处修全站受益,**不要在单个 fetcher 里绕** |
| ② 详情容器圈定(站点级) | 开头混入重复标题/日期/栏目/作者/CTA 按钮/Share,结尾混入相关推荐/订阅框/版权尾注,中段夹推荐卡 | CrawlProfile 的 `target_elements`/`excluded_selector`,或 fetcher 覆写 `_detail_for_url` 做 HTML 预清洗 |
| ③ 文本层清洗(站点级) | 站点固定文案(`(opens in a new window)`、`一键三连`、`View details`) | fetcher 内文本后处理;通用形态(sr-only/`<summary>`/播放器)已下沉到 ① |

### ① 共享转换层现在保证什么(新 fetcher 不要重复造)

`node_to_markdown` / `_inline_markdown`(v3.22.4 起)已内建:

- **行内空白保真**:嵌套 `<a>/<b>/<span>` 内侧的边界空格不丢(曾是全站性 bug,波及几乎所有 rss_*/web_* 源);
- `<strong>/<b>`→`**`、`<em>/<i>`→`*` 粗斜体标记;
- 跳过 sr-only 类视觉隐藏元素(`(opens in a new window)`)、`<summary>` 折叠开关、`<video>/<audio>` 子树与 Ghost `kg-*-player` 播放器控件;
- 剥标题自链锚(`[#]`/¶/§ permalink,Hugo/Docusaurus);解包 `google.com/url?q=` 跳转链接;
- 清零宽字符(ZWSP/WJ/BOM;孤立成行的 ZWJ 单清,组合 emoji 保留);
- 行内-only 容器整体按单段渲染(`<p>text <code>x</code></p>` 不再散成多块);根节点本身是 `p`/行内容器时同样成立;
- `json_ld_article_body` 保留 `\r\n\r\n` 段落结构(the_decoder 曾整篇塌成一段)。

**推论——新 fetcher 的铁律:凡有结构的正文一律走 `node_to_markdown`,禁用
`get_text(" ")` 拍平**(它既吞导航页脚又压平块级换行;qwen_blog/xAI 均是此病)。
列表项转单行用 `SinglePageDocumentFetcher._li_markdown`(保链接/码内空格,无强插分隔符)。

### ② 站点镶边(chrome)的三种成熟修法,按类名稳定性选

1. **类名有语义 → CrawlProfile `excluded_selector`**(anthropic/claude_blog/qbitai/aiera 先例):最declarative,优先用。
2. **类名是哈希/混淆(Tailwind、Next 混淆类)→ `keep_dominant_child_html()`**(article_extractor 提供;OpenAI News/DeepMind 先例):容器是「头部横幅 + 正文 + 尾部推荐」三段式时,保留文本最长的直接子块即可——标题/日期由页面 meta 单独提取,不靠头部横幅。
3. **两者都不稳 → 文本层截断兜底**(在已转出的 markdown 上找 `## Related posts`/`版权所有…` 等标记截断)。可与 1/2 叠加做双保险(claude_blog 先例)。

**教训:markdown 化会改变文本形态**——站点级正则要容忍转换产物,如 `<em>` 包裹的
版权行是 `*版权所有…违者必究。*`(qbitai 曾因正则没料到星号而漏截)。

### 新源上线前的正文抽查清单

跑 `f.fetch(limit=2)` 真实端到端(B 类含 crawl4ai 渲染),对每篇看
`content[:300]` 与 `content[-300:]`,核对:

- **开头**:无重复标题/日期行/栏目名/作者卡/CTA 按钮/Share;
- **结尾**:无相关推荐卡/订阅表单文案(含「订阅成功/失败」提示语!)/版权样板/页脚;
- **中段**(长文抽一篇全读):无夹在正文中间的推荐卡(anthropic 曾有水合重复副本);
- **链接**:前后空格正常、锚文本无辅助文案、无跳转包裹;**列表/码块**:分行正常、空格未塌;
- **样板残留**:grep 站点固定文案(`submitted by`、`opens in a new window`、播放器时间码等);
- 与《What "healthy" looks like》各轴一并核对。

已知豁免(不算抓取错):feed 自带的作者推广位(oneusefulthing 卖书、simonwillison
订阅提示、阮一峰刊头)属原文行文;数学博客的 `$$…$$`(阅读器不渲染 LaTeX,存档忠实)。

## When to remove a node instead of fixing it

Delete the node (delete the class, remove its id from `ESSENTIAL_FETCHER_IDS`, **and**
add its id to `DECOMMISSIONED_FETCHER_IDS`) when either trigger fires:

- **Structural unfitness** — it can't produce correct chronological records with a reasonable fetcher: a static catalog with no dates/chronology, the same blob every fetch, or an entry point now hard-gated (WAF/SPA) with no cheap recovery.
- **Redundancy** — the brand/topic is already covered by a higher-signal node, so the marginal node mostly duplicates.

Removing the class (not just hiding it) is the policy — see
[curation_policy.md](./curation_policy.md); the registry's invariant test forbids
"registered but hidden" presets. Precedents: `docs_xai_models`,
`web_bytedance_seed_models`, `web_jiqizhixin`.

Registering the id in `DECOMMISSIONED_FETCHER_IDS` matters because the reader-side
subscription catalog (`GET /api/reader/sources`) unions in every `source_id` that still
has archived rows. A removed node's historical archive would otherwise leak back as a
"fresh" subscribable source, leaving the 阅读器's source list out of sync with the slimmed
节点管理. The denylist keeps the two surfaces aligned; already-subscribed users still see
the node so they can unsubscribe.

## Fix discipline

1. Reproduce against the live source (or archived rows) before changing code.
2. Prefer **source-specific parsing** when the page structure is stable enough; keep those selectors in the fetcher, not in the shared extractor.
3. Add a **fixture-based regression test** for every parser correction — capture a representative HTML/JSON snapshot and assert the split/date/order/scope. Live network must not be required to run tests.
4. Run targeted tests (`tests/test_rss_fetcher.py`, `tests/test_webpage_fetcher.py`, `tests/test_github_release_fetcher.py`, …) before moving on.
5. Record any special adaptation and its stability risk in [node_catalog_and_risks.md](./node_catalog_and_risks.md).
