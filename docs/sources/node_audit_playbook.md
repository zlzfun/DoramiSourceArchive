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

## When to remove a node instead of fixing it

Delete the node (delete the class **and** its id from `ESSENTIAL_FETCHER_IDS`)
when either trigger fires:

- **Structural unfitness** — it can't produce correct chronological records with a reasonable fetcher: a static catalog with no dates/chronology, the same blob every fetch, or an entry point now hard-gated (WAF/SPA) with no cheap recovery.
- **Redundancy** — the brand/topic is already covered by a higher-signal node, so the marginal node mostly duplicates.

Removing the class (not just hiding it) is the policy — see
[curation_policy.md](./curation_policy.md); the registry's invariant test forbids
"registered but hidden" presets. Precedents: `docs_xai_models`,
`web_bytedance_seed_models`, `web_jiqizhixin`.

## Fix discipline

1. Reproduce against the live source (or archived rows) before changing code.
2. Prefer **source-specific parsing** when the page structure is stable enough; keep those selectors in the fetcher, not in the shared extractor.
3. Add a **fixture-based regression test** for every parser correction — capture a representative HTML/JSON snapshot and assert the split/date/order/scope. Live network must not be required to run tests.
4. Run targeted tests (`tests/test_rss_fetcher.py`, `tests/test_webpage_fetcher.py`, `tests/test_github_release_fetcher.py`, …) before moving on.
5. Record any special adaptation and its stability risk in [node_catalog_and_risks.md](./node_catalog_and_risks.md).
