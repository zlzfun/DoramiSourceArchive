# Data Model: Podcast Intelligence

## 1. Principles

- Keep `SourceConfigRecord`, `SourceStateRecord` and `ArticleRecord` compatible.
- Store Podcast domain facts in normalized tables; use `extensions_json` only for lightweight API projection during migration.
- Store large transcripts/audio in an artifact store; database rows hold URI, hash, metadata and small inline text only.
- Do not overload one status column. Source admission, episode eligibility, processing stage and artifact publication are independent.
- Record provider/model revision, prompt/policy version and input fingerprints for reproducibility.
- Every schema change must include an Alembic migration and pass metadata-vs-head drift tests.

## 2. Existing Records Retained

### SourceConfigRecord

Remains the source configuration and fetch scheduling truth. `is_active` continues to mean operationally enabled. `ai_analysis_enabled` continues to mean AI processing permission. Neither means source admitted.

### ArticleRecord

Remains the common delivery record. Podcast episodes keep `content_type=podcast_episode`; current top-level `podcast` projection remains additive and stable.

## 3. P1 Source Admission Entities

### PodcastSourceProfileRecord

One current profile per Podcast source.

| Field | Type/Constraint | Meaning |
|---|---|---|
| `source_id` | PK/FK SourceConfig | Existing source identity |
| `canonical_feed_url` | required URL | Resolved canonical RSS URL |
| `podcast_guid` | optional string | Podcast Namespace GUID |
| `admission_status` | enum, indexed | `pending`, `sampling`, `review_required`, `approved`, `rejected_scope`, `blocked`, `failed` |
| `admission_policy` | enum | `ai_core`, `tech_core`, `mixed`, `manual` |
| `source_scope_class` | enum | `ai_core`, `tech_adjacent`, `mixed`, `off_topic`, `unknown` |
| `ai_episode_ratio` | decimal 0..1 | Weighted AI-core sample ratio |
| `in_scope_episode_ratio` | decimal 0..1 | AI + technology ratio |
| `scope_confidence` | decimal 0..1 | Aggregate classification confidence |
| `sample_size` | nonnegative integer | Effective valid samples |
| `hard_reject_reasons_json` | bounded JSON | Security/access reasons; never topic reasons |
| `current_review_id` | nullable FK | Latest effective review |
| `reviewed_at` | timestamp | Last effective decision |
| `next_review_at` | timestamp, indexed | Scheduled review time |
| `drift_score` | decimal 0..1 | Computed topic/identity drift |
| `manual_override_by` | nullable username | Manual decision actor |
| `manual_override_reason` | bounded string | Required for override |
| `manual_override_expires_at` | nullable timestamp | Required for temporary override |
| `row_version` | integer | Optimistic concurrency |
| timestamps | required | Created/updated |

Validation:

- `approved` requires a completed review or a valid manual override.
- `blocked` requires at least one hard reject reason.
- `rejected_scope` must not contain technical/security reasons.
- Expired manual overrides no longer affect the effective decision.

### PodcastSourceReviewRecord

Append-only evidence for each admission run.

| Field | Meaning |
|---|---|
| `id` | Review ID |
| `source_id` | Reviewed source |
| `trigger` | `initial`, `scheduled`, `drift`, `manual`, `migration` |
| `status` | `running`, `succeeded`, `failed`, `cancelled` |
| `sampled_episode_guids_json` | Exact sampled identities |
| `sampled_inputs_hash` | Hash of normalized classification inputs |
| `classifications_json` | Per-sample label, topics, evidence and confidence |
| ratios/confidence | Proposed aggregate result |
| `proposed_decision` | Machine/rule proposal |
| `final_decision` | Effective outcome after manual handling |
| provider/model/prompt fields | Reproducibility |
| reviewer/rationale | Human decision when present |
| timestamps/error | Execution audit; error is redacted |

Indexes:

- `(source_id, created_at desc)`
- `(status, created_at)`
- unique optional `sampled_inputs_hash` per source/model/prompt to reuse an unchanged result

## 4. P2 Podcast Domain Entities

