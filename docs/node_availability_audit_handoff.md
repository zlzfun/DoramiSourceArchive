# Node Availability Audit Handoff

## Background

This branch is for a source-by-source availability and quality audit of fetcher nodes. The trigger was a full-fetch run where all nodes used default parameters, then the resulting ledger was compared against the visible source pages.

The audit is not just "does the request succeed". The intended standard is:

- the fetched records should match the page's primary chronological content, not navigation, hero links, sidebars, popularity widgets, or footer links;
- records should preserve the original publication or release date when the source exposes one;
- source ordering should be newest-first in the knowledge ledger;
- source granularity should match how the source presents updates, for example one release/model entry per article when a release-note page is structured that way;
- stale or mis-scoped sources should be removed from the default catalog rather than patched into misleading coverage.

Current branch:

```text
codex/node-availability-audit
```

The full-fetch run used for initial comparison was:

```text
collection_job_runs.id = 133
name = 临时抓取采集范围: 全量抓取
node_count = 30
fetched = 275
saved = 275
failed = 0
child fetch_runs.id = 152..181
```

Existing records in `data/cms_data.db` are not automatically cleaned when a fetcher is fixed. For polluted old records, either rerun the source and dedupe/replace through the normal ingest path, or perform a deliberate cleanup migration.

## Source Quality Decisions

### QbitAI

Source id:

```text
web_qbitai
```

Problem observed:

- The generic scanner pulled links from the home page hero area and right-side "热门文章".
- As a result, the 20-item ledger result was not a clean chronological article list and included older popularity-widget articles.

Decision:

- Use the cleaner QbitAI category URL rather than the home page:

```text
https://www.qbitai.com/category/%E8%B5%84%E8%AE%AF
```

- Parse only the main article list:

```text
.article_list > .picture_text
```

Implemented in:

```text
src/fetchers/impl/curated_core_fetcher.py
```

The fetcher now:

- accepts only URLs shaped like `YYYY/MM/id.html`;
- parses QbitAI relative dates such as `33分钟前`, `4小时前`, `昨天 12:44`, and `前天 17:01`;
- sorts entries newest-first using parsed publish time;
- marks records with `raw_data.listing_source = qbitai_main_article_list`;
- excludes navigation, hero links, and "热门文章".

Regression test:

```text
tests/test_webpage_fetcher.py::test_qbitai_fetcher_uses_main_article_list_only
```

### Global Ledger Ordering

Problem observed:

- The knowledge ledger displayed current full-fetch results in time-ascending order from top to bottom.
- The desired behavior is newest-first.

Decision:

- This should be enforced at read/query time in the API layer, not by mutating every fetcher's emitted order.
- Fetchers should still emit clean chronological data when possible, but the ledger should have a stable global ordering rule.

Implemented in:

```text
src/api/app.py
```

Current default ordering:

```text
ArticleRecord.publish_date.desc()
ArticleRecord.fetched_date.desc()
ArticleRecord.id.desc()
```

For subscribed-priority views, subscribed-first ordering is preserved, then the same recency ordering is applied.

Regression coverage:

```text
tests/test_subscriptions.py
tests/test_runtime_role.py
```

### Z.ai / Zhipu New Released

Source id:

```text
docs_zai_new_released
```

Problem observed:

- The Z.ai "New Released" page is a single release-note page.
- Generic single-page extraction produced one long article containing all releases.
- The visible page is structured by release date and model, so each model release should be a separate ledger record.

Decision:

- Treat each `div.update.update-container` release block as one record.

Implemented in:

```text
src/fetchers/impl/curated_core_fetcher.py
```

The fetcher now:

- parses release blocks by date id, for example `2026-04-07`;
- uses the model name in the block, for example `GLM-5.1`;
- emits titles like `Z.ai New Released: GLM-5.1`;
- uses source URLs with anchors, for example:

```text
https://docs.z.ai/release-notes/new-released#2026-04-07
```

- sets `publish_date` to the release date at UTC midnight;
- marks records with `raw_data.listing_source = zai_new_released_updates`.

Regression test:

```text
tests/test_webpage_fetcher.py::test_zai_new_released_fetcher_splits_updates_by_model
```

Live verification returned 16 release records, from `GLM-5.1` on `2026-04-07` through `CogVideoX-3` on `2025-07-15`.

