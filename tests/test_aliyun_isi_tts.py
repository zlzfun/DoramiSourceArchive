import json
import logging
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import AliyunIsiConfig  # noqa: E402
from services.aliyun_isi_auth import NlsToken, NlsTokenManager  # noqa: E402
from services.aliyun_isi_tts import (  # noqa: E402
    AliyunIsiTtsClient,
    AliyunTtsError,
    AliyunTtsPollError,
    AliyunTtsRejected,
    AliyunTtsSubmissionUnknown,
    TtsState,
    parse_voice_profiles,
)


PROFILE = {
    "narrator_zh": {
        "voice": "aixia",
        "format": "wav",
        "sample_rate": 16000,
        "volume": 50,
        "speech_rate": 0,
        "pitch_rate": 0,
        "enable_subtitle": True,
    }
}


class StaticTokenManager:
    def __init__(self, value="nls-secret-token"):
        self.value = value
        self.invalidations = 0
        self.closed = 0

    def get(self):
        return NlsToken(self.value, 4_102_444_800)

    def invalidate(self, expected_value=None):
        assert expected_value == self.value
        self.invalidations += 1
        return True

    def close(self):
        self.closed += 1


def _config(**updates):
    values = {
        "app_key": "sensitive-appkey",
        "access_token": "nls-secret-token",
        "token_expires_at": 4_102_444_800,
        "tts_url": "https://tts.example.test/rest/v1/tts/async",
        "tts_voice_profiles_json": json.dumps(PROFILE),
        "tts_result_allowed_host_suffixes": ("aliyuncs.com",),
    }
    values.update(updates)
    return AliyunIsiConfig(**values)


def _client(handler, *, token_manager=None, **updates):
    config = _config(**updates)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return AliyunIsiTtsClient(
        config,
        token_manager=token_manager or StaticTokenManager(),
        http_client=http,
    )


def _accepted(task_id="tts-task-1"):
    return {
        "status": 200,
        "error_code": 20000000,
        "error_message": "SUCCESS",
        "request_id": "request-1",
        "data": {"task_id": task_id},
    }


def test_submit_sends_exact_json_contract_without_authorization_header():
    observed = {}

    def handler(request: httpx.Request):
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=_accepted())

    result = _client(handler).submit("这是测试。", voice_profile="narrator_zh")

    assert result.task_id == "tts-task-1"
    assert result.voice_profile == "narrator_zh"
    assert len(result.settings_revision) == 64
    assert observed["method"] == "POST"
    assert observed["url"] == "https://tts.example.test/rest/v1/tts/async"
    assert "authorization" not in observed["headers"]
    assert observed["body"] == {
        "header": {
            "appkey": "sensitive-appkey",
            "token": "nls-secret-token",
        },
        "context": {"device_id": "dorami-source-archive"},
        "payload": {
            "enable_notify": False,
            "tts_request": {"text": "这是测试。", **PROFILE["narrator_zh"]},
        },
    }
    assert "nls-secret-token" not in observed["url"]


def test_submit_never_forwards_secrets_or_text_across_redirects():
    observed = []

    def handler(request: httpx.Request):
        observed.append(str(request.url))
        if request.url.host != "tts.example.test":
            raise AssertionError("redirect target must not be requested")
        return httpx.Response(
            307,
            headers={"Location": "https://evil.example/steal"},
            json={
                "status": 307,
                "error_code": 40000003,
                "error_message": "REDIRECT",
            },
        )

    client = AliyunIsiTtsClient(
        _config(),
        token_manager=StaticTokenManager(),
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        ),
    )
    with pytest.raises(AliyunTtsRejected):
        client.submit("绝不能转发的口播全文", voice_profile="narrator_zh")
    assert observed == ["https://tts.example.test/rest/v1/tts/async"]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: "not-json",
        lambda value: [],
        lambda value: {**value, "data": {}},
        lambda value: {**value, "status": 201},
        lambda value: {**value, "error_message": "RUNNING"},
    ],
)
def test_submit_malformed_or_inconsistent_2xx_is_unknown(mutator):
    body = mutator(_accepted())
    response = (
        httpx.Response(200, text=body)
        if isinstance(body, str)
        else httpx.Response(200, json=body)
    )
    client = _client(lambda _request: response)
    with pytest.raises(AliyunTtsSubmissionUnknown):
        client.submit("测试", voice_profile="narrator_zh")


