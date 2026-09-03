# Tasks: AI/科技播客专栏与中文导读

**Branch**: `feat/issue-7-podcast-intelligence`
**Strategy**: P0 baseline → P1 source admission → P2 domain/summary → P3 ASR/digest → P4 TTS
**Testing**: Required at unit, contract, migration and main-session browser E2E levels.

## Phase 1 — Setup and P0 Baseline

Goal: freeze the existing Podcast MVP in the dedicated branch and create shared evaluation fixtures.

- [ ] T001 Record the clean Issue #7 baseline commit range and excluded unrelated commits in `specs/007-podcast-intelligence/plan.md`
- [ ] T002 Run and record Podcast backend baseline tests in `artifacts/issue-7/p0-test-report.md`
- [ ] T003 [P] Run and record frontend lint/build results in `artifacts/issue-7/p0-frontend-report.md`
- [ ] T004 [P] Audit current Podcast desktop/mobile UX against repository conventions in `artifacts/issue-7/p0-ui-audit.md`
- [ ] T005 [P] Create license-safe feed admission fixtures and expected labels in `tests/fixtures/podcast_admission/`
- [ ] T006 [P] Create bilingual multi-speaker transcript/value/single-narrator-TTS golden fixture manifests in `tests/fixtures/podcast_processing/README.md`

## Phase 2 — Foundational Contracts and Ownership

Goal: freeze shared enums, API shapes, feature flags, remote-input policy and migration ownership before parallel implementation.

- [ ] T007 Define Podcast admission/rights/processing/artifact enums in `src/models/podcast_contracts.py`
- [ ] T008 Define configurable admission, value, duration and budget thresholds in `src/services/podcast_policy.py`
- [ ] T009 [P] Add Podcast feature flags and safe defaults to `src/config.py` and `config/config.example.ini`
- [ ] T010 [P] Extend stable Podcast error codes and redaction rules in `src/api/podcast_errors.py`
- [ ] T011 Reconcile the proposed OpenAPI contract with runtime router conventions in `specs/007-podcast-intelligence/contracts/podcast-api.yaml`
- [ ] T012 Assign the single model/Alembic owner and record phase-specific migration heads in `specs/007-podcast-intelligence/migration-ledger.md`

## Phase 3 — User Story 1: Source and Episode Admission (P1)

Story goal: off-scope Podcast sources never become subscribable/fetchable; mixed sources admit only in-scope episodes.

Independent test: AI core, technology core, mixed, off-topic, insufficient-sample and hostile fixtures produce the expected audited decision without downloading enclosure audio.

