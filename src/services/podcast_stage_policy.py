"""Single execution-authority guard for every Podcast processing boundary."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from config import PODCAST_STAGES, AliyunIsiConfig, PodcastConfig
from services.aliyun_isi_usage import (
    AliyunIsiUsageConfigurationError,
    asr_usage_plan,
    tts_usage_plan,
)
from services.podcast_worker_contracts import ProviderUsagePlan, ProviderUsageUnit


EXECUTION_BOUNDARIES = frozenset({"enqueue", "claim", "provider_submit", "commit"})
ARTIFACT_STAGE_BY_KIND = {
    "source_media_snapshot": "fetch",
    "publisher_transcript": "fetch",
    "normalized_transcript": "asr",
    "transcript_zh": "translate",
    "source_chapters": "analyze",
    "evidence_fact_pack": "analyze",
    "digest_blog_zh": "digest",
    "digest_chapters": "digest",
    "narration_script_zh": "script",
    "digest_audio_zh": "local_publish",
}
_EXTERNAL_ARTIFACT_KINDS = frozenset(ARTIFACT_STAGE_BY_KIND)
_INTERNAL_ARTIFACT_KINDS = frozenset()


class PodcastStageDenied(PermissionError):
    """The configured installation is not authoritative for an operation."""


@dataclass(frozen=True)
class PodcastStagePolicy:
    config: PodcastConfig
    aliyun_isi: AliyunIsiConfig | None = None

    def require_stage(self, stage: str, *, boundary: str) -> None:
        normalized_stage = (stage or "").strip().lower()
        normalized_boundary = (boundary or "").strip().lower()
        if normalized_stage not in PODCAST_STAGES:
            raise ValueError(f"unknown Podcast stage: {stage}")
        if normalized_boundary not in EXECUTION_BOUNDARIES:
            raise ValueError(f"unknown Podcast execution boundary: {boundary}")
        if normalized_stage not in self.config.allowed_stages:
            raise PodcastStageDenied(
                f"Podcast stage '{normalized_stage}' denied at '{normalized_boundary}' "
                f"for installation '{self.config.installation}' "
                f"(authority_id={self.config.authority_id})"
            )

    @property
    def writable_artifact_kinds(self) -> frozenset[str]:
        if self.config.installation == "external":
            return _EXTERNAL_ARTIFACT_KINDS
        if self.config.installation == "internal":
            return _INTERNAL_ARTIFACT_KINDS
        return _EXTERNAL_ARTIFACT_KINDS | _INTERNAL_ARTIFACT_KINDS

    def artifact_kind_writer_allowed(self, kind: str) -> bool:
        return (kind or "").strip().lower() in self.writable_artifact_kinds

    def provider_usage_plan_matches(
        self,
        provider_name: str,
        stage: str,
        plan: ProviderUsagePlan,
        *,
        now: dt.datetime,
    ) -> bool:
        """Verify an Aliyun plan against trusted resolved accounting config."""

        if (provider_name or "").strip().lower() != "aliyun-isi":
            return True
        if self.aliyun_isi is None:
            return False
        normalized_stage = (stage or "").strip().lower()
        try:
            if normalized_stage == "asr":
                if plan.unit is not ProviderUsageUnit.AUDIO_SECONDS:
                    return False
                expected = asr_usage_plan(
                    self.aliyun_isi,
                    audio_duration_ms=plan.reserved_units * 1000,
                    now=now,
                )
            elif normalized_stage == "tts":
                if plan.unit is not ProviderUsageUnit.TTS_CHARACTERS:
                    return False
                expected = tts_usage_plan(
                    self.aliyun_isi,
                    billable_characters=plan.reserved_units,
                    now=now,
                )
            else:
                return False
        except (AliyunIsiUsageConfigurationError, ValueError):
            return False
        return expected == plan

    def provider_call_guard_seconds(self, provider_name: str, stage: str) -> int | None:
        """Return the configured request horizon required before a paid call."""

        if (provider_name or "").strip().lower() != "aliyun-isi":
            return 0
        if self.aliyun_isi is None or (stage or "").strip().lower() not in {"asr", "tts"}:
            return None
        return self.aliyun_isi.request_timeout_seconds

    def require_artifact_writer(self, kind: str, *, boundary: str = "commit") -> None:
        normalized = (kind or "").strip().lower()
        stage = ARTIFACT_STAGE_BY_KIND.get(normalized)
        if stage is None:
            raise ValueError(f"unknown Podcast artifact kind: {normalized}")
        if normalized not in self.writable_artifact_kinds:
            raise PodcastStageDenied(
                f"Podcast artifact kind '{normalized}' is not writable by "
                f"installation '{self.config.installation}' "
                f"(authority_id={self.config.authority_id}) at '{boundary}'"
            )
        # The stage that creates the artifact owns its commit. External creates
        # every Podcast artifact; internal installs only synchronized replicas.
        self.require_stage(stage, boundary=boundary)

    def capabilities(self) -> dict:
        """Return operational identity/permissions only; no provider config or secret."""

        return {
            "installation": self.config.installation,
            "authority_id": self.config.authority_id,
            "allowed_stages": list(self.config.allowed_stages),
            "execution_boundaries": sorted(EXECUTION_BOUNDARIES),
            "writable_artifact_kinds": sorted(self.writable_artifact_kinds),
        }


def policy_for(config: PodcastConfig) -> PodcastStagePolicy:
    return PodcastStagePolicy(config)


def require_stage(
    stage: str,
    *,
    boundary: str,
    policy: Optional[PodcastStagePolicy] = None,
) -> None:
    """Canonical stage guard for queues, workers, providers and commits."""

    if policy is None:
        # Lazy import keeps configuration tests able to call load_config() with
        # isolated environment variables instead of binding settings at import.
        from config import settings

        policy = PodcastStagePolicy(settings.podcast)
    policy.require_stage(stage, boundary=boundary)


def artifact_kind_writer_allowed(
    kind: str, *, policy: Optional[PodcastStagePolicy] = None
) -> bool:
    if policy is None:
        from config import settings

        policy = PodcastStagePolicy(settings.podcast)
    return policy.artifact_kind_writer_allowed(kind)
