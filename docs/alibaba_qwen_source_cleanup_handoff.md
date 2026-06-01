# Alibaba / Qwen Source Cleanup Handoff

## Intent

This workstream removes Alibaba Cloud Model Studio `model-announcements` from the default source catalog and makes the existing Qwen official source responsible for Alibaba/Qwen model-release coverage.

The reason is source quality. The Model Studio announcements page is still reachable, but its visible model-update table is stale and region/platform oriented. It focuses on Model Studio deployment regions, pricing, quotas, and availability notes for Singapore and Beijing, while recent Qwen model launches such as Qwen3.7-Max are published through Qwen's own surfaces and the current Qwen article API.

## Coverage Decision

`web_qwen_blog` is the primary Alibaba/Qwen model update source.

It now reads Qwen's current public article retrieval API:

```text
https://qwen.ai/api/v2/article/retrieval
```

Runtime parameters:

```text
type=qwen_ai
language=en-US
```

This API backs the current Qwen Research/Blog surface and includes recent model-release records, including Qwen3.7. The returned article records contain:

- `title`
- `path`, used to build canonical article URLs such as `https://qwen.ai/blog?id=qwen3.7`
- `extra.date`
- `extra.tags`
- `extra.author`
- `content`, as article HTML

`github_qwen_code_releases` covered Qwen Code tooling releases, not core model-family announcements. It was later removed during the node availability audit (see `docs/node_availability_audit_handoff.md`): the `QwenCodeGitHubReleasesFetcher` class was deleted (same approach as the Model Studio removal), with the candidate record kept at `status: proposed` for possible future re-admission.

## Implemented Changes

- `src/fetchers/impl/webpage_fetcher.py`
  - Updated `QwenBlogWebFetcher.listing_url` to `https://qwen.ai/api/v2/article/retrieval`.
  - Updated `QwenBlogWebFetcher.source_url` to the same API URL so frontend `base_url` and the "来源入口" field show the actual fetch entrypoint.
  - Fetches with `params={"type": "qwen_ai", "language": "en-US"}`.
  - Parses `payload.data.articles`.
  - Uses `record.path` for canonical article links.
  - Reads metadata from `record.extra`.
  - Extracts article body from returned HTML when available.
  - Sets `fetch_reliability` to `stable_public_api`.

- `src/fetchers/impl/curated_core_fetcher.py`
  - Removed `AlibabaModelStudioAnnouncementsFetcher`.

- `src/fetchers/registry.py`
  - Removed `docs_alibaba_model_studio_announcements` from `ESSENTIAL_FETCHER_IDS`.

- `docs/source_catalog.md`
  - Removed `docs_alibaba_model_studio_announcements`.
  - Updated `web_qwen_blog` base URL to the Qwen article retrieval API.

- `docs/source_curation_policy.md`
  - Removed `docs_alibaba_model_studio_announcements` from the default-visible source list.

- `docs/source_candidates/alibaba_qwen_sources.md`
  - Removed the Model Studio announcements candidate block.
  - Updated Qwen source reliability and validation notes for the new article API.

- `tests/test_webpage_fetcher.py`
  - Added coverage proving `QwenBlogWebFetcher` parses a Qwen3.7-style API response into a `WebPageArticleContent` item with canonical `https://qwen.ai/blog?id=...` source URL and extracted content.

## Verification

Targeted tests passed:

```bash
uv run pytest tests/test_fetcher_curation.py tests/test_webpage_fetcher.py
```

Result:

```text
10 passed
```

Full test suite was also run:

```bash
uv run pytest
```

Result:

```text
77 passed, 1 failed
```

The failure is `tests/test_mcp.py::test_admin_auth_session_lifecycle`, where login with `admin/admin` returns `401`. That appears unrelated to the Alibaba/Qwen source changes and likely comes from existing auth configuration or dirty local state.

## Current State

The old source id should no longer appear outside this handoff note:

```text
docs_alibaba_model_studio_announcements
```

Use `rg "docs_alibaba_model_studio_announcements" src docs tests --glob '!docs/alibaba_qwen_source_cleanup_handoff.md'` to confirm after future edits.

The frontend source entry should now show the Qwen article retrieval API as `base_url`, because registry metadata computes `base_url` from `source_url` first.

## Follow-Up Options

- Add a lightweight live smoke test for `web_qwen_blog` gated behind network availability, if the project has a pattern for network tests.
- Consider a future Model Studio model-catalog source if API availability, pricing, or region-specific serving status becomes a requirement. That should be a separate source from Qwen model-release coverage, not the old announcements page.
- If frontend should show both "human landing page" and "actual fetch endpoint", add a separate metadata field instead of overloading `base_url`.
