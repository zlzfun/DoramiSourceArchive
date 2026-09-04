# Tasks: AI/科技播客专栏与中文精华

**Accepted Baseline**: `main@b5864d3`
**Design review**: [design-review-2026-09-03.md](../../artifacts/issue-7/design-review-2026-09-03.md)
**Strategy**: P0 → P0.5 stabilization → P1 admission/domain → P2 Bundle v2 → P3A publisher transcript → P3B ASR → P4 TTS
**Testing**: unit, API contract, migration, sync failure drills and main-session desktop/mobile browser E2E are required.

The previous 100-item draft mixed optional future work with critical-path work and treated Archive Sync v2 as rollout polish. This revision groups work into six independently testable packages. Each package receives one migration owner; implementation starts only after the open deployment/rights decisions in `decisions.md` are resolved for that package.

## P0 — Accepted baseline

- [x] T001 Record `main@b5864d3` as the accepted Podcast RSS/catalog/player baseline.
- [x] T002 Record focused backend tests in `artifacts/issue-7/p0-test-report.md`.
- [x] T003 Record frontend lint/build in `artifacts/issue-7/p0-frontend-report.md`.
- [x] T004 Record product, architecture, UI and cost findings in `artifacts/issue-7/design-review-2026-09-03.md`.
- [x] T004A Restore every Reader discovery entry, including the Podcast page entry, to the shared full source catalog; keep Podcast as a user-selectable shape filter rather than a forced scope.
- [x] T004B Expose shared Podcast `SourceConfigRecord` rows in the existing Node Management board with health, item count, feed/schedule metadata, enable/disable, manual fetch and reader visibility. Show every node's content shape explicitly (`article`/`bulletin`/`social`/`podcast`) and provide a shape filter; keep episode-level premium administration in T025A/T025B.
- [x] T004C Correct the current Reader vocabulary to “原节目 / 中文精华”, label show-notes-derived detail analysis as “简介初评”, and use the same presentation on desktop and mobile without claiming full-audio analysis.

## P0.5 — Fact and sync stabilization

Goal: stop presenting show-notes analysis as full-audio truth and make the current split deployment safe enough to extend.

- [ ] T005 Add `analysis_basis=show_notes` to current Podcast projection and display “简介初评 / 基于节目简介” on list/detail/mobile.
- [ ] T006 Retire the misleading compatibility field `processing_eligible`; expose descriptive `is_long_form` only where useful and keep it out of premium eligibility decisions.
- [x] T007 Hide or rename Podcast “阅读时长” as “简介阅读约 N 分钟”; keep original audio duration primary.
- [x] T008 Prevent currency strings such as `$350M ... $1B` from being parsed as display math in Podcast show notes.
- [ ] T009 Set a dark-theme root/body inherited text color, add a dark `::selection`, remove hard-coded dark text dependencies and add Chromium/WebKit/Firefox visual cases.
- [ ] T010 Make remote-sync scheduling and job status available in a real `reader` role without enabling collection/provider routes.
- [ ] T011 Replace offset progress with stable keyset progress and make any line/checksum/import error preserve the old checkpoint.
- [ ] T012 Require payload checksums, remove the production HTTP Secure-cookie bypass and move sync credentials to a service-token secret boundary.
- [ ] T013 Recover or terminally mark stale `queued/running` process-memory jobs after restart so they cannot block future sync forever.
- [ ] T014 Decide and test the internal original-audio policy: browser egress allowlist, licensed mirroring, or Chinese-digest-only.
- [ ] T015 Run P0.5 API/browser/failure-drill regression and save `artifacts/issue-7/p0-5-report.md`.

Exit gate: episodes from all 35 current Podcast sources show their correct analysis basis; the reported 20VC page has readable dark text and correct currency rendering; a corrupt sync item cannot be skipped permanently.

## P1 — Source admission and Podcast domain

Goal: separate source trust, episode relevance, processing authorization and final premium state before spending on audio.