def test_submit_inconsistent_success_preserves_task_id_for_reconciliation():
    payload = {**_accepted("reconcile-task"), "error_message": "RUNNING"}
    client = _client(lambda _request: httpx.Response(200, json=payload))

    with pytest.raises(AliyunTtsSubmissionUnknown) as caught:
        client.submit("测试", voice_profile="narrator_zh")

    assert caught.value.task_id == "reconcile-task"
    assert "reconcile-task" not in str(caught.value)


def test_submit_non_2xx_success_identity_is_unknown_not_rejected():
    client = _client(
        lambda _request: httpx.Response(409, json=_accepted("reconcile-task"))
    )
    with pytest.raises(AliyunTtsSubmissionUnknown) as caught:
        client.submit("测试", voice_profile="narrator_zh")
    assert caught.value.task_id == "reconcile-task"


@pytest.mark.parametrize("code", [40000001, 40000005])
def test_submit_any_business_error_with_task_identity_is_unknown(code):
    payload = {
        "status": 200,
        "error_code": code,
        "error_message": "provider contradiction",
        "data": {"task_id": "reconcile-task"},
    }
    manager = StaticTokenManager()
    client = _client(
        lambda _request: httpx.Response(200, json=payload),
        token_manager=manager,
    )
    with pytest.raises(AliyunTtsSubmissionUnknown) as caught:
        client.submit("测试", voice_profile="narrator_zh")
    assert caught.value.task_id == "reconcile-task"
    assert manager.invalidations == 0


def test_submit_timeout_and_server_failures_are_unknown_and_never_retried():
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret response", request=request)

    with pytest.raises(AliyunTtsSubmissionUnknown, match="transport_unknown"):
        _client(timeout).submit("测试", voice_profile="narrator_zh")
    assert len(calls) == 1

    for response in (
        httpx.Response(503, json={"error_code": 50000000}),
        httpx.Response(
            200,
            json={
                "status": 500,
                "error_code": 50000001,
                "error_message": "SERVER_ERROR",
            },
        ),
    ):
        with pytest.raises(AliyunTtsSubmissionUnknown):
            _client(lambda _request, response=response: response).submit(
                "测试", voice_profile="narrator_zh"
            )

    for status in (408, 425):
        with pytest.raises(AliyunTtsSubmissionUnknown):
            _client(
                lambda _request, status=status: httpx.Response(status, text="timeout")
            ).submit("测试", voice_profile="narrator_zh")


@pytest.mark.parametrize(
    ("http_status", "code", "retryable"),
    [(400, 40000003, False), (400, 40000005, True), (429, 40000005, True)],
)
def test_submit_explicit_business_rejections_are_classified(
    http_status, code, retryable
):
    payload = {
        "status": http_status,
        "error_code": code,
        "error_message": "provider detail must not escape",
    }
    client = _client(lambda _request: httpx.Response(http_status, json=payload))
    with pytest.raises(AliyunTtsRejected) as caught:
        client.submit("测试", voice_profile="narrator_zh")
    assert caught.value.retryable is retryable
    assert payload["error_message"] not in str(caught.value)


def test_submit_auth_rejection_invalidates_token_for_a_future_attempt():
    manager = StaticTokenManager()
    client = _client(
        lambda _request: httpx.Response(
            400,
            json={
                "status": 400,
                "error_code": 40000001,
                "error_message": "token 'nls-secret-token' is invalid",
            },
        ),
        token_manager=manager,
    )
    with pytest.raises(AliyunTtsRejected) as caught:
        client.submit("测试", voice_profile="narrator_zh")
    assert caught.value.retryable is True
    assert manager.invalidations == 1
    assert "nls-secret-token" not in str(caught.value)


