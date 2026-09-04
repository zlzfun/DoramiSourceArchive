# Implementation Plan: AI/科技播客专栏与中文导读

**Accepted Baseline**: `main@b5864d3`
**Spec**: [spec.md](./spec.md)
**Voice stack decision**: [voice-stack-decision.md](./voice-stack-decision.md)
**Design review**: [design-review-2026-09-03.md](../../artifacts/issue-7/design-review-2026-09-03.md)
**Issue**: [#7](https://github.com/zlzfun/DoramiSourceArchive/issues/7)
**Next Worktree**: 产品与架构复核完成后，从届时最新 `main` 新建；不复用旧集成 worktree 假设

## 1. Delivery Outcome

Issue #7 交付一条逐级增量的 Podcast 产品线：

```text
P0 已迁移基线
Podcast RSS metadata + 专用目录 + 桌面/移动原音频播放器
        ↓
P0.5 事实与同步止血
简介初评标识 + Reader sync 修复 + 暗色/Markdown 校正
        ↓
P1 源/单集准入与领域模型
AI/科技范围、SSRF/访问、重复、漂移、人工审核
        ↓
P2 外网产物与内网同步底座
Artifact Store + Archive Bundle v2 + 内网 materialize
        ↓
P3A 零 ASR 文字闭环
发布者逐字稿 + 完整中文转录 + 深评/精华 + 内网双模式
        ↓
P3B ASR 回退
抽样筛选 + 批量 ASR/说话人 + QA/成本闸
        ↓
P4 中文音频导读
固定单声线 TTS + <=900s QA + 预生成同步/撤权
```

## 2. Technical Context

| Area | Current/Decision |
|---|---|
| Backend | Python, FastAPI, SQLModel, SQLite/FTS5, Alembic |
| Frontend | React, Vite, Tailwind v4; desktop and mobile reader share `useReaderState` |
| Feed parsing | Reuse `feedparser` and the existing `generic_podcast_rss` fetcher |
| Source identity | `SourceConfigRecord` remains source configuration truth; Podcast profile/reviews are side tables |
| Episode compatibility | Existing `ArticleRecord(content_type=podcast_episode)` remains delivery-compatible; domain facts move to dedicated tables in P2 |
| Background work | Current `JobRecord` is only a UI shell around process-memory tasks; Podcast workers add DB claim, renewable lease, heartbeat, fencing, provider task ID and stage attempts |
| LLM | Reuse OpenAI-compatible client behind provider-neutral adapters; structured JSON and prompt versioning |
| ASR | Publisher transcript first; managed diarization benchmark first, with `faster-whisper` + WhisperX/FunASR as self-hosted candidates |
| Translation | Source transcript and full Chinese transcript are separate evidence-linked artifacts; glossary/entity QA is mandatory |
| TTS | One fixed licensed Chinese narrator; semantic segment synthesis behind a provider-neutral adapter |
| Media | Original audio remains publisher enclosure; derived artifacts use content-addressed object/file storage |
| Rights | Default `link_only`; text/audio derivative permissions are independent and mandatory before publication |
| Observability | Per-stage latency, retry, tokens/minutes/chars, provider and actual/estimated cost |
| API compatibility | `/api/articles*` keeps the current additive top-level `podcast` projection |
| Deployment split | External collector performs all provider calls and publishes immutable bundles; internal sync/reader only validates, stores and serves local artifacts |

The processing order and trial budget are settled. The internal original-audio network policy and derivative-distribution rights remain explicit package gates in `decisions.md`; no implementation should silently choose them.

## 3. Repository and Branch Baseline

2026-09-03 重新核对后的事实基线：

1. Podcast P0 已作为 squash commit `b5864d3` 合入并推送到 `main` / `origin/main`，不再存在“待迁入独立 Issue #7 分支”的前置步骤。
2. P0 包含 Podcast RSS metadata、兼容文章投影、桌面/移动 Reader、Podcast-only 发现目录及 36 个策展条目（35 ready、1 blocked）。
3. 文章分析与个人早报已经先于 P0 合入 main；Podcast show notes 当前会进入通用 `ArticleAnalysisRecord`，所以后续元数据初评必须复用现有摘要/质量/标签能力，不再假设该系统缺席。
4. 本地验收数据中已有 35 个启用 Podcast 来源与 35 个单集；35 个均有原音频，30 个超过 30 分钟，4 个携带发布者 transcript 定位信息。
5. 版本升级仍推迟到最终发布集成；后续里程碑从届时最新 `main` 创建新工作分支和隔离 worktree。

P0 基线报告见 `artifacts/issue-7/p0-test-report.md` 与 `artifacts/issue-7/p0-frontend-report.md`。

## 4. Architecture Decisions

### 4.1 Gate expensive work from left to right

Every stage must be cheaper than the stage to its right:

```text
feed preview
→ source scope
→ episode metadata relevance/preliminary value
→ duplicate/rights/input/budget/editor authorization
→ publisher transcript
→ sample ASR when uncertain
→ full ASR when authorized
→ deep value score
→ digest blog
→ TTS
```

An item rejected on the left must not allocate work on the right. Duration is a cost/scheduling feature and optional recall signal; it never decides premium eligibility. `duration > 1800` may label an item as long-form, but shorter episodes can still be selected by value or an editor.

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

### 4.7 Precompute outside, consume inside

RSS collection, admission, ASR, translation, deep analysis, digest and TTS run only in the external collector environment. A publish transaction writes immutable artifacts plus an `ArchiveChangeRecord`. The internal sync agent pulls signed Bundle v2 manifests by monotonic `change_seq`, downloads blobs into staging, verifies signature/hash/bytes/MIME, atomically materializes the new local version and only then advances its checkpoint.

Archive Sync v1 is not sufficient: it only transports Article JSONL, does not transport analysis/tags/media, and offset/partial-import behavior cannot guarantee a lossless Podcast pipeline. Bundle v2, local CAS, tombstones and receipt/checkpoint records are prerequisites for P3, not rollout polish.

### 4.8 One page-level Podcast mode

The episode page has `original` and `chinese_digest` as its primary state. Switching mode changes the single player, default content and timeline together while preserving independent playback positions. Transcript/search/language/chapters are secondary controls. Long transcripts load by chapter and segment cursor; evidence links always resolve back to the source-language segment and original timeline.

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

Use title, show notes, categories, source profile and existing controlled taxonomy to estimate relevance and preliminary value. Existing Podcast analysis is exactly this path and must be presented as `analysis_basis=show_notes` / “简介初评”. This pass may hide obvious off-topic episodes, but cannot claim a deep content summary or a final premium decision.

Episode state is deliberately staged:

- `premium_candidate`: cheap metadata result only.
- `processing_authorized`: rights/input/budget plus high-confidence metadata or editor approval allow full processing.
- `premium_ready`: complete transcript QA and transcript-backed value analysis pass; only this state is presented as 精品.

### Transcript pass

Prefer Podcasting 2.0 publisher transcript. Otherwise ASR runs only if duration, rights, metadata relevance/value, resource and budget gates pass.

When a metadata-only candidate is uncertain and has no publisher transcript, transcribe 6–10 minutes sampled across opening/middle/end before authorizing full ASR. Sample analysis remains a candidate signal and is never displayed as a full summary.

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

### P0 — Accepted baseline

Podcast RSS metadata parsing, additive article projection, dedicated desktop/mobile container and original-audio player are already on `main@b5864d3`. That baseline also introduced a Podcast-only discovery branch; P0.5 restores every discovery entry to the shared full source catalog.

### P0.5 — Fact and sync stabilization

Label every current Podcast result as show-notes preliminary analysis; retire `processing_eligible` in favor of descriptive `is_long_form` without implying eligibility; restore the shared full “发现更多来源” catalog; correct the misleading show-notes reading time, Podcast dollar/math rendering and dark-theme inheritance. Fix real reader-role remote-sync scheduling/status access, make partial failures preserve the checkpoint, move to stable keyset cursors and recover stale in-memory jobs. Decide how an internal terminal reaches original enclosure audio.

Exit gate: current 35 episodes never present show-notes analysis as full-audio truth; Chromium/WebKit/Firefox dark snapshots pass; a failed sync line cannot advance the cursor; a reader-only deployment can operate and inspect sync.

### P1 — Admission and Podcast domain

Deliver source profile/decisions, safe preview, central fetch guard, shadow review of the existing 35 sources, normalized episode/rights/playback records, candidate/authorized state, Podcasting 2.0 transcript/chapters/license ingestion and basis-aware current analysis projection. Add a dedicated Podcast collection-management surface with source health/scheduling/manual fetch, episode processing state and an audited per-episode “生成精品播客” action.

Exit gate: off-scope feeds cannot be subscribed/fetched; existing approved sources are not abruptly removed; source trust cannot bypass topic/rights gates; show-notes and transcript analyses render different basis labels.

### P2 — Artifact Store and Archive Bundle v2

Deliver local/S3-compatible CAS, immutable artifact and stage-attempt records, publish outbox/change sequence, signed manifests, blob/tombstone export, internal staging/import/materialization, local media serving and offline-package parity.

Exit gate: a complete bundle switches versions atomically; any corrupt/missing entity or blob leaves the previous version and checkpoint intact; revoked artifacts disappear without exposing external storage/provider URIs.

### P3A — Publisher-transcript vertical slice

Use the four currently discovered publisher transcript URLs to produce normalized source transcripts, complete aligned Chinese transcripts, transcript-backed score/tags/summary, evidence fact packs, digest reading copy and narration scripts. Sync the published text artifacts through Bundle v2 and ship the page-level original/Chinese-digest experience plus chapter/cursor transcript reading.

Exit gate: the vertical slice makes zero ASR calls; every published premium item has full Chinese coverage and evidence timecodes; an internal reader can search, deep-link, switch modes and receive takedowns without internet provider access.

### P3B — ASR fallback and budget funnel

Add sampled candidate ASR, full batch ASR routing, diarization/alignment QA, renewable leases, heartbeat/fencing, provider-state reconciliation, actual cost ledger and managed/self-hosted benchmark adapters. Start with Alibaba Paraformer-v2, Tencent large-model ASR as an upgrade route, and FunASR/SenseVoice/faster-whisper as PoCs.

Exit gate: rights/budget failure creates no provider call; ambiguous calls do not double-charge; the configured monthly cap (trial: 1,500 CNY warning / 2,000 CNY hard stop) controls paid work without constraining future scale; `premium_ready` is assigned only after complete transcript deep analysis.

### P4 — Precomputed <=15-minute Chinese audio

Blind-test Tencent licensed fixed voices and Alibaba CosyVoice candidates, then deliver one approved narrator, semantic-segment synthesis, deterministic cache keys, audio concatenation/normalization, ffprobe QA, Bundle v2 media sync, single-player switching and revocation. Ordinary reader visits never initiate TTS.

Exit gate: all golden outputs <=900s; narrator voice and commercial rights are documented; AI disclosure is visible; range/seek/desktop/mobile and revocation tests pass; the monthly budget remains enforced.

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