- [ ] T013 [US1] Add `PodcastSourceProfileRecord` and `PodcastSourceReviewRecord` to `src/models/db.py`
- [ ] T014 [US1] Add the P1 admission schema migration with indexes and legacy backfill in `alembic/versions/`
- [ ] T015 [P] [US1] Add model and migration drift tests for P1 records in `tests/test_podcast_admission_schema.py`
- [ ] T016 [P] [US1] Implement canonical feed identity, duplicate detection and bounded sample selection in `src/services/podcast_admission.py`
- [ ] T017 [P] [US1] Implement Podcast preview SSRF, redirect, response-size, XML and enclosure checks in `src/services/podcast_preview.py`
- [ ] T018 [US1] Implement structured sample classification and deterministic ratio/confidence decisions in `src/services/podcast_admission.py`
- [ ] T019 [US1] Implement append-only reviews, manual override, expiry and optimistic concurrency in `src/services/podcast_admission.py`
- [ ] T020 [US1] Implement signed preview-token and idempotent import flows in `src/api/routers/admin_podcasts.py`
- [ ] T021 [US1] Add admission preview/list/review/decision endpoints in `src/api/routers/admin_podcasts.py`
- [ ] T022 [US1] Enforce `is_active && admission_status=approved` at the common fetch execution boundary in `src/api/app.py`
- [ ] T023 [US1] Route custom Podcast source creation through admission before activation/subscription/first fetch in `src/api/routers/reader.py`
- [ ] T024 [US1] Route administrator Podcast source creation through admission in `src/api/routers/source_configs.py`
- [ ] T025 [P] [US1] Implement review-due and topic-drift scheduling in `src/services/podcast_admission_scheduler.py`
- [ ] T026 [P] [US1] Implement cheap per-episode metadata relevance filtering for approved mixed feeds in `src/services/podcast_episode_relevance.py`
- [ ] T027 [US1] Exclude pending/rejected/blocked sources and rejected episodes from Reader catalogs and delivery queries in `src/api/feed_service.py`
- [ ] T028 [P] [US1] Add admin admission preview/evidence/decision API calls in `frontend/src/api.js`
- [ ] T029 [US1] Add admin Podcast admission preview and decision UI in `frontend/src/components/PodcastSourceAdmissionPanel.jsx`
- [ ] T030 [US1] Integrate Podcast admission state into source creation/catalog UI in `frontend/src/components/CustomNodeBuilder.jsx` and `frontend/src/components/DiscoverPage.jsx`
- [ ] T031 [P] [US1] Add sampler/classifier/override/drift unit tests in `tests/test_podcast_admission.py`
- [ ] T032 [P] [US1] Add preview/import/fetch-guard contract and permission tests in `tests/test_podcast_admission_api.py`
- [ ] T033 [US1] Add main-session desktop/mobile admission browser scenarios in `tests/e2e/podcast_admission.spec.js`

## Phase 4 — User Story 2: Podcast Catalog, Domain and Playback (P2A)

Story goal: readers browse only admitted Podcast shows, inspect normalized episode facts and play original audio consistently on desktop/mobile.

Independent test: a source/episode fixture is idempotently normalized, remains compatible with `/api/articles`, and supports original audio/player state without derivative rights.

- [ ] T034 [US2] Add `PodcastEpisodeRecord`, `PodcastRightsRecord` and `PodcastPlaybackStateRecord` to `src/models/db.py`
- [ ] T035 [US2] Add the P2 domain schema migration and resumable Podcast extensions backfill in `alembic/versions/`
- [ ] T036 [P] [US2] Implement Podcast episode identity and normalized upsert service in `src/services/podcast_episodes.py`
- [ ] T037 [P] [US2] Implement Podcasting 2.0 transcript/chapters/license parsing with bounded remote reads in `src/services/podcast_publisher_assets.py`
- [ ] T038 [US2] Implement source/episode rights precedence, expiry and audit in `src/services/podcast_rights.py`
- [ ] T039 [US2] Add show/episode/transcript/audio/playback endpoints in `src/api/routers/podcasts.py`
- [ ] T040 [US2] Preserve the existing additive `/api/articles` Podcast projection using normalized-domain fallback in `src/api/articles_view.py`
- [ ] T041 [P] [US2] Add Podcast show/episode/playback API calls in `frontend/src/api.js`
- [ ] T042 [US2] Add show/episode, original/translated metadata and rights state to `frontend/src/components/PodcastAudioPanel.jsx`
- [ ] T043 [US2] Add original/digest-safe playback progress state to `frontend/src/hooks/useReaderState.js`
- [ ] T044 [US2] Align desktop and mobile Podcast details in `frontend/src/components/ReaderTab.jsx` and `frontend/src/components/mobile/MobileArticlePage.jsx`
- [ ] T045 [P] [US2] Add identity/backfill/rights/publisher-asset tests in `tests/test_podcast_domain.py`
- [ ] T046 [P] [US2] Add Range/seek/transcript-pagination/permission API tests in `tests/test_podcast_api.py`
- [ ] T047 [US2] Add main-session desktop/mobile playback and translation scenarios in `tests/e2e/podcast_playback.spec.js`

## Phase 5 — User Story 3: Summary, Value and Tags (P2B)

Story goal: readers can judge relevance/value quickly; editors can audit basis, confidence, dimensions and evidence.