- [ ] T016 Define stable enums/contracts for source admission, episode state, analysis basis, rights dimensions and errors.
- [ ] T017 Add `PodcastSourceProfileRecord` plus append-only source decision/audit records with Alembic migration.
- [ ] T018 Implement safe feed preview, canonical identity, bounded sample selection, duplicate detection and SSRF/redirect/size checks.
- [ ] T019 Implement deterministic source decisions, manual override/expiry and drift review; shadow-review existing 35 sources before enforcement.
- [ ] T020 Enforce approved admission at every activation, subscription and fetch boundary; mixed feeds get per-episode relevance filtering.
- [ ] T021 Add normalized `PodcastEpisodeRecord`, dimensioned `PodcastRightsRecord` and `PodcastPlaybackStateRecord` with resumable backfill.
- [ ] T022 Parse publisher transcript/chapters/license documents with bounded reads; never download enclosure audio during admission.
- [ ] T023 Implement `premium_candidate → processing_authorized → premium_ready`; metadata cannot directly assign `premium_ready`.
- [ ] T024 Reuse `ArticleAnalysisRecord` as the current Reader projection while storing basis, confidence, input hash and immutable analysis history separately.
- [ ] T025 Add admin source review/rights/budget UI and basis-aware desktop/mobile Reader states.
- [ ] T025A Add a dedicated Podcast collection-management page for source enablement, schedule, health, manual fetch, recent episodes and processing visibility.
- [ ] T025B Add an audited per-episode “生成精品播客” action that overrides AI selection only, while rights, input-safety and budget checks remain mandatory; expose a separate privileged budget override if later required.
- [ ] T026 Add gold fixtures and unit/API/migration/browser tests for core, mixed, off-scope, hostile and insufficient-sample feeds.

Exit gate: source trust never bypasses topic/rights checks, off-scope feeds are not fetchable, and metadata analysis is clearly distinct from transcript analysis.

## P2 — Artifact Store and Archive Bundle v2

Goal: establish the external-compute/internal-consume boundary before generating large or paid artifacts.

- [ ] T027 Add `PodcastProcessingRecord` with renewable lease, heartbeat and fencing plus `PodcastStageAttemptRecord` with provider task/cost state.
- [ ] T028 Add immutable `PodcastArtifactRecord`, `ArchiveChangeRecord` and internal `ArchiveSyncReceiptRecord` with Alembic migration.
- [ ] T029 Implement local and S3-compatible content-addressed storage with staging, hash/size/MIME validation and atomic publish pointers.
- [ ] T030 Write publish/unpublish/takedown and tombstone changes to the outbox in the same database transaction as publication.
- [ ] T031 Define and sign `archive-bundle-v2` manifests using monotonic `change_seq`; exclude credentials, leases, drafts and provider URLs.
- [ ] T032 Implement blob export/Range serving and internal download-to-staging with signature/hash/bytes/MIME verification.
- [ ] T033 Materialize source, episode, current analysis/tag projection, publication and tombstone in one internal transaction; advance checkpoint only after success.
- [ ] T034 Resolve derived media to a stable internal API and local CAS; never persist an external storage URI in the Reader projection.
- [ ] T035 Support HTTP pull and reviewed offline bundle import through the same verifier/importer.
- [ ] T036 Add reader-only deployment tests, corrupted/missing/reordered bundle drills, retry idempotency and takedown priority tests.

Exit gate: P3 is blocked until a complete external bundle can reach a reader-only environment atomically and a failed bundle leaves the old version/checkpoint intact.

## P3A — Publisher-transcript text vertical slice

Goal: validate the complete product and sync chain with the four current publisher transcript URLs and zero ASR cost.

- [ ] T037 Define source transcript, aligned Chinese transcript, evidence fact pack, digest article and narration-script JSON Schemas.
- [ ] T038 Fetch/normalize publisher transcripts; preserve source segment ID, time range, language and stable speaker labels.
- [ ] T039 Produce full Chinese transcripts: Chinese normalization is a no-op translation; English/mixed content is translated per aligned segment.
- [ ] T040 Add transcript QA for coverage, monotonic time, gaps, repetition, speaker mapping, entities, numbers and glossary terms.
- [ ] T041 Generate transcript-backed summary, tags, chapters, why-listen and final value dimensions; set `premium_ready` only after QA/thresholds pass.
- [ ] T042 Generate an evidence fact pack, Reader digest and separate structured narration script; verify every material claim against source segments.
- [ ] T043 Publish artifacts through Bundle v2 and materialize the current `ArticleAnalysisRecord`/tag projection internally.
- [ ] T044 Replace stacked audio with one player and page-level `原节目 / 中文精华` mode; preserve independent progress.
- [ ] T045 Implement chapter/cursor transcript loading, search, speaker/language filters, timestamp seek and shareable `mode/tab/t` URLs.
- [ ] T046 Add admin evidence review, publish/unpublish/takedown and version/provenance inspection.
- [ ] T047 Run the four-episode external-to-internal E2E; assert zero ASR calls and save `artifacts/issue-7/p3a-report.md`.