### PodcastEpisodeRecord

One normalized Podcast episode per common article.

| Field group | Fields |
|---|---|
| Identity | `id`, `article_id unique`, `source_id`, `episode_guid`, `podcast_guid`, `identity_basis`, `input_fingerprint` |
| URLs | `canonical_episode_url`, `enclosure_url`, `image_url` |
| Enclosure | `mime_type`, `declared_bytes`, `etag`, `last_modified`, `audio_sha256` |
| Episode | `duration_seconds`, `duration_source`, `language`, `explicit`, `season_number`, `episode_number`, `episode_type`, `show_title` |
| People | bounded `persons_json` |
| Publisher assets | `source_transcripts_json`, `source_chapters_url`, `source_chapters_mime`, `license_url` |
| Dates | `published_at`, `created_at`, `updated_at` |

Constraints:

- Unique `(source_id, episode_guid)` when GUID is present.
- GUID-less fallback identity is a hash of canonical enclosure, publication time and normalized title; `identity_basis` makes this explicit.
- Enclosure URL must pass the same remote-media policy before serving or probing.

### PodcastRightsRecord

Rights may be source-wide or episode-specific. Episode deny takes precedence.

| Field | Meaning |
|---|---|
| `id` | Rights row |
| `source_id` | Required source |
| `episode_id` | Optional episode override |
| `policy` | `link_only`, `transcribe_private`, `derivative_text`, `derivative_audio`, `blocked`, `review_required` |
| booleans | `transcript_allowed`, `derivative_text_allowed`, `derivative_audio_allowed`, `public_distribution_allowed` |
| evidence | `license_name`, `license_url`, `evidence_url`, `note` |
| audit | `reviewed_by`, `reviewed_at`, `expires_at`, `policy_version` |

Validation:

- `derivative_audio_allowed` does not imply `public_distribution_allowed`.
- Publication uses the most restrictive effective source/episode rule.
- Expiry or revocation immediately blocks new publishing and schedules unpublish cleanup.

### PodcastPlaybackStateRecord

| Field | Constraint |
|---|---|
| `username` | FK user |
| `episode_id` | FK episode |
| `variant` | `original` or `digest` |
| `position_ms` | >=0 |
| `duration_ms` | >=0 |
| `completed` | boolean |
| `updated_at` | timestamp |

Unique `(username, episode_id, variant)`.

## 5. P3/P4 Processing Entities

### PodcastProcessingRecord

One immutable-versioned run for one episode/input/pipeline.

| Group | Fields |
|---|---|
| Identity | `id`, `episode_id`, optional `job_id`, `input_fingerprint`, `pipeline_version`, `policy_version`, `requested_target`, `selection_source=policy|editor`, optional `requested_by`, required-on-editor `request_reason`, `idempotency_key` |
| Eligibility | `eligibility_status`, `eligibility_reasons_json` |
| Runtime | `processing_status`, `stage`, `attempt_count`, `lease_owner`, `lease_expires_at`, `heartbeat_at`, monotonic `fencing_token`, `next_retry_at` |
| Providers | ASR/LLM/TTS provider, model and revision fields |
| Metering | audio minutes, input/output tokens, TTS chars/audio tokens, stage cost JSON, estimated and actual total cost |
| Error | stable `error_code`, redacted `error_message` |
| Dates | queued/started/updated/finished timestamps |

Eligibility enum:

- `unknown`
- `blocked_source`
- `blocked_rights`
- `rejected_relevance`
- `rejected_value`
- `invalid_input`
- `over_budget`
- `eligible`

Processing enum:

- `not_required`
- `queued`
- `running`
- `retry_wait`
- `awaiting_review`
- `ready`
- `failed`
- `cancelled`
- `superseded`

Unique `(episode_id, input_fingerprint, pipeline_version, requested_target)` for an effective run; repeated API submissions reuse it through `idempotency_key`. An editor request changes only the candidate-selection source. It still evaluates source, rights, input-safety, budget and resource gates before queueing. An intentional rerun creates a new request/pipeline version, never an in-place reset.

