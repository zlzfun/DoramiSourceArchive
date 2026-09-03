# Research and Technical Decisions

**Feature**: Issue #7 AI/科技播客专栏与中文导读
**Verified**: 2026-09-03

## 1. Executive Finding

No researched product or open-source project provides a verified, production-ready chain for arbitrary Podcast RSS → AI/technology source admission → evidence-grounded Chinese digest blog → licensed Chinese audio with a hard 15-minute ceiling.

Dorami should combine proven primitives behind its own rights, state, evidence, cost and quality gates. The highest-return order is source admission first, structured summaries second, ASR third and TTS last.

## 2. Competitor Findings

### Feedly

**Decision**: Adopt source-scoped concepts, explicit include/exclude rules and feedback; do not treat Feedly as a Podcast processing reference.

**Rationale**: Feedly AI Feeds combine concept models, source scopes, boolean conditions, mute filters and “less like this” feedback. Feedly can subscribe to Podcast RSS, but official AI materials do not establish an audio transcription/TTS pipeline.

**Sources**:

- [Guide to AI Feeds](https://docs.feedly.com/article/699-guide-to-ai-feeds-market-intel)
- [Refining AI Feeds](https://docs.feedly.com/article/549-refining-feedly-ai-feeds)
- [Podcast RSS as a source](https://feedly.com/new-features/posts/the-10-types-of-sources-you-can-add-on-feedly)

### BestBlogs

**Decision**: Separate global content value, taxonomy and editorial review from personal relevance.

**Rationale**: BestBlogs scores technical depth, information value, readability and practical guidance, then generates summaries/tags and retains expert correction. Its AI reader supports Podcast transcript sections and timestamp navigation. Official material around generated audio briefs has changed over time, which reinforces making TTS optional and independently switchable.

**Sources**:

- [How It Works](https://www.bestblogs.dev/en/docs/how-it-works)
- [AI Reading](https://www.bestblogs.dev/en/docs/personal/ai-reading)
- [Changelog](https://www.bestblogs.dev/en/changelog)

### AIHOT

**Decision**: Keep content value and event popularity as separate concepts; make Dorami scores explainable and versioned.

**Rationale**: AIHOT exposes summary, category, score, selected status and recommendation reason while event heat is a different object. Its public material does not establish a first-class Podcast transcript or generated-audio product.

**Sources**:

- [AIHOT](https://aihot.virxact.com/)
- [Agent/API access](https://aihot.virxact.com/agent)

### Snipd

**Decision**: Use timestamp evidence and time-saved UX as benchmarks; do not copy original-audio remixing for the first derivative product.

**Rationale**: Snipd offers speaker transcripts, chapters, summaries and timestamp-grounded questions. AI DJ selects original moments with spoken bridges and targets roughly 25% of source duration; the official example converts 60 minutes to about 15 minutes. It is not a hard 15-minute Chinese derivative-file pipeline.

**Sources**:

- [AI summaries and chat](https://www.snipd.com/blog/ai-podcast-summaries-you-can-chat-with)
- [AI DJ](https://www.snipd.com/blog/ai-dj-listen-to-best-parts-of-any-podcast)

### Podwise

**Decision**: Model summary, chapters, takeaways, keywords, Q&A, transcript and article as distinct artifacts.

**Rationale**: Podwise's enterprise result API returns these objects separately and attaches time/speaker data to transcript output. No verified generated-short-audio capability was found.

**Source**: [Processing Result API](https://docs.podwise.ai/ent-api-v1/processing/get-processing-result)

### Readwise Reader

**Decision**: Deliver a useful transcript/knowledge companion before trying to build a full Podcast application.

**Rationale**: Reader explicitly positions itself as a Podcast companion: users obtain a persistent, searchable, highlightable transcript with cited chat. It does not establish automatic value scoring or short audio generation.

**Source**: [Reader Podcast update](https://readwise.io/reader/update-dec2025)

### Gemini Notebook / NotebookLM

**Decision**: Treat multilingual Audio Overviews as UX evidence, not a production batch dependency.

**Rationale**: Audio imports can be transcribed and Audio Overview supports multiple formats and Simplified Chinese. Official help warns about inaccuracies and does not document RSS batch automation, Dorami's value gates or a 900-second production constraint.

**Sources**:

- [Audio source import](https://support.google.com/notebooklm/answer/16215270)
- [Audio Overview](https://support.google.com/notebooklm/answer/16212820)

## 3. Standards and Rights

### Podcasting 2.0 metadata

**Decision**: Prefer publisher `transcript`, `chapters`, `license`, `person` and alternate-enclosure metadata when present.

**Rationale**: The Podcast Namespace supplies standardized discovery fields and reduces unnecessary ASR work.

**Sources**:

- [Podcast Namespace](https://github.com/Podcastindex-org/podcast-namespace)
- [Namespace XSD](https://github.com/Podcastindex-org/podcast-namespace/blob/main/podcast.xsd)

### Rights boundary

**Decision**: Feed accessibility and transcript availability do not imply a right to republish translations, digests or synthetic audio. Default to `link_only`.

**Rationale**: Transcript, derivative text, derivative audio and public distribution are different permissions. The Podcast Namespace discussion also recognizes transcript derivative-right concerns.

**Source**: [Transcript copyright discussion](https://github.com/Podcastindex-org/podcast-namespace/discussions/458)

**Alternatives considered**:

- Publish everything internally because RSS is public — rejected; access is not a derivative license.
- Copy original voice or audio excerpts — rejected for first release due to copyright, personality-right and dual-timeline complexity.

## 4. Source Admission

**Decision**: Build a thin Dorami-specific admission service using safe feed preview, structured classification, deterministic thresholds and append-only reviews.

**Rationale**: No mature open-source package was found for AI/technology Podcast feed admission. Dorami already has source configuration, feed parsing, remote URL safety and an OpenAI-compatible client. A small service is easier to audit than a general recommendation framework.

**Alternatives considered**:

- Keywords only — too brittle for mixed feeds, aliases and topic drift.
- One LLM yes/no decision — difficult to calibrate or audit.
- Trust all internal imports — conflicts with the product requirement that off-scope feeds must never enter the catalog.

**Chosen hybrid**:

1. Deterministic technical/security checks.
2. Sample-level structured labels and evidence.
3. Deterministic ratio/confidence thresholds.
4. Manual decision with reason and expiry for grey cases.
5. Periodic and drift-triggered reassessment.

## 5. ASR

### Managed-first

**Decision**: Use managed transcription for P3 low-volume production; measure before self-hosting.

**Rationale**: Current OpenAI transcription pricing is minute-based and low enough that operational engineering dominates early volume. Provider adapters prevent lock-in.

**Sources**:

- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [GPT Transcribe model](https://developers.openai.com/api/docs/models/gpt-transcribe)

At published rates, a 60-minute episode is approximately $0.18–$0.36 for current OpenAI transcription model choices. This excludes LLM, storage, retries and TTS.

### Self-hosted candidate

**Decision**: `faster-whisper` is the default self-hosted benchmark; `WhisperX`/speaker diarization is opt-in.

**Rationale**: `faster-whisper` is MIT-licensed, Python-friendly and supports batching, VAD and word timestamps. Word alignment and diarization add dependencies and compute that a chapter-level summary does not always need.

**Sources**:

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [WhisperX](https://github.com/m-bain/whisperX)
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp)

### Chinese candidate

**Decision**: Benchmark SenseVoiceSmall for Chinese/code-switching, but do not make it the default until model-license review passes.

**Rationale**: FunASR/SenseVoice offers attractive Chinese capabilities, but model terms are less straightforward than MIT/Apache code licensing.

**Sources**:

- [FunASR](https://github.com/modelscope/FunASR)
- [FunASR model license](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)

## 6. Summarization and Evidence

**Decision**: Use bounded map/reduce with claim-level evidence verification, not a single full-transcript prompt.

**Rationale**: Long inputs increase omission, position bias and unverifiable synthesis. Normalized segments allow deterministic time links, partial retry, human review and prompt/version comparisons.

**Map output**: claims, numbers, entities, uncertainty, ads/banter, importance and evidence segment IDs.

**Reduce output**: card summary, takeaways, chapters, Chinese digest and narration outline.

**Verifier**: checks every atomic claim against the cited original segment; unsupported content is deleted or expressed as attributed opinion.

**Open-source references**:

- [MinusPod](https://github.com/ttlequals0/minuspod) — useful process-once cache, review and Podcasting 2.0 patterns; its product is ad removal, so it is not a direct dependency.
- [summ-it](https://github.com/syntax-syndicate/summ-it) — transcript-first/fallback pattern; not a production platform dependency.
- [podcast-summarizer](https://github.com/tekeburak/podcast-summarizer) — useful demo only.

## 7. TTS

### MVP

**Decision**: Use a managed, fixed, licensed Chinese stock voice and disclose synthetic speech.

**Rationale**: It avoids GPU operations and voice-consent risk while product demand is unknown. Text is generated first; audio is editor-selected or user-requested.

### Self-hosted evaluation

**Decision**: Evaluate CosyVoice first and Kokoro as a lightweight baseline. Piper is a legal/architecture fallback only; Fish Speech is excluded unless commercial permission is obtained.

**Sources**:

- [CosyVoice](https://github.com/QwenAudio/CosyVoice)
- [Kokoro](https://github.com/hexgrad/kokoro)
- [Piper voices and per-voice licenses](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md)
- [Fish Speech license](https://github.com/fishaudio/fish-speech/blob/main/LICENSE)

**Alternatives considered**:

- Host/guest voice cloning — rejected.
- Original-audio highlights with AI bridges — deferred; requires separate rights and dual-language editorial design.
- Generate every eligible episode — rejected until demand and completion-rate evidence exists.

## 8. Cost Decision

**Decision**: Apply a hard preflight budget and keep all stage costs observable.

```text
C_episode = C_filter + C_download + C_ASR
          + C_LLM(map + reduce + verify)
          + C_TTS + C_storage + C_egress + C_retry
```

Initial operational targets:

- Median fully processed 60-minute episode: <= $0.35.
- Any episode estimated above $0.60 requires explicit administrator approval.
- Publisher transcript path records zero ASR cost.
- TTS is not scheduled until a text artifact passes review and a demand trigger exists.

These are Dorami budget controls, not vendor price guarantees.

## 9. Final Decision Summary

| Topic | Chosen decision |
|---|---|
| Source control | Metadata-only sampled admission before activation/fetch, plus drift reviews |
| Internal imports | Trust boost only; no scope/rights bypass |
| Episode filter | Separate AI/tech relevance from intrinsic value |
| Summary | Four artifacts; transcript claims link to time evidence |
| ASR | Publisher transcript first, managed fallback, faster-whisper benchmark |
| TTS | Fixed licensed voice, on demand, hard measured duration limit |
| Rights | Default link-only; independent text/audio/public permissions |
| Storage | Domain records + immutable artifacts; large blobs outside article extensions |
| Processing | Persistent, idempotent, versioned state machine with cost ledger |