### NewZhiYuan / Aiera

Source id:

```text
web_aiera
```

Problems observed:

- The ledger did not preserve the original publication date; records fell back to fetch time.
- The generic scanner included the right-side "爆款文章" block, including very old 2015 articles.
- The home page shows only 10 records; requesting `limit=20` still returned only 10 before pagination support.

Decision:

- Parse only the main WordPress card list and follow pagination when the requested limit requires more than the first page.

Implemented in:

```text
src/fetchers/impl/curated_core_fetcher.py
```

The fetcher now:

- parses only:

```text
main#main .entries > article.entry-card
```

- excludes sidebar query blocks such as "爆款文章";
- extracts `time[datetime]`, with Chinese date and URL-date fallbacks;
- follows:

```text
nav.ct-pagination a.next[rel='next']
```

- deduplicates records across pages by normalized URL;
- sorts newest-first and truncates to `limit`;
- marks records with `raw_data.listing_source = aiera_main_article_list`.

Regression test:

```text
tests/test_webpage_fetcher.py::test_aiera_fetcher_uses_main_article_list_dates_and_excludes_sidebar
```

Live verification:

- `limit=10` returned only main-list records with original `+08:00` publish times.
- `limit=20` returned 20 records across page 1 and page 2, with no 2015 sidebar records.

### Alibaba / Qwen

Related handoff:

```text
docs/alibaba_qwen_source_cleanup_handoff.md
```

That handoff has been read and should be treated as part of the current branch context.

Decision from that work:

- Remove Alibaba Cloud Model Studio `model-announcements` from default model-release coverage.
- The page is reachable but stale and region/platform oriented. It focuses on Beijing/Singapore Model Studio deployment availability, pricing, quota, and region rollout notes rather than current Qwen model-family releases.
- `web_qwen_blog` is now the primary Alibaba/Qwen model update source.

Current Qwen fetch endpoint:

```text
https://qwen.ai/api/v2/article/retrieval
```

Runtime parameters:

```text
type=qwen_ai
language=en-US
```

The old source id should not appear outside its handoff note:

```text
docs_alibaba_model_studio_announcements
```

Use this check after future edits:

```bash
rg "docs_alibaba_model_studio_announcements" src docs tests --glob '!docs/alibaba_qwen_source_cleanup_handoff.md'
```

Implemented by the other assistant in this branch:

- `QwenBlogWebFetcher` now reads the Qwen article retrieval API.
- `AlibabaModelStudioAnnouncementsFetcher` was removed.
- `docs_alibaba_model_studio_announcements` was removed from essential/default-visible catalog docs.
- `docs/source_candidates/alibaba_qwen_sources.md` now documents Qwen API coverage instead of the stale Model Studio page.

Regression coverage:

```text
tests/test_webpage_fetcher.py::test_qwen_blog_fetcher_uses_current_article_retrieval_api
tests/test_fetcher_curation.py
```

### Qwen Code GitHub Releases

Source id:

```text
github_qwen_code_releases
```

Problem observed:

- The fetcher itself works (manually verified), but the node's scope is questionable.
- Within the "Alibaba / Qwen" catalog group it was the only repo singled out, while sibling Qwen repos (e.g. Qwen-Agent, the broader QwenLM org) and Qwen/Model Studio surfaces were not covered — an asymmetric, arbitrary-looking selection.
- `web_qwen_blog` already covers Qwen code/agent/model releases at the brand level (high-signal, low-noise).
- The source-candidate doc itself gates it: `status: proposed`, `medium_signal` / `medium_noise`, with the note to "keep as proposed until Qwen Code is confirmed as a priority comparable to Codex, Claude Code, Cursor, or Antigravity." Promoting it into the essential default catalog jumped that gate.

Decision:

- Temporarily remove `github_qwen_code_releases` from the catalog by **deleting the fetcher class**, consistent with how `AlibabaModelStudioAnnouncementsFetcher` was removed earlier in this branch.
- This was chosen over hiding-but-keeping-the-class because the registry enforces an invariant (`tests/test_fetcher_curation.py::test_registry_contains_only_recommended_sources_plus_generic_capabilities`) that registered preset fetchers are exactly the essential set + generic capabilities — no "registered but default-hidden" presets. Hiding would have required weakening that guardrail and leaving dead code in `impl/`.
- "Temporary / reversible" is preserved by git history plus the candidate record, which stays at `status: proposed`. Restoring is re-adding the ~12-line `PresetGitHubReleasesFetcher` subclass and the id to `ESSENTIAL_FETCHER_IDS`.
- Do **not** broaden into other Qwen repos / Model Studio: the parking lot deliberately parks "QwenLM GitHub org" as too broad, and Model Studio announcements were just removed as stale.