@pytest.mark.parametrize(
    "profiles",
    [
        "not-json",
        "[]",
        json.dumps({"bad alias!": PROFILE["narrator_zh"]}),
        json.dumps({"narrator": {**PROFILE["narrator_zh"], "sample_rate": 24000}}),
        json.dumps({"narrator": {**PROFILE["narrator_zh"], "format": "pcm"}}),
        json.dumps({"narrator": {**PROFILE["narrator_zh"], "volume": 101}}),
        json.dumps({"narrator": {**PROFILE["narrator_zh"], "unknown": True}}),
        '{"narrator_zh":' + json.dumps(PROFILE["narrator_zh"]) + ',"narrator_zh":{}}',
        '{"narrator_zh":{"voice":"aixia","voice":"siqi","format":"wav","sample_rate":16000,"volume":50,"speech_rate":0,"pitch_rate":0,"enable_subtitle":true}}',
    ],
)
def test_voice_profile_configuration_fails_closed(profiles):
    with pytest.raises(ValueError):
        parse_voice_profiles(profiles)


def test_voice_profile_revision_is_stable_and_changes_with_settings():
    original = parse_voice_profiles(json.dumps(PROFILE))["narrator_zh"]
    reordered = parse_voice_profiles(
        json.dumps(
            {
                "narrator_zh": dict(
                    reversed(list(PROFILE["narrator_zh"].items()))
                )
            }
        )
    )["narrator_zh"]
    changed_payload = json.loads(json.dumps(PROFILE))
    changed_payload["narrator_zh"]["speech_rate"] = 1
    changed = parse_voice_profiles(json.dumps(changed_payload))["narrator_zh"]

    assert original.revision == reordered.revision
    assert original.revision != changed.revision


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "bad\x00text",
        "\ud800",
        '<speak voice="other">绕过音色</speak>',
        '<soundEvent src="https://untrusted.example/audio.mp3"/>',
    ],
)
def test_submit_rejects_invalid_text_before_network(text):
    client = _client(lambda _request: pytest.fail("network must not be called"))
    with pytest.raises(ValueError):
        client.submit(text, voice_profile="narrator_zh")


def test_submit_enforces_configured_text_limit_before_network():
    client = _client(
        lambda _request: pytest.fail("network must not be called"),
        tts_max_chars=3,
    )
    with pytest.raises(ValueError, match="character limit"):
        client.submit("四个字符", voice_profile="narrator_zh")


def test_submit_requires_result_host_allowlist_before_network():
    client = _client(
        lambda _request: pytest.fail("network must not be called"),
        tts_result_allowed_host_suffixes=(),
    )

    with pytest.raises(ValueError, match="host allowlist"):
        client.submit("测试", voice_profile="narrator_zh")


def test_poll_running_sends_only_minimal_query_contract():
    observed = {}

    def handler(request):
        observed["method"] = request.method
        observed["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "status": 200,
                "error_code": 20000000,
                "error_message": "RUNNING",
                "data": {"task_id": "task-1", "audio_address": None},
            },
        )

    result = _client(handler).poll("task-1")

    assert result.state is TtsState.RUNNING
    assert observed == {
        "method": "GET",
        "query": {
            "appkey": "sensitive-appkey",
            "token": "nls-secret-token",
            "task_id": "task-1",
        },
    }


@pytest.mark.parametrize(
    ("error_code", "message"),
    [
        (21050001, "RUNNING"),
        (21050002, "QUEUEING"),
        (20000000, "QUEUEING"),
        (20000000, "WAITING"),
    ],
)
def test_poll_accepts_documented_and_observed_pending_states(
    error_code, message
):
    result = _client(
        lambda _request: httpx.Response(
            200,
            json={
                "status": 200,
                "error_code": error_code,
                "error_message": message,
                "data": {"task_id": "task-1", "audio_address": None},
            },
        )
    ).poll("task-1")

    assert result.state is TtsState.RUNNING


def test_poll_success_accepts_allowlisted_https_signed_url_and_sorts_subtitles():
    audio_url = "https://nls-cloud-cn-shanghai.oss-cn-shanghai.aliyuncs.com/a.wav?Expires=9&Signature=secret"
    payload = {
        "status": 200,
        "error_code": 20000000,
        "error_message": "SUCCESS",
        "data": {
            "task_id": "task-1",
            "audio_address": audio_url,
            "sentences": [
                {"text": "第二句", "begin_time": "100", "end_time": "200"},
                {"text": "第一句", "begin_time": "0", "end_time": "100"},
            ],
        },
    }

    result = _client(lambda _request: httpx.Response(200, json=payload)).poll(
        "task-1"
    )

    assert result.state is TtsState.SUCCEEDED
    assert result.audio_url == audio_url
    assert [item.text for item in result.subtitles] == ["第一句", "第二句"]
    assert audio_url not in repr(result)
    assert "Signature=secret" not in repr(result)