`JobRecord` is only a user-visible batch/progress shell. Workers claim `PodcastProcessingRecord` rows directly; no API-process closure or `asyncio.create_task` is part of the correctness boundary.

### PodcastStageAttemptRecord

Each provider or deterministic stage attempt records `processing_id`, `stage`, `attempt_no`, `fencing_token`, input/output hashes, provider/model/task ID, submission/reconciliation state, usage, estimated/actual cost, timestamps and a redacted error. A request whose submission outcome is unknown must reconcile by provider task ID before a retry can submit again.

### PodcastArtifactRecord

| Field | Meaning |
|---|---|
| `id`, `episode_id`, `processing_id` | Identity |
| `kind` | `publisher_transcript`, `normalized_transcript`, `transcript_zh`, `source_chapters`, `digest_blog_zh`, `narration_script_zh`, `digest_audio_zh`, `digest_chapters` |
| `language` | BCP-47 style language |
| `status` | `draft`, `qa_failed`, `awaiting_review`, `ready`, `published`, `unpublished`, `deleted`, `superseded` |
| `version` | monotonically increasing within episode/kind |
| `content_hash` | content-addressed identity |
| content | bounded `inline_text` or `storage_uri`, never both required |
| media | MIME, bytes, duration, character count |
| publication | `audience`, `publication_status`, `published_at`, `expires_at`, `rights_version` |
| audit | `provenance_json`, `qa_json`, timestamps |

Unique `(episode_id, kind, version)` and `(processing_id, kind, content_hash)`.

### PodcastTranscriptSegmentRecord

| Field | Meaning |
|---|---|
| `artifact_id`, `ordinal` | Ordered identity |
| `start_ms`, `end_ms` | Monotonic audio range |
| `speaker_label` | Stable anonymous A/B/C or publisher label |
| `speaker_name` | Optional verified person name; never inferred from voice alone |
| `text` | Normalized segment text |
| `confidence` | Optional ASR confidence |
| `language` | Segment language |
| `source_segment_id` | Required normalized-source segment for translated segments; publisher/original identity when present |
| `is_translated` | Whether text is a Chinese translation rather than source-language text |

Unique `(artifact_id, ordinal)`. Validate `0 <= start_ms < end_ms` and nondecreasing ranges.

For `transcript_zh`, `language` is `zh-CN`, `source_segment_id` is mandatory and speaker labels must preserve the normalized source turn mapping. One source segment may map to multiple Chinese segments, but not to a different speaker.

### PodcastNarrationSegmentRecord

This is the logical narration-segment schema. P3A may store it inside a validated immutable `narration_script_zh` artifact; create a query table only when administration requires cross-episode SQL access.

| Field | Meaning |
|---|---|
| `artifact_id`, `ordinal` | Ordered narration-script segment |
| `voice_id` | Approved fixed platform narrator |
| `text` | Chinese spoken copy, independent of digest Markdown |
| `evidence_segment_ids_json` | Source-language evidence for factual content in the turn |
| `pronunciation_hints_json` | Reviewed names, acronyms, numbers and model names |
| `estimated_duration_ms` | Pre-synthesis duration budget |
| `section_kind` | `opening`, `context`, `claim`, `evidence`, `caveat`, `conclusion` |

Unique `(artifact_id, ordinal)`. The narrator never maps to, imitates or claims to be the source host/guest. Source speaker identity remains available through evidence segment IDs.

### PodcastClaimRecord

This is the logical evidence schema. P3A may store it inside a validated immutable evidence-fact-pack artifact; create a query table only when product queries justify the additional fact source.

| Field | Meaning |
|---|---|
| `artifact_id`, `ordinal` | Digest claim identity |
| `claim_text` | Atomic claim in the output language |
| `evidence_segment_ids_json` | One or more transcript segments |
| `evidence_start_ms`, `evidence_end_ms` | Convenience time range |
| `verification_status` | `pending`, `supported`, `attributed_opinion`, `unsupported`, `rejected` |
| `uncertainty` | Preserved caveat |
| `verifier_version` | Reproducibility |

Published digest claims may only be `supported` or `attributed_opinion`.

