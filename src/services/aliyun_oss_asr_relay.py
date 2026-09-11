"""Private OSS relay used only after Aliyun cannot fetch an RSS enclosure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import httpx
import oss2

from config import AliyunIsiConfig, PodcastArtifactStorageConfig
from models.db import PodcastSourceMediaSnapshotRecord
from services.aliyun_isi_asr import validate_provider_fetch_url
from services.podcast_artifacts import PodcastArtifactStore
from services.podcast_source_media import (
    EnclosureSnapshot,
    download_snapshot_media,
)
from services.podcast_worker_contracts import StageContext


class OssRelayError(RuntimeError):
    """Known failure before an ASR fallback submission is attempted."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "aliyun_oss_fallback_failed")
        super().__init__(self.code)


class _Bucket(Protocol):
    def put_object_from_file(self, key: str, filename: str, headers=None): ...

    def sign_url(
        self, method: str, key: str, expires: int, *, slash_safe: bool = False
    ) -> str: ...

    def delete_object(self, key: str): ...


BucketFactory = Callable[[str], _Bucket]


_EXTENSIONS = {
    "audio/aac": "aac",
    "audio/flac": "flac",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
}


@dataclass(frozen=True)
class AliyunOssAsrRelay:
    config: AliyunIsiConfig
    artifact_store: PodcastArtifactStore
    storage_config: PodcastArtifactStorageConfig
    bucket_factory: BucketFactory | None = None
    download_client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient

    def __post_init__(self) -> None:
        if not self.config.asr_oss_configured:
            raise ValueError("Aliyun OSS ASR relay is not configured")

    def _bucket(self, endpoint: str) -> _Bucket:
        if self.bucket_factory is not None:
            return self.bucket_factory(endpoint)
        credentials = oss2.credentials.StaticCredentialsProvider(
            self.config.access_key_id,
            self.config.access_key_secret,
            self.config.security_token,
        )
        auth = oss2.ProviderAuthV4(credentials)
        return oss2.Bucket(
            auth,
            endpoint,
            self.config.asr_oss_bucket,
            region=self.config.region_id,
        )

    def object_key(self, context: StageContext) -> str:
        extension = _EXTENSIONS.get(context.input_artifact.mime_type, "bin")
        return (
            f"{self.config.asr_oss_prefix}/{context.attempt_id}/"
            f"{context.input_artifact.content_hash}.{extension}"
        )

    def prepare(
        self,
        context: StageContext,
        *,
        enclosure: EnclosureSnapshot,
        expected: PodcastSourceMediaSnapshotRecord,
    ) -> str:
        """Upload exact validated bytes and return an internal signed GET URL."""

        key = self.object_key(context)
        uploaded = False
        try:
            with download_snapshot_media(
                self.artifact_store,
                enclosure=enclosure,
                expected=expected,
                storage_config=self.storage_config,
                client_factory=self.download_client_factory,
            ) as media:
                result = self._bucket(self.config.asr_oss_endpoint).put_object_from_file(
                    key,
                    str(media.path),
                    headers={"Content-Type": media.mime},
                )
                status = int(getattr(result, "status", 0) or 0)
                if status < 200 or status >= 300:
                    raise OssRelayError("aliyun_oss_fallback_upload_failed")
                uploaded = True
            signed_url = self._bucket(
                self.config.asr_oss_internal_endpoint
            ).sign_url(
                "GET",
                key,
                self.config.asr_oss_signed_url_ttl_seconds,
                slash_safe=True,
            )
            return validate_provider_fetch_url(signed_url)
        except OssRelayError:
            if uploaded:
                self.delete(context)
            raise
        except Exception as exc:
            if uploaded:
                self.delete(context)
            # Never retain SDK exceptions: request URLs can contain signatures.
            raise OssRelayError("aliyun_oss_fallback_prepare_failed") from None

    def delete(self, context: StageContext) -> None:
        """Best-effort eager cleanup; the bucket lifecycle remains the backstop."""

        try:
            self._bucket(self.config.asr_oss_endpoint).delete_object(
                self.object_key(context)
            )
        except Exception:
            return


__all__ = ["AliyunOssAsrRelay", "OssRelayError"]