def test_poll_rejects_overlapping_subtitles():
    payload = {
        "status": 200,
        "error_code": 20000000,
        "error_message": "SUCCESS",
        "data": {
            "task_id": "task-1",
            "audio_address": "https://safe.aliyuncs.com/a.wav",
            "sentences": [
                {"text": "第一句", "begin_time": 0, "end_time": 200},
                {"text": "第二句", "begin_time": 100, "end_time": 300},
            ],
        },
    }

    with pytest.raises(AliyunTtsError, match="overlapping_subtitles"):
        _client(lambda _request: httpx.Response(200, json=payload)).poll("task-1")


@pytest.mark.parametrize(
    "audio_url",
    [
        "ftp://safe.aliyuncs.com/audio.wav",
        "https://evil.example/audio.wav",
        "https://127.0.0.1/audio.wav",
        "https://user:pass@safe.aliyuncs.com/audio.wav",
        "https://safe.aliyuncs.com:0/audio.wav",
        "https://safe.aliyuncs.com/audio.wav#fragment",
    ],
)
def test_poll_rejects_untrusted_or_malformed_audio_url(audio_url):
    payload = {
        "status": 200,
        "error_code": 20000000,
        "error_message": "SUCCESS",
        "data": {"task_id": "task-1", "audio_address": audio_url},
    }
    with pytest.raises(AliyunTtsError, match="invalid_audio_url"):
        _client(lambda _request: httpx.Response(200, json=payload)).poll("task-1")


def test_poll_upgrades_allowlisted_http_result_to_https_without_losing_signature():
    payload = {
        "status": 200,
        "error_code": 20000000,
        "error_message": "SUCCESS",
        "data": {
            "task_id": "task-1",
            "audio_address": (
                "http://safe.aliyuncs.com/audio.wav?Expires=9&Signature=secret"
            ),
        },
    }

    result = _client(lambda _request: httpx.Response(200, json=payload)).poll(
        "task-1"
    )

    assert result.audio_url == (
        "https://safe.aliyuncs.com/audio.wav?Expires=9&Signature=secret"
    )
    assert "Signature=secret" not in repr(result)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": 200,
            "error_code": 20000000,
            "error_message": "SUCCESS",
            "data": {"task_id": "task-1", "audio_address": None},
        },
        {
            "status": 200,
            "error_code": 20000000,
            "error_message": "RUNNING",
            "data": {"task_id": "task-1", "audio_address": "https://safe.aliyuncs.com/a.wav"},
        },
        {
            "status": 200,
            "error_code": 20000000,
            "error_message": "SUCCESS",
            "data": {"task_id": "other", "audio_address": "https://safe.aliyuncs.com/a.wav"},
        },
    ],
)
def test_poll_rejects_inconsistent_success_or_task_identity(payload):
    with pytest.raises((AliyunTtsError, AliyunTtsPollError)):
        _client(lambda _request: httpx.Response(200, json=payload)).poll("task-1")


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (40000004, True),
        (40000003, False),
        (40000005, True),
        (50000000, True),
        (50000001, True),
    ],
)
def test_poll_business_errors_never_claim_the_synthesis_task_failed(
    code, retryable
):
    payload = {
        "status": 200,
        "error_code": code,
        "error_message": "provider detail",
    }
    client = _client(lambda _request: httpx.Response(200, json=payload))
    with pytest.raises(AliyunTtsPollError) as caught:
        client.poll("task-1")
    assert caught.value.retryable is retryable


def test_poll_auth_failure_invalidates_token_and_retries_only_same_task_later():
    manager = StaticTokenManager()
    payload = {
        "status": 400,
        "error_code": 40000001,
        "error_message": "token 'nls-secret-token' is invalid",
    }
    client = _client(
        lambda _request: httpx.Response(400, json=payload),
        token_manager=manager,
    )
    with pytest.raises(AliyunTtsPollError) as caught:
        client.poll("existing-task")
    assert caught.value.retryable is True
    assert manager.invalidations == 1
    assert "nls-secret-token" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (408, True), (425, True), (429, True), (503, True)],
)
def test_poll_malformed_http_failures_are_classified(status, retryable):
    client = _client(lambda _request: httpx.Response(status, text="not-json"))
    with pytest.raises(AliyunTtsPollError) as caught:
        client.poll("existing-task")
    assert caught.value.retryable is retryable