Independent test: metadata-only and transcript-backed fixtures render different analysis-basis labels, separate relevance/value, controlled tags and timestamp-grounded chapters.

- [ ] T048 [P] [US3] Define Podcast analysis JSON schemas and prompt/scoring versions in `src/models/podcast_analysis_contracts.py`
- [ ] T049 [P] [US3] Define controlled Podcast format/audience/depth/value labels in `config/podcast-taxonomy-v1.json`
- [ ] T050 [US3] Implement metadata preliminary relevance/value/summary analysis in `src/services/podcast_analysis.py`
- [ ] T051 [US3] Implement transcript-backed value dimensions, tag evidence and confidence in `src/services/podcast_analysis.py`
- [ ] T052 [US3] Store model result, effective manual override and versioned evidence in `src/services/podcast_analysis.py`
- [ ] T053 [US3] Add card summary, takeaways, chapters, score basis and tags to `src/api/routers/podcasts.py`
- [ ] T054 [P] [US3] Add analysis presentation utilities in `frontend/src/utils/podcastAnalysis.js`
- [ ] T055 [US3] Add separate relevance/value, why-listen, takeaways and basis UI in `frontend/src/components/PodcastAudioPanel.jsx`
- [ ] T056 [P] [US3] Add scoring, tag-limit, evidence and manual-override unit tests in `tests/test_podcast_analysis.py`
- [ ] T057 [US3] Add gold-set Precision@K and entity/number evaluation in `scripts/evaluate_podcast_analysis.py`
- [ ] T058 [US3] Add main-session summary/value/tag scenarios in `tests/e2e/podcast_analysis.spec.js`

## Phase 6 — User Story 4: ASR, Full Chinese Transcript and Evidence-Grounded Digest (P3)

Story goal: eligible high-value long episodes produce a speaker-aligned full Chinese transcript and reviewable Chinese digest with claim-level original evidence.

Independent test: publisher-transcript and ASR paths converge on the same source segment contract, then produce a complete Chinese segment set preserving timestamps/speakers; invalid evidence/translation cannot be published and retries create no duplicate artifacts/cost.

- [ ] T059 [US4] Add `PodcastProcessingRecord`, `PodcastArtifactRecord`, `PodcastTranscriptSegmentRecord`, `PodcastNarrationSegmentRecord` and `PodcastClaimRecord` to `src/models/db.py`
- [ ] T060 [US4] Add the P3 processing/artifact/segment/claim migration in `alembic/versions/`
- [ ] T061 [P] [US4] Implement content-addressed Podcast artifact storage in `src/services/podcast_artifacts.py`
- [ ] T062 [P] [US4] Define provider-neutral ASR/LLM interfaces and revision metadata in `src/services/podcast_providers.py`
- [ ] T063 [US4] Implement persistent eligibility, lease, retry, idempotency and cost accounting in `src/services/podcast_processing.py`
- [ ] T064 [US4] Implement publisher-transcript-first routing and managed source-language ASR/diarization fallback in `src/services/podcast_transcription.py`
- [ ] T065 [P] [US4] Add feature-flagged faster-whisper + WhisperX benchmark adapter in `src/services/podcast_asr/faster_whisper_adapter.py`
- [ ] T066 [P] [US4] Add feature-flagged SenseVoice/FunASR benchmark adapter with license warning in `src/services/podcast_asr/sensevoice_adapter.py`
- [ ] T067 [US4] Implement timestamp/language/coverage/repetition/gap, diarization and speaker-identity QA in `src/services/podcast_transcript_qa.py`, plus segment-aligned complete Chinese translation, glossary/entity preservation and translation QA in `src/services/podcast_translation.py`
- [ ] T068 [US4] Implement chapter-aware map/reduce with claim, number, entity and evidence outputs in `src/services/podcast_digest.py`
- [ ] T069 [US4] Implement atomic claim verification and publish blocking in `src/services/podcast_claim_verifier.py`
- [ ] T070 [US4] Generate separate full Chinese transcript, Chinese digest blog and structured single-narrator script artifacts in `src/services/podcast_digest.py`
- [ ] T071 [US4] Add process/status/retry/publish/unpublish/takedown endpoints in `src/api/routers/admin_podcasts.py`
- [ ] T072 [P] [US4] Add processing/cost/review API calls in `frontend/src/api.js`
- [ ] T073 [US4] Add admin pipeline, evidence review and publish UI in `frontend/src/components/PodcastProcessingPanel.jsx`
- [ ] T074 [P] [US4] Add eligibility/lease/retry/idempotency/provider tests in `tests/test_podcast_processing.py`
- [ ] T075 [P] [US4] Add transcript/translation/diarization QA, map-reduce and fabricated-evidence tests in `tests/test_podcast_digest.py`
- [ ] T076 [US4] Add isolated real-provider smoke with explicit budget cap in `scripts/smoke_podcast_processing.py`
- [ ] T077 [US4] Add main-session long-podcast processing and takedown scenarios in `tests/e2e/podcast_digest.spec.js`