Implemented in:

```text
src/fetchers/impl/github_release_fetcher.py   # deleted QwenCodeGitHubReleasesFetcher class
src/fetchers/registry.py                      # removed from ESSENTIAL_FETCHER_IDS
docs/source_catalog.md                        # removed from default catalog table
docs/source_curation_policy.md                # removed from default-visible source list
```

The candidate record in `docs/source_candidates/alibaba_qwen_sources.md` stays as `proposed`, which is now consistent with it not being in the catalog.

### Anthropic News

Source id:

```text
web_anthropic_news
```

Problems observed:

- Noise: the generic anchor scanner pulled non-article `/news/` links (nav, footer, "all news").
- Long titles: each row's `DATE` + `CATEGORY` + `TITLE` were concatenated into one title, because generic title extraction took the whole row/anchor text.
- Shallow date coverage: only reached `May 14, 2026`; older posts (behind the page's "See More" button) could not be fetched.

Root cause:

- The page is Next.js RSC streaming. The initial DOM only contains ~11 first-screen `<a>` anchors; the full article list (and the clean fields) live as structured objects in the `self.__next_f` escaped-JSON stream. Each article is a Sanity-style object: `{"_type":"post", "publishedOn":"2026-05-28T17:00:00.000Z", "slug":{"_type":"slug","current":"claude-opus-4-8"}, "title":..., "summary":..., "subjects":[{"label":"Product"...},{"label":"Announcements"...}]}`. Generic anchor scraping is therefore both noisy (it concatenates the visible `DATE CATEGORY TITLE` row text) and shallow (first screen only), and the base class's embedded-JSON extractor skipped these because the slug is a nested dict (`{"current": ...}`), not a string, and the date key (`publishedOn`) was not in its candidate list.

Decision:

- Parse the RSC `_type:"post"` objects directly for `web_anthropic_news` instead of scraping anchors.

Implemented in:

```text
src/fetchers/impl/webpage_fetcher.py
```

The fetcher (`AnthropicNewsWebFetcher`) now:

- overrides `_run`, reusing the base `_script_payloads` / `_json_values_from_text` / `_walk_json` helpers (which already decode the `__next_f` chunks via balanced-brace `raw_decode`) to collect every `_type == "post"` object — no fragile hand-written escape regex;
- builds the URL from `slug.current` via `_entry_url_from_slug`, takes the clean `title` and `summary` directly, and parses `publishedOn` (full ISO; `date` for the rarer `featuredGridLink`) into a UTC-normalized publish date so the ledger keeps the real date and string sorting is consistent;
- carries `subjects[].label` (e.g. `Product`, `Announcements`) into both `tags` and `raw_data.subjects`;
- sorts newest-first and dedupes by normalized URL;
- marks records with `raw_data.listing_source = anthropic_news_rsc`;
- reuses the base detail-fetch / URL-normalization / matching helpers, so the existing `default_fetch_detail = True` body extraction is preserved;
- falls back to generic anchor scraping if the RSC structure ever yields nothing (no silent failure).

Regression test:

```text
tests/test_webpage_fetcher.py::test_anthropic_news_parses_rsc_post_objects_only
```

Live verification (real `https://www.anthropic.com/news`):

- 232 unique posts parsed (vs. the old ~11 / `May 14` cap), strictly newest-first, from `2026-05-28` back to `2021-05-28`.
- Every record has a real publish date and a clean title (0 with a concatenated `DATE`/`CATEGORY` prefix); every URL is under `/news/`; nav/careers/footer anchors excluded.

### Claude Code Changelog

Source id:

```text
docs_claude_code_changelog
```

Problem observed (same shape as the Z.ai "New Released" case):

- The page is a single changelog page, and generic single-page extraction (`SinglePageDocumentFetcher`) produced one long article containing every release.
- The page is naturally segmented by version, with an explicit per-version date, so each version release should be a separate ledger record.

Root cause:

- The page is a Mintlify `<Update>`-component changelog. Each version block is `data-component-part="update-label"` (version, e.g. `2.1.158`) + `data-component-part="update-description"` (release date, e.g. `May 30, 2026`) + `data-component-part="update-content"` (the bullet list). The 604 version-like text nodes in the raw HTML are the sidebar "On this page" anchors; the real content lives under `#content-area`.

Decision:

- Treat each version block under `#content-area` as one record.

Implemented in:

```text
src/fetchers/impl/curated_core_fetcher.py
```

The fetcher (`ClaudeCodeChangelogFetcher`) now overrides `_run` to:

- parse each `update-label` / `update-description` / `update-content` block under `#content-area`;
- emit titles like `Claude Code 2.1.158`, keyed/deduped by version;
- parse `update-description` (`%B %d, %Y`) into the publish date at UTC midnight, so each version keeps its real release date;
- sort by `(publish_date, version)` descending (a module-level `_version_sort_key` orders same-day versions numerically);
- use source URLs with version anchors, e.g. `https://code.claude.com/docs/en/changelog#2.1.158`;
- mark records with `raw_data.listing_source = claude_code_changelog_updates` and `detail_extraction_method = mintlify_update_component`.

Regression test:

```text
tests/test_webpage_fetcher.py::test_claude_code_changelog_splits_releases_by_version
```

Live verification (real `https://code.claude.com/docs/en/changelog`):

- 302 unique version records (vs. the old single long article), each with its own date and bullet content, strictly newest-first from `2.1.158` on `2026-05-30` back to `0.2.21` on `2025-04-02`.

### Gemini API & Gemma Release Notes (Google devsite)

Source ids:

```text
docs_gemini_api_changelog
docs_gemma_release_notes
```

Problem observed (same shape as Z.ai / Claude Code):

- Both are single release-notes pages, and generic single-page extraction (`SinglePageDocumentFetcher`) produced one long article containing every dated release.
- Both pages are naturally segmented by date, so each date's release should be a separate ledger record.

Root cause:

- These are Google `devsite` pages. Inside the `devsite-content` container each release is an `<h2>` date heading (e.g. `May 28, 2026`, carrying an `id` anchor — `05-28-2026` on Gemini, the version name like `gemma-4-mtp` on Gemma), and the heading's following siblings (`ul`/`p`) are that date's content until the next `<h2>`. It's heading-based segmentation, not a wrapper container.

Decision:

- Add a reusable `DevsiteReleaseNotesFetcher` base (heading-based date splitter) and make both Google fetchers subclass it; treat each date `<h2>` section as one record.

Implemented in:

```text
src/fetchers/impl/curated_core_fetcher.py
```

The base (`DevsiteReleaseNotesFetcher`) now:

- walks `<h2>` headings under `devsite-content`, keeping only those that parse as a date (`Month DD, YYYY`, tolerating the one comma-less `December 13 2023` heading);
- collects each heading's following `ul`/`p`/`ol` siblings (until the next `<h2>`) as the section body, so non-date headings (e.g. "Related guides") and their content are excluded;
- titles records as `<site>: <date>` (e.g. `Gemini API: May 28, 2026`), sets `publish_date` to the heading date at UTC midnight, and anchors the URL with the heading's `id`;
- sorts newest-first and dedupes by anchor;
- marks records with a per-source `listing_source` (`gemini_api_changelog_updates` / `gemma_release_notes_updates`) and `detail_extraction_method = devsite_release_notes_heading`.

Regression tests:

```text
tests/test_webpage_fetcher.py::test_gemini_api_changelog_splits_releases_by_date_heading
tests/test_webpage_fetcher.py::test_gemma_release_notes_split_by_date_and_anchor_by_id
```

Live verification:

- Gemini API (`https://ai.google.dev/gemini-api/docs/changelog`): 121 dated records, strictly newest-first from `2026-05-28` back to `2023-12-13`.
- Gemma (`https://ai.google.dev/gemma/docs/releases`): 28 dated records, newest-first from `2026-04-16` back to `2024-02-21`, each anchored by its version `id`.

### OpenAI Codex & API Changelog

Source ids:

```text
docs_openai_codex_changelog
docs_openai_api_changelog
```

Problem observed (same shape as the other changelog nodes):

- Both are single changelog pages, and generic single-page extraction (`SinglePageDocumentFetcher`) produced one long article containing every release.
- Both are naturally segmented (Codex by release, OpenAI API by dated entry), so each release/entry should be a separate ledger record.

Root cause:

- The two pages are the same site (`developers.openai.com`) but use **two different DOM shapes**, so they need separate parsers (no shared base like the Google devsite case):
  - **Codex**: months are `<h2>` group headings, but each release is its own `<li data-product="codex">` container with a `<time>` (ISO date), a first heading (the release name, e.g. `Codex CLI 0.135.0`), and the body. IDs split into `codex-…` (app updates) and `github-release-…` (CLI releases).
  - **OpenAI API**: months are `<h3>` headings using full month names (`May, 2026`); each entry under a month is a `div.mt-5` with a left-column date badge using the **abbreviated** month (`Apr 24`), type/scope badges (`Update` / `Feature` + `v1/responses`, `chat-latest`, …), and a Markdown body. The entry date must be completed from the month heading's year — the original cut only resolved `May` because the abbreviation matched the full name; every other month parsed to an empty date.

Decision:

- Give each OpenAI changelog its own `_run` override: split Codex by `<li data-product>` and OpenAI API by `div.mt-5`.

Implemented in:

```text
src/fetchers/impl/curated_core_fetcher.py
```

`OpenAiCodexChangelogFetcher` now:

- emits one record per `<li data-product>` under `main`, titled `OpenAI Codex: <release name>`;
- takes the publish date from the item's `<time>` (ISO), strips the repeated date/title prefix from the body, anchors the URL with the item `id`, sorts newest-first, and dedupes by anchor;
- marks records with `listing_source = openai_codex_changelog_updates`, `detail_extraction_method = openai_changelog_list_item`.

`OpenAiApiChangelogFetcher` now:

- walks each month `<h3>` and emits one record per `div.mt-5` entry;
- parses the entry date from the abbreviated day badge completed with the month heading's year (the `_month_map` accepts both full and abbreviated month names);
- titles records `OpenAI API: <type> · <scopes>` from the badges, takes the Markdown body, sorts newest-first (stable within a day), and dedupes;
- marks records with `listing_source = openai_api_changelog_updates`, `detail_extraction_method = openai_changelog_month_entry`.

Regression tests:

```text
tests/test_webpage_fetcher.py::test_openai_codex_changelog_splits_releases_by_list_item
tests/test_webpage_fetcher.py::test_openai_api_changelog_splits_entries_with_month_year_dates
```

Live verification:

- Codex (`https://developers.openai.com/codex/changelog`): 83 release records, strictly newest-first from `2026-05-29` back to `2025-05-19`.
- OpenAI API (`https://developers.openai.com/api/docs/changelog`): 133 dated entries, strictly newest-first from `2026-05-29` back to `2023-10-06` (all years now resolve, not just May).

### Curation follow-up: hide the two API changelogs (2026-06-02)

After the API changelogs were made to split correctly, a content-value review concluded that two of them are **redundant rather than low-value** and were removed from the default catalog:

```text
docs_openai_api_changelog
docs_gemini_api_changelog
```

Rationale:

- Both are dense, operational, parameter-level changelogs. For each brand a higher-signal source already provides launch coverage: OpenAI via `rss_openai_news` + `docs_openai_codex_changelog`, Gemini via `rss_google_gemini_models`. So the standard is "this brand is already covered, the changelog is redundant" — not "API changelogs have no value" (other brands' changelogs such as `docs_zai_new_released` / `docs_deepseek_api_changelog` / `docs_xai_release_notes` are each their brand's coverage pillar and stay).
- `docs_gemma_release_notes` was deliberately **kept**: it is low-frequency but every entry is a real open-model release, and it is Google's only dedicated open-model source — hiding it would leave a coverage gap, not remove noise.

Implementation (same delete-the-class approach as `github_qwen_code_releases`, because the registry invariant forbids a "registered but hidden" preset):

- Deleted `OpenAiApiChangelogFetcher` and `GeminiApiChangelogFetcher` from `src/fetchers/impl/curated_core_fetcher.py` (the shared `DevsiteReleaseNotesFetcher` base is kept — Gemma still uses it).
- Removed both ids from `ESSENTIAL_FETCHER_IDS`.
- Synced `docs/source_catalog.md`, `docs/source_curation_policy.md`, and the two `docs/source_candidates/*` validation notes.
- Removed the two now-dead fetcher tests; repointed the curation metadata assertion to `docs_openai_codex_changelog`. The registry invariant tests still pass (registered set == essential set + generic capabilities).

Restore from git history if a dedicated API-changelog node is wanted again.

### Fetch performance: skip detail fetch for already-archived items

Problem: re-running a feed/listing node re-fetched the per-item detail page for
every entry before dedup ran, because dedup only happens in `DatabaseStorage.save`
(after the fetcher already yielded). On `rss_openai_news` a 20-item re-run with
only 4 new articles still hit the source ~16 extra times (each behind an
anti-bot challenge + retries).

Fix: add a pre-fetch dedup check. `DatabaseStorage.existing_content_flags(ids)`
returns `{id: has_content}` for already-stored ids (id + has_content columns
only). `DataPipeline` injects it onto the fetcher's optional `dedup_lookup`
hook before each run; `BaseFetcher` exposes it via `_lookup_existing_content_flags`.
The content id is computable before the detail request, so:

- RSS (`GenericRssFetcher`): batch-checks all entry ids up front.
- Webpage list fetchers (`BaseWebPageListFetcher` + Anthropic/IThome/Qwen
  overrides, Qbitai/Aiera): per-item check via `_should_skip_detail_fetch`.

Both only skip the detail request when the item is already stored **and** has
content, so empty-body rows are still backfilled (unchanged `save` semantics).
The hook defaults to `None`, so dedup failure or absence degrades to the prior
behavior. Only "list + per-item detail" sources benefit; single-page and
list-API sources (changelogs, GitHub releases, HF models) get no extra query.

## Verification Performed

Targeted tests for the node audit and related curation changes:

```bash
uv run pytest tests/test_rss_fetcher.py tests/test_subscriptions.py tests/test_runtime_role.py tests/test_webpage_fetcher.py tests/test_fetcher_curation.py
```

Latest result:

```text
55 passed
```

The Alibaba/Qwen handoff also reports:

```bash
uv run pytest tests/test_fetcher_curation.py tests/test_webpage_fetcher.py
```

Result:

```text
10 passed
```

It also reports a full-suite run with one unrelated failure:

```text
77 passed, 1 failed
tests/test_mcp.py::test_admin_auth_session_lifecycle
```

The failure was `admin/admin` returning `401`, likely unrelated to source cleanup and possibly tied to auth configuration or local state.

## Current Modified Files

At the time this handoff was written, the branch contains changes in:

```text
docs/alibaba_qwen_source_cleanup_handoff.md
docs/node_availability_audit_handoff.md
docs/source_candidates/alibaba_qwen_sources.md
docs/source_catalog.md
docs/source_curation_policy.md
src/api/app.py
src/fetchers/impl/curated_core_fetcher.py
src/fetchers/impl/webpage_fetcher.py
src/fetchers/registry.py
tests/test_fetcher_curation.py
tests/test_subscriptions.py
tests/test_webpage_fetcher.py
```

## Working Principles For The Next Nodes

For each source:

1. Compare the fetched ledger records to the visible source page or API payload.
2. Identify whether the node should represent primary chronological content, release-note blocks, docs pages, RSS items, or API records.
3. Avoid generic full-page link scanning when the page has a clear main list container.
4. Explicitly exclude sidebars, popularity lists, nav, footer, search widgets, and related-content blocks.
5. Preserve original publish/release date from the source. Use URL-derived dates only as a fallback.
6. Use source-specific parsing when the page's structure is stable enough.
7. Add a small fixture-based regression test for every parser correction.
8. Run targeted tests before moving to the next node.

## Suggested Next Steps

- Continue node-by-node audit using the same pattern: compare full-fetch result, inspect the actual source, then decide whether to fix, split, paginate, or remove.
- Re-run a full fetch after the current set of fixes to see which remaining sources still produce stale records, wrong ordering, missing dates, or sidebar pollution.
- Decide whether existing polluted records in `data/cms_data.db` should be cleaned with a one-off maintenance script or left to age out after refreshed fetches.
- Consider adding a general "main content only" guideline to source admission docs, but keep source-specific selectors in fetchers where page structure is known.