### ArchiveChangeRecord / ArchiveSyncReceiptRecord

- External `ArchiveChangeRecord`: monotonic `seq`, entity kind/id/version/action, payload hash, audience and creation time. It is written in the same transaction as publish, unpublish or takedown.
- Internal `ArchiveSyncReceiptRecord`: producer, last fully committed sequence, bundle/signature hash, import statistics and committed time.
- A bundle containing any invalid entity or blob never advances the receipt. Offset pagination is not a valid synchronization cursor.
- Blob truth is the content-addressed artifact; an internal Reader projection only stores a stable local artifact API identifier.

## 6. Scoring Records

Podcast analysis may reuse the broader analysis subsystem after it is merged, but its contract must expose:

- `scope_relevance_score`
- `quality_score`
- `processing_priority`
- `score_dimensions_json`
- `value_labels_json`
- `score_confidence`
- `analysis_basis` = `show_notes`, `publisher_transcript` or `asr_transcript`
- `input_artifact_hash` and `supersedes_analysis_version`
- `evidence_segment_ids_json`
- provider/model/prompt/scoring versions
- model result and optional effective manual override

Popularity and personal interest are not persisted as intrinsic quality.

## 7. State Transitions

### Source admission

```text
pending → sampling → approved
                   ↘ review_required → approved | rejected_scope
                   ↘ rejected_scope
                   ↘ blocked
sampling failure → failed/retry_wait → sampling
approved + review due → sampling (old decision remains stale, not silently trusted)
```

### Episode processing

```text
discovered → show_notes_preanalysis → premium_candidate
  ├─ source/rights failure   → blocked_*
  ├─ relevance/prevalue fail → not_selected | editor approval
  ├─ budget failure          → over_budget
  ├─ uncertain/no transcript → sample_asr → authorize | rejected_*
  └─ pass/editor approval    → processing_authorized → queued

queued → publisher_transcript_fetch
  ├─ usable transcript → transcript_qa
  └─ none              → download → probe/normalize → ASR → transcript_qa

transcript_qa → transcript_value_score
  ├─ low value → rejected_value
  └─ pass      → premium_ready → summarize_map → summarize_reduce → claim_verify

Duration is recorded for cost estimation, scheduling, resource caps and digest-length planning only. It may improve candidate recall (for example, `is_long_form = duration_seconds > 1800`) but never creates an eligibility transition or blocks a shorter high-value episode. Editor approval can override model selection, not rights, input-safety or budget audit.
               → script_generate → awaiting_review → publish_text
               → TTS → audio_package → audio_qa → publish_audio
```

Retryable errors move to `retry_wait`; nonretryable errors move to `failed`. Input/model/policy changes supersede, rather than overwrite, old runs and artifacts.

## 8. Idempotency Keys

```text
transcript_key = hash(
  audio_sha_or_publisher_transcript_hash,
  asr_model_revision,
  normalization_parameters
)

digest_key = hash(
  transcript_hash,
  llm_revision,
  prompt_version,
  scoring_policy_version
)

tts_key = hash(
  narration_script_hash,
  tts_model_revision,
  voice_id,
  synthesis_settings
)
```

Queue delivery is at least once; writes use lease ownership plus idempotent upsert/content hashes. No stage treats a progress percentage as the truth source.

## 9. Migration Sequence

1. P1 migration: source profile/decision, episode, dimensioned rights and playback state.
2. Shadow-review existing Podcast sources; curated provenance does not auto-pass scope.
3. Resumable idempotent backfill from existing Podcast `extensions_json`; switch reads with legacy fallback.
4. P2 migration: processing/attempt, immutable artifact, archive change and sync receipt.
5. Ship Bundle v2 and internal materialization before any P3 provider integration.
6. Add transcript segment indexes only for Reader query performance; keep immutable transcript artifacts as the cross-environment truth.
7. Add narration/claim query tables later only if validated artifact data is insufficient for real queries.
8. Only after verification, stop writing large processing facts to `extensions_json`.

One agent owns `src/models/db.py` and Alembic migrations for each integration window.