def test_poll_refreshes_expired_token_and_keeps_existing_task_id():
    observed = {}

    class FakePop:
        def call(self, **_kwargs):
            return {"Token": {"Id": "fresh-token", "ExpireTime": 5000}}

    config = _config(
        access_key_id="ak-id",
        access_key_secret="ak-secret",
        access_token="expired",
        token_expires_at=10,
    )
    manager = NlsTokenManager(config, pop_client=FakePop(), clock=lambda: 1000)

    def handler(request):
        observed.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "status": 200,
                "error_code": 20000000,
                "error_message": "RUNNING",
                "data": {"task_id": "existing-task", "audio_address": None},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = AliyunIsiTtsClient(config, token_manager=manager, http_client=http)
    result = client.poll("existing-task")

    assert result.state is TtsState.RUNNING
    assert observed["task_id"] == "existing-task"
    assert observed["token"] == "fresh-token"


def test_poll_never_forwards_query_credentials_across_redirects():
    observed = []

    def handler(request: httpx.Request):
        observed.append(str(request.url))
        if request.url.host != "tts.example.test":
            raise AssertionError("redirect target must not be requested")
        return httpx.Response(
            307,
            headers={"Location": "https://evil.example/steal"},
            json={
                "status": 307,
                "error_code": 40000003,
                "error_message": "REDIRECT",
            },
        )

    client = AliyunIsiTtsClient(
        _config(),
        token_manager=StaticTokenManager(),
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        ),
    )
    with pytest.raises(AliyunTtsPollError):
        client.poll("original-task")
    assert len(observed) == 1
    assert observed[0].startswith(
        "https://tts.example.test/rest/v1/tts/async?"
    )


def test_poll_auth_rejection_refreshes_then_queries_only_original_task_id():
    calls = []

    class FakePop:
        def call(self, **_kwargs):
            calls.append("create-token")
            return {"Token": {"Id": "fresh-token", "ExpireTime": 5000}}

    config = _config(
        access_key_id="ak-id",
        access_key_secret="ak-secret",
        access_token="rejected-token",
        token_expires_at=2000,
    )
    manager = NlsTokenManager(config, pop_client=FakePop(), clock=lambda: 1000)
    requests = []

    def handler(request):
        requests.append((request.method, dict(request.url.params)))
        if len(requests) == 1:
            return httpx.Response(
                400,
                json={
                    "status": 400,
                    "error_code": 40000001,
                    "error_message": "TOKEN_INVALID",
                },
            )
        return httpx.Response(
            200,
            json={
                "status": 200,
                "error_code": 20000000,
                "error_message": "RUNNING",
                "data": {"task_id": "original-task", "audio_address": None},
            },
        )

    client = AliyunIsiTtsClient(
        config,
        token_manager=manager,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AliyunTtsPollError):
        client.poll("original-task")
    result = client.poll("original-task")

    assert result.state is TtsState.RUNNING
    assert calls == ["create-token"]
    assert [method for method, _params in requests] == ["GET", "GET"]
    assert [params["task_id"] for _method, params in requests] == [
        "original-task",
        "original-task",
    ]


def test_poll_http_logs_redact_complete_query(caplog):
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={
                "status": 200,
                "error_code": 20000000,
                "error_message": "RUNNING",
                "data": {"task_id": "sensitive-task", "audio_address": None},
            },
        )
    )
    caplog.set_level(logging.INFO, logger="httpx")
    client.poll("sensitive-task")

    assert "sensitive-appkey" not in caplog.text
    assert "nls-secret-token" not in caplog.text
    assert "sensitive-task" not in caplog.text
    assert "?[REDACTED]" in caplog.text


def test_tts_client_close_respects_injected_dependencies():
    manager = StaticTokenManager()
    http = httpx.Client(transport=httpx.MockTransport(lambda _request: None))
    injected = AliyunIsiTtsClient(_config(), token_manager=manager, http_client=http)
    injected.close()
    assert manager.closed == 0
    assert http.is_closed is False
    http.close()

    owned = AliyunIsiTtsClient(_config())
    owned_http = owned._http_client
    owned.close()
    owned.close()
    assert owned_http.is_closed is True