## Phase 7 — User Story 5: <=15-Minute Chinese AI Audio (P4)

Story goal: approved condensed narration scripts become clearly disclosed, licensed single-narrator Chinese audio with a measured 900-second hard limit.

Independent test: golden scripts exercise success, controlled rewrite, terminal review, bad-audio rejection, separate playback progress and rights revocation.

- [ ] T078 [P] [US5] Define the licensed fixed narrator registry, disclosure and provenance fields in `config/podcast-voices.json`
- [ ] T079 [P] [US5] Define provider-neutral TTS and audio QA interfaces in `src/services/podcast_tts/providers.py`
- [ ] T080 [US5] Implement managed single-voice semantic-segment synthesis, retry-safe chunking and deterministic pause/concatenation in `src/services/podcast_tts/cloud.py`
- [ ] T081 [US5] Implement FFmpeg normalization, silence/truncation checks and ffprobe duration measurement in `src/services/podcast_audio_qa.py`
- [ ] T082 [US5] Implement 901–960 reduction, >960 regeneration and two-attempt review fallback in `src/services/podcast_tts_pipeline.py`
- [ ] T083 [P] [US5] Add Azure, Alibaba, Tencent and licensed self-hosted single-voice candidates to the blind listening/cost benchmark in `scripts/benchmark_podcast_tts.py`
- [ ] T084 [US5] Implement short-lived digest audio serving, Range support and rights revocation in `src/api/routers/podcasts.py`
- [ ] T085 [P] [US5] Add AI disclosure and original/digest variant contracts to `frontend/src/utils/podcast.js`
- [ ] T086 [US5] Add original/digest selector and independent playback progress to `frontend/src/components/PodcastAudioPanel.jsx`
- [ ] T087 [P] [US5] Add fixed-voice mapping, segment order, TTS rewrite/duration/audio-QA/revocation tests in `tests/test_podcast_tts.py`
- [ ] T088 [US5] Add main-session desktop/mobile digest-audio scenarios in `tests/e2e/podcast_tts.spec.js`

## Phase 8 — Polish, Rollout and Final Integration

Goal: complete performance, accessibility, migration, security, cost and release validation in the Issue #7 main session.

