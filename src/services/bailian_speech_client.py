"""No-retry Model Studio wire client; no credentials on result downloads."""

from __future__ import annotations

import io
import re
from urllib.parse import urlsplit, urlunsplit

import httpx
from config_bailian import BailianSpeechConfig
from services import http_safety


# 只保留已知供应商错误码;响应 message/URL/未知字符串可能回显凭据或签名链接。
_PROVIDER_CODES = frozenset({
    "ModelNotFound", "InvalidModel", "ModelUnavailable", "InvalidApiKey",
    "InvalidParameter", "InvalidParameter.ModelNotFound",
    "InvalidParameter.UnsupportedModel", "AccessDenied", "Forbidden",
    "Throttling", "Throttling.RateQuota", "QuotaExhausted",
    "DataInspectionFailed", "InternalError", "ServiceUnavailable",
})


def provider_error_code(payload):
    code = payload.get("code") if isinstance(payload, dict) else None
    return code if isinstance(code, str) and code in _PROVIDER_CODES else ""


class BailianSpeechError(ValueError):
    def __init__(self, code, *, unknown=False):
        self.code = code
        self.unknown = unknown
        super().__init__(f"Bailian speech: {code}")


def task_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise BailianSpeechError("invalid_task_id", unknown=True)
    return value


def result_url(url, config):
    try:
        p = urlsplit(url)
        if (
            p.scheme not in {"http", "https"}
            or p.username
            or p.password
            or p.port not in (None, 443)
            or p.fragment
        ):
            raise ValueError()
        http_safety.host_suffix_validator(config.result_host_suffixes)(p.hostname or "")
        # Aliyun sometimes returns HTTP OSS links. Use the same host/path/signature
        # over TLS; never fall back to cleartext if TLS fails.
        return urlunsplit(("https", p.netloc, p.path, p.query, ""))
    except Exception:
        raise BailianSpeechError("invalid_result_url") from None


async def download_result(url, config, *, max_bytes):
    destination = io.BytesIO()
    try:
        await http_safety.stream_public_url_to_file(
            result_url(url, config),
            destination,
            max_bytes=max_bytes,
            require_https=True,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                trust_env=False, **kwargs
            ),
            timeout_seconds=config.request_timeout_seconds,
            host_validator=http_safety.host_suffix_validator(
                config.result_host_suffixes
            ),
        )
        return destination.getvalue()
    except Exception:
        raise BailianSpeechError("result_download_failed") from None


class BailianSpeechClient:
    def __init__(self, config: BailianSpeechConfig, *, transport=None):
        if not config.asr_poll_configured:
            raise BailianSpeechError("credentials_missing")
        self.config = config
        self.http = httpx.Client(
            timeout=config.request_timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self):
        self.http.close()

    def request(self, method, path, *, body=None, asynchronous=False):
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        if asynchronous:
            headers["X-DashScope-Async"] = "enable"
        try:
            response = self.http.request(
                method, self.config.base_url + path, headers=headers, json=body
            )
        except httpx.HTTPError:
            raise BailianSpeechError(
                "transport_error", unknown=method == "POST"
            ) from None
        if not 200 <= response.status_code < 300:
            provider_code = ""
            if len(response.content) <= 2 * 1024 * 1024:
                try:
                    provider_code = provider_error_code(response.json())
                except ValueError:
                    pass
            code = f"http_{response.status_code}"
            if provider_code:
                code += f":{provider_code}"
            # 服务端错误/超时/冲突仍可能已经计费,不得因有错误码改成可重提。
            unknown = method == "POST" and (response.status_code >= 500 or response.status_code in (408, 409))
            raise BailianSpeechError(code, unknown=unknown)
        try:
            if len(response.content) > 2 * 1024 * 1024:
                raise ValueError()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise BailianSpeechError(
                "invalid_response", unknown=method == "POST"
            ) from None
        if payload.get("code"):
            code = provider_error_code(payload)
            raise BailianSpeechError(
                f"invalid_response:{code}" if code else "invalid_response",
                unknown=method == "POST",
            )
        return payload

    def submit_asr(self, file_url):
        return self.request(
            "POST",
            "/services/audio/asr/transcription",
            asynchronous=True,
            body={
                "model": self.config.asr_model,
                "input": {"file_urls": [file_url]},
                "parameters": {"channel_id": [0]},
            },
        )

    def poll_asr(self, identity):
        return self.request("GET", "/tasks/" + task_id(identity))

    def synthesize(self, text):
        if not text.strip() or len(text) > 600:
            raise BailianSpeechError("invalid_text_length")
        return self.request(
            "POST",
            "/services/aigc/multimodal-generation/generation",
            body={
                "model": self.config.tts_model,
                "input": {
                    "text": text,
                    "voice": self.config.tts_voice,
                    "language_type": "Auto",
                },
            },
        )