Exit gate: every published premium slice has a complete Chinese transcript and timestamp evidence, and an internal Reader can consume it without external provider access.

## P3B — ASR fallback and budget funnel

Goal: add paid transcription only for authorized candidates and reconcile every provider call safely.

- [ ] T048 Define provider-neutral async ASR interfaces, reconciliation rules, actual/estimated cost units and budget reservations.
- [ ] T049 Implement opening/middle/end sample extraction and 6–10 minute sample ASR for uncertain candidates; label it as candidate evidence only.
- [ ] T050 Implement Alibaba Paraformer-v2 batch ASR as the initial cost route and Tencent large-model ASR 2.0 as a low-confidence upgrade route.
- [ ] T051 Benchmark FunASR/SenseVoice for Chinese and faster-whisper/WhisperX for English against the bilingual gold set and documented licenses.
- [ ] T052 Add diarization/alignment, language routing, transcript normalization and the same P3A QA contract.
- [ ] T053 Handle request-sent/response-unknown provider states by querying provider task ID before retry; never blindly double-submit.
- [ ] T054 Enforce configurable monthly, source and episode budgets before reservation and before final provider submission; trial defaults are 1,500 CNY warning / 2,000 CNY hard cap, and ordinary page views cannot enqueue work.
- [ ] T055 Add throughput/backpressure metrics and size the queue/worker controls for elastic 100–500 hours/day input; paid processing volume follows the current budget and can expand without schema changes.
- [ ] T056 Run cost, 429/timeout/restart/fencing, duplicate delivery and budget-exhaustion drills; save `artifacts/issue-7/p3b-report.md`.

Exit gate: actual charges reconcile to attempts, no retry duplicates a paid task, and the monthly hard cap stops new processing without breaking previously published content.

## P4 — Precomputed Chinese digest audio

Goal: turn reviewed narration scripts into one clearly disclosed, licensed, cached Chinese audio artifact.

- [ ] T057 Obtain written contract answers for caching, repeated playback, target-audience distribution, AI marking, provider retention and voice rights.
- [ ] T058 Blind-test Tencent premium/large-model fixed voices and Alibaba CosyVoice candidates on identical scripts; select one licensed platform narrator.
- [ ] T059 Define provider-neutral TTS, voice registry and deterministic `script_hash + voice_id + model_version + settings` key.
- [ ] T060 Implement semantic-segment synthesis, affected-segment retry, pauses, concatenation, loudness normalization and artifact caching.
- [ ] T061 Enforce 12–13.5 minute target and 900-second hard limit with controlled script rewrite, at most two automatic attempts and human-review fallback.
- [ ] T062 Publish/cache audio once externally and synchronize it through Bundle v2; user playback never calls TTS.
- [ ] T063 Enable the Chinese-digest player in the existing page mode, with AI/original attribution, audio transcript and independent progress.
- [ ] T064 Add Range/seek, truncation/silence/loudness, voice misuse, commercial-rights, budget and revocation tests.
- [ ] T065 Measure text-mode adoption and evidence click-through before expanding TTS coverage; keep TTS independently disableable.
- [ ] T066 Save final costs, sample audio, ffprobe/QA, rights and E2E evidence in `artifacts/issue-7/sign-off.md`.

Exit gate: every published audio is licensed, cached, attributable, <=900 seconds and removable; TTS remains optional to the text product.

## Dependency graph

```text
P0 → P0.5 → P1 → P2 → P3A → P3B → P4
```

- P1 contract and non-schema UI fixtures may be prepared beside P0.5, but enforcement waits for P0.5 sync correctness.
- P3A must precede P3B so product/schema/sync bugs are not debugged while paying ASR costs.
- TTS voice/legal benchmarking may begin early, but no production TTS integration precedes reviewed narration artifacts and Bundle v2.
- Version bumps and release notes occur only after final integration acceptance.