- [ ] T089 [P] Run full backend Podcast, migration, permission, feed and reader-shape regression suites and record them in `artifacts/issue-7/final-backend-report.md`
- [ ] T090 [P] Run frontend lint/build/accessibility checks and record them in `artifacts/issue-7/final-frontend-report.md`
- [ ] T091 Run legacy and fresh database migration rehearsals and record schema/hash evidence in `artifacts/issue-7/migration-report.md`
- [ ] T092 Run main-session desktop and mobile browser E2E on isolated ports and save screenshots in `artifacts/issue-7/e2e/`
- [ ] T093 Run approved real-RSS smoke without production database mutation and save request/cost evidence in `artifacts/issue-7/rss-smoke-report.md`
- [ ] T094 Run rights-deny, budget-deny, provider-failure, input-change and takedown drills in `artifacts/issue-7/failure-drill-report.md`
- [ ] T095 Audit logs/API responses/artifacts for provider secrets, private storage URIs and transcript leakage in `artifacts/issue-7/security-review.md`
- [ ] T096 Rebase the Issue #7 integration branch onto the final target branch and resolve only reviewed Podcast conflicts in `specs/007-podcast-intelligence/integration-log.md`
- [ ] T097 Update active documentation and remove superseded Issue #7 design claims in `docs/README.md` and `docs/podcast-wave-plan.md`
- [ ] T098 Update `src/version.py`, `pyproject.toml`, controlled lock/export files and release notes only after final acceptance in `src/version.py`
- [ ] T099 Obtain product confirmation for derivative visibility and on-demand TTS; record the confirmed full-Chinese/single-narrator decisions in `specs/007-podcast-intelligence/decisions.md`
- [ ] T100 Complete main-session sign-off with commit range, tests, screenshots, sample audio, ffprobe report and residual risks in `artifacts/issue-7/sign-off.md`

## Dependencies

```text
Setup → Foundational
Foundational → US1
US1 → US2
US2 → US3
US2 + US3 → US4
US4 → US5
US1..US5 → Final Integration
```

- US2 domain work may start after P1 contracts/schema are stable, but reader visibility cannot ship before US1 enforcement.
- US3 metadata analysis can prototype beside US2, but transcript-based scoring depends on normalized transcript segments.
- US4 processing does not start until rights, source and episode eligibility contracts are integrated.
- US5 consumes only the reviewed `narration_script_zh` artifact from US4; it never summarizes the transcript independently.

## Worktree Allocation

| Work package | Branch/worktree | Owns | Must not own |
|---|---|---|---|
| Contract | `wp/issue-7-contract` | enums, OpenAPI, errors | migrations, UI |
| Schema | `wp/issue-7-schema` | `src/models/db.py`, Alembic | business UI |
| P1 backend | `wp/issue-7-admission` | preview/admission/relevance/API | global CSS, migrations after handoff |
| P1 frontend | `wp/issue-7-admission-ui` | admission UI/API client | backend policy |
| P2 backend | `wp/issue-7-domain` | episode/rights/publisher assets/API | TTS |
| P2 frontend | `wp/issue-7-podcast-ui` | show/episode/player/translation | schema |
| P3 pipeline | `wp/issue-7-digest` | jobs/ASR/QA/digest/evidence | TTS voice selection |
| P3 admin UI | `wp/issue-7-processing-ui` | processing review/cost UI | pipeline semantics |
| P4 TTS | `wp/issue-7-tts` | TTS/audio QA/artifacts | transcript summarization |
| E2E | `wp/issue-7-e2e` | fixtures/scenarios/reports | business fixes |

## Parallel Execution Examples

### US1

- After T013–T014 integrate, T016, T017, T025, T026, T028 and T031 can run in parallel.
- T018–T024 depend on the policy/contracts and converge before T027/T030/T033.

### US2/US3

- Publisher asset parsing, episode identity and frontend API scaffolding can run in parallel after the P2 schema.
- Metadata summary/taxonomy work can run beside playback UI, but transcript-backed analysis waits for normalized transcript segments.

### US4/US5

- Artifact storage, provider interfaces and benchmark adapters can run in parallel after the P3 schema.
- Claim verification follows normalized transcripts and map output.
- TTS provider/voice benchmarks can begin before P4 integration, but production TTS waits for the reviewed narration artifact contract.

## Suggested MVP

The first independently releasable increment is P0 + US1:

- Dedicated Podcast catalog and original playback baseline.
- Source admission before activation/subscription/fetch.
- Mixed-source episode filtering.
- Manual review/override and drift reassessment.

ASR, Chinese digest and TTS remain disabled until their later acceptance gates pass.
