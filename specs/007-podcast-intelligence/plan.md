# Implementation Plan: AI/科技播客专栏与中文导读

**Branch**: `feat/issue-7-podcast-intelligence`
**Spec**: [spec.md](./spec.md)
**Voice stack decision**: [voice-stack-decision.md](./voice-stack-decision.md)
**Issue**: [#7](https://github.com/zlzfun/DoramiSourceArchive/issues/7)
**Integration Worktree**: `/Users/frankzhang/workspace/dorami/.worktrees/dorami-issue-7`

## 1. Delivery Outcome

Issue #7 交付一条逐级增量的 Podcast 产品线：

```text
P0 已迁移基线
Podcast RSS metadata + 专用目录 + 桌面/移动原音频播放器
        ↓
P1 源/单集准入
AI/科技范围、SSRF/访问、重复、漂移、人工审核
        ↓
P2 播客领域化
节目/单集/权利/播放状态 + 官方 transcript/chapters + 摘要/评分/标签
        ↓
P3 高价值长播客
源语言 ASR/说话人分离 + 完整中文转录 + 证据化中文精华博客
        ↓
P4 中文音频导读
固定单声线 TTS + <=900s QA + 双播放器/撤权
```

## 2. Technical Context

| Area | Current/Decision |
|---|---|
| Backend | Python, FastAPI, SQLModel, SQLite/FTS5, Alembic |
| Frontend | React, Vite, Tailwind v4; desktop and mobile reader share `useReaderState` |
| Feed parsing | Reuse `feedparser` and the existing `generic_podcast_rss` fetcher |
| Source identity | `SourceConfigRecord` remains source configuration truth; Podcast profile/reviews are side tables |
| Episode compatibility | Existing `ArticleRecord(content_type=podcast_episode)` remains delivery-compatible; domain facts move to dedicated tables in P2 |
| Background work | Persisted jobs with lease, retry and idempotency; no process-memory-only correctness |
| LLM | Reuse OpenAI-compatible client behind provider-neutral adapters; structured JSON and prompt versioning |
| ASR | Publisher transcript first; managed diarization benchmark first, with `faster-whisper` + WhisperX/FunASR as self-hosted candidates |
| Translation | Source transcript and full Chinese transcript are separate evidence-linked artifacts; glossary/entity QA is mandatory |
| TTS | One fixed licensed Chinese narrator; semantic segment synthesis behind a provider-neutral adapter |
| Media | Original audio remains publisher enclosure; derived artifacts use content-addressed object/file storage |
| Rights | Default `link_only`; text/audio derivative permissions are independent and mandatory before publication |
| Observability | Per-stage latency, retry, tokens/minutes/chars, provider and actual/estimated cost |
| API compatibility | `/api/articles*` keeps the current additive top-level `podcast` projection |

No unresolved technical clarification blocks P1/P2. P3/P4 product decisions are documented in `spec.md` with safe defaults.

## 3. Repository and Branch Baseline

The dedicated branch was created from `origin/main` at `a263326` so it does not inherit the article-analysis/personal-brief feature branch.

The following Podcast-only work was migrated in dependency order:

1. Podcast RSS backend MVP (`9c93ca4`, equivalent to work-package commit `8fe4b94`; only one copy retained).
2. Podcast reader MVP and original design (`9460201`), manually separated from article-analysis UI context.
3. Podcast-only discovery scoping (`bcd92e9`).
4. Curated Podcast catalog (`040a5fa`).

Premature `3.45/3.46` version bumps and unrelated taxonomy/personal-brief files were deliberately excluded. Versioning is deferred until final integration with the then-current `main`.

## 4. Architecture Decisions

### 4.1 Gate expensive work from left to right

Every stage must be cheaper than the stage to its right:

```text
feed preview
→ source scope
→ episode metadata relevance
→ duration/rights/budget
→ publisher transcript
→ ASR
→ deep value score
→ digest blog
→ TTS
```

An item rejected on the left must not allocate work on the right. `duration > 1800` is only a candidate flag.

### 4.2 Keep four concepts independent

- `source_trust_tier`: who supplied the source and how much editorial confidence it has.
- `scope_relevance_score`: whether the source/episode belongs to Dorami's AI/technology remit.
- `quality_score`: intrinsic depth, density, novelty, evidence and actionability.
- `processing_priority`: internal scheduling value after demand, freshness and cost; never displayed as editorial truth.

### 4.3 Source admission is a central invariant

Source admission runs after safe preview and before activation/subscription/first fetch. It is rechecked at the common fetch execution boundary. `is_active` and `ai_analysis_enabled` retain their current meanings and are not reused as admission state.

Internal imports may set trust/provenance, but do not bypass topical scope or rights. Manual scope overrides require actor, rationale and expiry.

### 4.4 Separate structured artifacts

Source transcript, full Chinese transcript, card summary, chapters, Chinese digest blog and narration script are independently versioned artifacts. Translation preserves segment/time/speaker alignment instead of overwriting the source transcript. A narration script is generated for speech and is never the digest Markdown or complete transcript passed unchanged to TTS.

Important claims point to normalized transcript segments and time spans. External fact-checking may add an editorial note; it must not silently rewrite a guest opinion as objective fact.

### 4.5 Rights are data, not a comment

Source and episode rights are stored separately; episode deny overrides source allow. Transcript access, derivative text, derivative audio and public distribution are independent permissions. Revocation stops jobs, unpublishes artifacts and invalidates served asset URLs.

### 4.6 Provider-neutral, revision-locked processing

Transcript, digest and TTS keys incorporate input hash, provider/model revision, prompt/policy version and processing settings. Reprocessing creates a new version and supersedes the old one; it does not overwrite evidence in place.

## 5. Source Admission Policy

### Sampling

- Inspect the latest 12 entries, covering at most 180 days.
- Select the newest six and uniformly sample six older entries.
- Fewer than five valid entries results in `review_required`.
- Preview reads feed metadata/show notes and may fetch 2–3 publisher transcript documents only when classification is uncertain; it never downloads enclosure audio.

### Classification

Each sample receives `ai_core`, `tech_adjacent`, `non_tech` or `unknown`, with topics, evidence and confidence.

Initial configurable thresholds:

- `approved_ai`: effective sample >=8, AI ratio >=0.70, in-scope ratio >=0.85, confidence >=0.80.
- `approved_tech`: effective sample >=8, in-scope ratio >=0.85, confidence >=0.80.
- `approved_mixed`: in-scope ratio >=0.40 but below core thresholds, and manual approval is required for catalog visibility.
- `rejected_scope`: effective sample >=8, in-scope ratio <0.25, confidence >=0.85.
- otherwise `review_required`.

Technical/security failures use `blocked`, never `rejected_scope`.

### Drift review

- AI/technology core: 90 days or 20 new episodes.
- Mixed: 30 days or 10 new episodes.
- Rejected: 180 days or manual trigger.
- Immediate review when the latest five in-scope ratio drops below 40%, identity metadata changes materially, or three episodes fail the metadata relevance gate consecutively.

## 6. Analysis and Processing Policy

### Episode metadata pass

Use title, show notes, categories, source profile and existing controlled taxonomy to estimate relevance and preliminary value. This pass may hide obvious off-topic episodes, but cannot claim a deep content summary.

### Transcript pass

Prefer Podcasting 2.0 publisher transcript. Otherwise ASR runs only if duration, rights, metadata relevance/value, resource and budget gates pass.

Transcript QA validates monotonic timestamps, language confidence, speech coverage, abnormal repetition, unexplained gaps and empty text. Diarization and identity attribution are separate: speaker labels remain stable anonymous A/B/C unless publisher metadata or an editor provides reliable identity evidence.

Every publishable premium episode then receives a full Chinese transcript. Translation runs on bounded, overlapping speaker turns; it preserves source segment IDs, timestamps, speaker mapping, numbers, product/model names and uncertainty. A glossary/entity pass and sampled back-translation QA block publication when alignment or factual fidelity falls below threshold. The original-language normalized transcript remains the evidence source of truth.

### Value score

| Dimension | Weight |
|---|---:|
| Topic depth | 20 |
| Information density | 20 |
| Novelty/originality | 15 |
| Evidence/authority | 15 |
| Actionability | 15 |
| Structure/clarity | 10 |
| Suitability for audio digest | 5 |

High-value processing defaults to `scope_relevance_score >=75`, `quality_score >=80`, confidence >=0.80 and all hard gates passed. Promotion, repetition, clickbait, unsupported speculation and ASR risk apply explicit penalties.

### Digest generation

The source transcript is split on chapter/sentence/speaker boundaries into bounded chunks. Map emits claims, numbers, entities, uncertainty, ads/banter and evidence segment IDs. Reduce deduplicates and creates the Chinese digest. Verifier checks every atomic claim against cited source segments before publishing. The narration rewrite emits concise evidence-linked spoken segments for one Dorami narrator; it does not read the full translation or impersonate source speakers.

### TTS duration

- Target: 12–13.5 minutes; hard limit: 900 seconds.
- Character budget uses measured conservative speed for the selected voice.
- 901–960 seconds: remove repetition and synthesize again.
- Above 960 seconds: regenerate the narration script.
- Maximum two automatic rewrite/synthesis attempts; then human review.
- Minor speed normalization may be used only for presentation, not to hide an oversized script.
- Default synthesis is deterministic per semantic segment with one approved voice, controlled pauses, concatenation and loudness normalization. Failed pronunciation retries only the affected segment.

## 7. Delivery Phases

### P0 — Migrated baseline

Current branch already contains Podcast RSS metadata parsing, additive article projection, dedicated desktop/mobile Podcast container, original-audio player, source-only discovery mode and a curated source catalog.

Exit gate: existing tests, frontend build/lint and main-session browser E2E pass on the dedicated worktree.

### P1 — Source admission and episode relevance

Deliver profile/review schema, safe sampler, deterministic decision service, preview/import tokens, manual decisions, drift review, central fetch guard, catalog visibility and metadata episode filtering.

Exit gate: core/mixed/rejected/blocked/insufficient-sample cases pass API, migration and browser E2E; preview downloads zero audio; imported off-scope feeds cannot be subscribed or fetched.

### P2 — Podcast domain and summary experience

Deliver episode/rights/playback records, Podcasting 2.0 transcript/chapters/license ingestion, show/episode APIs, original/translated view, structured metadata summaries, preliminary/deep analysis labels and admin rights management.

Exit gate: publisher transcript path is evidence-linked and causes zero ASR calls; link-only items expose no derivative generation/publish action.

### P3 — ASR, full Chinese transcript and digest

Deliver processing/artifact/segment schema, lease/retry/idempotency/cost accounting, publisher-transcript-first source normalization, managed ASR/diarization, optional self-hosted benchmark adapters, full evidence-linked Chinese transcript, transcript/translation QA, final value scoring, map/reduce/verifier, Chinese digest blog and review/publish/takedown.

Exit gate: 1800/1801 boundary, publisher-transcript bypass, retries, rights/budget failure, bad transcript/translation and fabricated evidence all pass; every published premium result has a full Chinese transcript and every key claim has a valid source segment/time span.

### P4 — <=15-minute AI audio

Deliver a fixed-narrator voice registry, provider-neutral TTS adapter, semantic-segment synthesis, audio concatenation/normalization, ffprobe QA, controlled rewrite loop, digest audio serving, dual progress and rights revocation.

Exit gate: all golden outputs <=900s; narrator voice is stable; AI disclosure visible; no cloned/impersonated voice; range/seek/desktop/mobile and revocation tests pass.

## 8. Constitution and Repository Checks

| Gate | Result |
|---|---|
| Model change has Alembic migration | Required; one migration owner per phase |
| `create_all()` metadata equals migration head | Must pass `tests/test_migrations.py` |
| Frontend changes follow conventions/tokens | Required before UI work |
| Existing article/feed contracts remain compatible | Additive `podcast` projection only |
| Version single source of truth | No feature-branch version bump; final integrator updates all required files |
| `uv.lock` local mirror drift | Do not include; dependency changes require controlled export workflow |
| Secrets and provider credentials | Existing credential resolver only; never returned by API/logs |
| User source isolation | Private/custom feeds never leak into team catalog or public derivatives |
| Safety | All remote URLs reuse SSRF, redirect, size and media validation boundaries |
| Main-session acceptance | Required after each merged phase and final release candidate |

Post-design check: no justified repository-constitution violation is required by this plan.

## 9. Multi-Agent and Worktree Strategy

The Issue #7 branch/worktree is the only integration and acceptance worktree. Agents never share a feature worktree and never merge directly into the integration branch.

```text
WT-contract
     ↓
WT-schema (single model/migration owner)
     ├── WT-p1-backend ──→ WT-p1-frontend
     └── WT-p2-backend ──→ WT-p2-frontend
                                  ↓
                           WT-p3-pipeline ──→ WT-p3-admin
                                  ↓
                           WT-p4-tts ──→ WT-p4-player
                                  ↓
                    Issue #7 integration worktree
                                  ↓
                   main session E2E / review / sign-off
```

Merge order is contract → schema → backend → frontend → tests/E2E for each phase. `src/models/db.py`, `alembic/versions`, router registration, shared API clients and global styles have a single named owner at any moment.

All agent branches use `wp/issue-7-<scope>` and worktrees use `.worktrees/dorami-issue-7-<scope>`. Each handoff includes base commit, commits, changed files, migrations, commands run, known risks and screenshots/artifacts.

## 10. Rollout and Rollback

- Feature flags separate Podcast catalog, source admission enforcement, deep processing and TTS.
- P1 first runs in shadow mode against current curated feeds; editors compare decisions with a gold set before enforcement.
- Existing active Podcast sources are backfilled to `review_required`, then approved in batches; no silent off-topic grandfathering.
- P2 normalized tables use resumable backfill and compatibility reads.
- P3/P4 publish flags default off; generated artifacts remain private until explicit review.
- Rollback disables new scheduling and serving while preserving immutable audit/evidence. Original audio links and existing Podcast metadata continue to work.

## 11. Generated Design Artifacts

- [research.md](./research.md)
- [data-model.md](./data-model.md)
- [contracts/podcast-api.yaml](./contracts/podcast-api.yaml)
- [quickstart.md](./quickstart.md)
- [tasks.md](./tasks.md)
