import json
import logging
import os
import sys
from urllib.parse import parse_qs

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import AliyunIsiConfig  # noqa: E402
from services.aliyun_isi_asr import (  # noqa: E402
    AliyunAsrError,
    AliyunAsrPollError,
    AliyunAsrRejected,
    AliyunAsrSubmissionUnknown,
    AliyunIsiAsrClient,
    AsrState,
    validate_provider_fetch_url,
)
from services.aliyun_isi_auth import AliyunPopClient  # noqa: E402


def _config(**updates):
    values = {
        "access_key_id": "ak-id",
        "access_key_secret": "ak-secret",
        "app_key": "app-key",
        "asr_domain": "asr.example.test",
    }
    values.update(updates)
    return AliyunIsiConfig(**values)


def _client(handler, **config_updates):
    config = _config(**config_updates)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return AliyunIsiAsrClient(
        config,
        pop_client=AliyunPopClient(config, http_client=http),
        file_url_resolver=lambda value: value,
    )


def test_submit_uses_pop_form_with_double_encoded_task_and_no_nls_token():
    observed = {}

    def handler(request: httpx.Request):
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["form"] = parse_qs(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "TaskId": "provider-task-1",
                "RequestId": "request-1",
                "StatusText": "SUCCESS",
                "StatusCode": 21050000,
            },
        )

    client = _client(handler, access_token="must-not-be-used", token_expires_at=9999999999)
    result = client.submit("https://audio.example.test/episode.wav?sig=a%2Bb")

    assert result.task_id == "provider-task-1"
    assert observed["method"] == "POST"
    assert observed["url"] == "https://asr.example.test/"
    assert set(observed["form"]) >= {"Action", "Version", "Task", "Signature"}
    assert observed["form"]["Action"] == ["SubmitTask"]
    assert observed["form"]["Version"] == ["2018-08-17"]
    task = json.loads(observed["form"]["Task"][0])
    assert task == {
        "appkey": "app-key",
        "file_link": "https://audio.example.test/episode.wav?sig=a%2Bb",
        "version": "4.0",
        "enable_words": True,
        "auto_split": True,
        "enable_sample_rate_adaptive": True,
        "enable_callback": False,
    }
    assert "must-not-be-used" not in str(observed)


def test_submit_resolves_public_redirect_chain_before_provider_request():
    provider_forms: list[dict[str, list[str]]] = []
    redirect_requests: list[str] = []
    original = "https://pscrb.example/episode?token=do-not-persist"
    final = "https://audio.transistor.example/final.mp3?delivery=signed"
    redirects = {
        "/episode": "https://pscrb.example/one",
        "/one": "https://tracker.example/two",
        "/two": "https://redirect.example/three",
        "/three": final,
    }

    def redirect_handler(request: httpx.Request):
        redirect_requests.append(str(request.url))
        location = redirects.get(request.url.path)
        if location is not None:
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(200, headers={"Content-Type": "audio/mpeg"})

    def redirect_client_factory(**kwargs):
        return httpx.Client(
            transport=httpx.MockTransport(redirect_handler),
            **kwargs,
        )

    def provider_handler(request: httpx.Request):
        provider_forms.append(parse_qs(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "TaskId": "redirect-task",
                "StatusCode": 21050000,
                "StatusText": "SUCCESS",
            },
        )

    config = _config(request_timeout_seconds=5)
    provider_http = httpx.Client(transport=httpx.MockTransport(provider_handler))
    client = AliyunIsiAsrClient(
        config,
        pop_client=AliyunPopClient(config, http_client=provider_http),
        redirect_client_factory=redirect_client_factory,
    )

    result = client.submit(original)

    assert result.task_id == "redirect-task"
    assert redirect_requests == [
        original,
        "https://pscrb.example/one",
        "https://tracker.example/two",
        "https://redirect.example/three",
        final,
    ]
    task = json.loads(provider_forms[0]["Task"][0])
    assert task["file_link"] == final
    assert original not in provider_forms[0]["Task"][0]


def test_submit_rejects_unsafe_redirect_before_requesting_target_or_provider():
    redirect_requests: list[str] = []
    provider_requests = 0

    def redirect_handler(request: httpx.Request):
        redirect_requests.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/private.mp3"},
        )

    def redirect_client_factory(**kwargs):
        return httpx.Client(
            transport=httpx.MockTransport(redirect_handler),
            **kwargs,
        )

    def provider_handler(_request: httpx.Request):
        nonlocal provider_requests
        provider_requests += 1
        pytest.fail("unsafe redirect must fail before provider submit")

    config = _config(request_timeout_seconds=5)
    provider_http = httpx.Client(transport=httpx.MockTransport(provider_handler))
    client = AliyunIsiAsrClient(
        config,
        pop_client=AliyunPopClient(config, http_client=provider_http),
        redirect_client_factory=redirect_client_factory,
    )

    with pytest.raises(AliyunAsrRejected) as caught:
        client.submit("https://podcast.example/start.mp3")

    assert caught.value.code == "source_url_resolution_failed"
    assert redirect_requests == ["https://podcast.example/start.mp3"]
    assert provider_requests == 0


def test_poll_needs_no_app_key_but_submit_rejects_before_network():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "TaskId": "paid-task-1",
                "StatusCode": 21050001,
                "StatusText": "RUNNING",
            },
        )

    config = _config(app_key="")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = AliyunIsiAsrClient(
        config,
        pop_client=AliyunPopClient(config, http_client=http),
    )
    assert client.poll("paid-task-1").state is AsrState.RUNNING
    with pytest.raises(AliyunAsrRejected) as caught:
        client.submit("https://audio.example.test/a.wav")
    assert caught.value.code == "app_key_unavailable"
    assert caught.value.retryable is True
    assert len(requests) == 1


@pytest.mark.parametrize(
    "value",
    [
        "/tmp/audio.wav",
        "https://127.0.0.1/a.wav",
        "https://localhost/a.wav",
        "https://user:pass@audio.example.test/a.wav",
        "https://audio/a.wav",
        "https://audio.example.test/a.wav#fragment",
        "https://audio.example.test:bad/a.wav",
        "https://%31%32%37.0.0.1/a.wav",
        "https://audio.example.test/a b.wav",
        "https://audio_example.test/a.wav",
        "https://audio.example.test:/a.wav",
        "https://audio.example.test:0/a.wav",
    ],
)
def test_submit_rejects_non_provider_fetchable_url_before_network(value):
    with pytest.raises(ValueError, match="provider-fetchable|IP literal"):
        validate_provider_fetch_url(value)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_provider_fetch_url_accepts_public_domain_and_preserves_exact_url(scheme):
    value = f"{scheme}://audio.example.test/episode.mp3?token=a%2Bb&part=1"
    assert validate_provider_fetch_url(value) == value


def test_submit_timeout_is_unknown_and_is_never_retried():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        raise httpx.ReadTimeout("after send", request=request)

    client = _client(handler)
    with pytest.raises(AliyunAsrSubmissionUnknown, match="transport_unknown"):
        client.submit("https://audio.example.test/a.wav")
    assert len(calls) == 1


@pytest.mark.parametrize("status", [408, 425, 500, 503])
def test_submit_ambiguous_http_failure_is_unknown(status):
    client = _client(
        lambda _request: httpx.Response(status, json={"Code": "provider-error"})
    )
    with pytest.raises(AliyunAsrSubmissionUnknown, match="http_unknown"):
        client.submit("https://audio.example.test/a.wav")


def test_submit_http_throttle_is_an_explicit_retryable_rejection():
    client = _client(
        lambda _request: httpx.Response(429, json={"Code": "Throttling.User"})
    )
    with pytest.raises(AliyunAsrRejected) as caught:
        client.submit("https://audio.example.test/a.wav")
    assert caught.value.retryable is True


def test_submit_pop_throttle_is_an_explicit_retryable_rejection():
    client = _client(
        lambda _request: httpx.Response(
            400,
            json={"Code": "Throttling.User", "RequestId": "request-1"},
        )
    )
    with pytest.raises(AliyunAsrRejected) as caught:
        client.submit("https://audio.example.test/a.wav")
    assert caught.value.retryable is True


def test_submit_http_5xx_and_accepted_without_id_are_unknown():
    responses = iter(
        [
            httpx.Response(503, json={"Code": "ServiceUnavailable"}),
            httpx.Response(200, text="not-json"),
            httpx.Response(200, json={"StatusCode": 21050000, "StatusText": "SUCCESS"}),
        ]
    )
    client = _client(lambda _request: next(responses))

    with pytest.raises(AliyunAsrSubmissionUnknown, match="http_unknown"):
        client.submit("https://audio.example.test/a.wav")
    with pytest.raises(AliyunAsrSubmissionUnknown, match="http_unknown"):
        client.submit("https://audio.example.test/a.wav")
    with pytest.raises(AliyunAsrSubmissionUnknown, match="accepted_without_task_id"):
        client.submit("https://audio.example.test/a.wav")


@pytest.mark.parametrize(
    "payload",
    [
        {"StatusCode": 41050013, "StatusText": "REQUEST_INVALID"},
    ],
)
def test_submit_business_rejection_is_known(payload):
    client = _client(lambda _request: httpx.Response(200, json=payload))
    with pytest.raises(AliyunAsrRejected) as caught:
        client.submit("https://audio.example.test/a.wav")
    assert caught.value.retryable is False


def test_submit_provider_server_error_is_unknown_not_automatically_retryable():
    for code in (50000000, 50000001, 51050000, 52010001):
        client = _client(
            lambda _request, code=code: httpx.Response(
                200,
                json={"StatusCode": code, "StatusText": "SERVER_ERROR"},
            )
        )
        with pytest.raises(AliyunAsrSubmissionUnknown, match="provider_server_unknown"):
            client.submit("https://audio.example.test/a.wav")


def test_submit_explicit_throttle_is_known_and_retryable():
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={"StatusCode": 40000005, "StatusText": "TOO_MANY_REQUESTS"},
        )
    )
    with pytest.raises(AliyunAsrRejected) as caught:
        client.submit("https://audio.example.test/a.wav")
    assert caught.value.retryable is True


def test_submit_malformed_business_code_is_unknown():
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={"StatusCode": "sensitive-bad-code", "StatusText": "SUCCESS"},
        )
    )
    with pytest.raises(AliyunAsrSubmissionUnknown, match="invalid_submit_response"):
        client.submit("https://audio.example.test/a.wav")


def test_submit_unknown_preserves_a_valid_task_id_only_for_reconciliation():
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={
                "TaskId": "provider-task-for-reconcile",
                "StatusCode": 21050000,
                "StatusText": "INCONSISTENT",
            },
        )
    )
    with pytest.raises(AliyunAsrSubmissionUnknown) as caught:
        client.submit("https://audio.example.test/a.wav")
    assert caught.value.task_id == "provider-task-for-reconcile"
    assert "provider-task-for-reconcile" not in str(caught.value)


@pytest.mark.parametrize(
    ("code", "status", "state"),
    [
        (21050002, "QUEUEING", AsrState.QUEUED),
        (21050001, "RUNNING", AsrState.RUNNING),
        (21050003, "SUCCESS_WITH_NO_VALID_FRAGMENT", AsrState.EMPTY),
        ("ASR_RESPONSE_HAVE_NO_WORDS", "ASR_RESPONSE_HAVE_NO_WORDS", AsrState.EMPTY),
        (41050002, "FILE_DOWNLOAD_FAILED", AsrState.FAILED_TERMINAL),
        (40000005, "TOO_MANY_REQUESTS", AsrState.FAILED_RETRYABLE),
        (50000000, "SERVER_ERROR", AsrState.FAILED_RETRYABLE),
        (51050000, "SERVER_ERROR", AsrState.FAILED_RETRYABLE),
    ],
)
def test_poll_maps_provider_states(code, status, state):
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={"TaskId": "task-1", "StatusCode": code, "StatusText": status},
        )
    )
    result = client.poll("task-1")
    assert result.state is state
    assert result.transcript is None


def test_poll_success_normalizes_sentence_word_timestamps_and_duration():
    payload = {
        "TaskId": "task-1",
        "StatusCode": 21050000,
        "StatusText": "SUCCESS",
        "BizDuration": 2956,
        "Result": {
            "Sentences": [
                {"BeginTime": 1300, "EndTime": 2365, "Text": "北京的天气。", "ChannelId": 0},
                {"BeginTime": 340, "EndTime": 1200, "Text": "今天很好。", "ChannelId": 1},
            ],
            "Words": [
                {"BeginTime": 340, "EndTime": 640, "Word": "北京", "ChannelId": 0},
                {"BeginTime": 640, "EndTime": 940, "Word": "天气", "ChannelId": 0},
            ],
        },
    }
    client = _client(lambda _request: httpx.Response(200, json=payload))
    result = client.poll("task-1")

    assert result.state is AsrState.SUCCEEDED
    assert result.transcript is not None
    assert result.transcript.text == "今天很好。\n北京的天气。"
    assert result.transcript.audio_duration_ms == 2956
    assert result.transcript.segments[0].channel_id == 1
    assert result.transcript.words[0].begin_ms == 340


@pytest.mark.parametrize(
    "payload",
    [
        {"TaskId": "task-1", "StatusCode": "bad"},
        {"TaskId": "other", "StatusCode": 21050001},
        {"TaskId": "task-1", "StatusCode": 21050000, "StatusText": "SUCCESS", "BizDuration": 10, "Result": {}},
        {
            "TaskId": "task-1",
            "StatusCode": 21050000,
            "StatusText": "SUCCESS",
            "BizDuration": 10,
            "Result": {"Sentences": [{"BeginTime": 5, "EndTime": 4, "Text": "bad", "ChannelId": 0}]},
        },
        {
            "TaskId": "task-1",
            "StatusCode": 21050000,
            "StatusText": "SUCCESS",
            "BizDuration": 10,
            "Result": {"Sentences": [{"BeginTime": 5, "EndTime": 5, "Text": "bad", "ChannelId": 0}]},
        },
        {
            "TaskId": "task-1",
            "StatusCode": 21050000,
            "StatusText": "SUCCESS",
            "BizDuration": 10,
            "Result": {"Sentences": [{"BeginTime": 0, "EndTime": 5, "Text": 123, "ChannelId": 0}]},
        },
        {
            "TaskId": "task-1",
            "StatusCode": 21050000,
            "StatusText": "SUCCESS",
            "BizDuration": 10,
            "Result": {"Sentences": [{"BeginTime": 0, "EndTime": 11, "Text": "bad", "ChannelId": 0}]},
        },
        {
            "TaskId": "task-1",
            "StatusCode": 21050000,
            "StatusText": "RUNNING",
            "BizDuration": 10,
            "Result": {"Sentences": [{"BeginTime": 0, "EndTime": 10, "Text": "bad", "ChannelId": 0}]},
        },
        {
            "TaskId": "task-1",
            "StatusCode": 21050000.5,
            "StatusText": "SUCCESS",
        },
    ],
)
def test_poll_rejects_malformed_success_or_identity(payload):
    client = _client(lambda _request: httpx.Response(200, json=payload))
    with pytest.raises(AliyunAsrError):
        client.poll("task-1")


@pytest.mark.parametrize(("status", "retryable"), [(429, True), (503, True), (403, False)])
def test_poll_http_errors_are_classified_without_resubmission(status, retryable):
    client = _client(
        lambda _request: httpx.Response(status, json={"Code": "provider-error"})
    )
    with pytest.raises(AliyunAsrPollError) as caught:
        client.poll("task-1")
    assert caught.value.retryable is retryable


def test_poll_pop_throttle_is_retryable_without_exposing_provider_code():
    client = _client(
        lambda _request: httpx.Response(
            400,
            json={"Code": "Throttling.User", "RequestId": "request-1"},
        )
    )
    with pytest.raises(AliyunAsrPollError) as caught:
        client.poll("task-1")
    assert caught.value.retryable is True
    assert "Throttling.User" not in str(caught.value)


def test_poll_http_info_log_redacts_all_signed_query_identifiers(caplog, monkeypatch):
    config = _config(
        access_key_id="sensitive-ak-id",
        access_key_secret="sensitive-ak-secret",
        security_token="sensitive-sts-token",
    )
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "TaskId": "sensitive-task-id",
                    "StatusCode": 21050001,
                    "StatusText": "RUNNING",
                },
            )
        )
    )
    client = AliyunIsiAsrClient(
        config,
        pop_client=AliyunPopClient(config, http_client=http),
    )
    httpx_logger = logging.getLogger("httpx")
    # Alembic's logging setup may disable pre-existing loggers when migration
    # tests run first. Keep this assertion independent of suite ordering.
    monkeypatch.setattr(httpx_logger, "disabled", False)
    monkeypatch.setattr(httpx_logger, "propagate", True)
    caplog.set_level(logging.INFO, logger="httpx")

    client.poll("sensitive-task-id")

    rendered = caplog.text
    for secret in (
        "sensitive-ak-id",
        "sensitive-ak-secret",
        "sensitive-sts-token",
        "sensitive-task-id",
    ):
        assert secret not in rendered
    assert "?[REDACTED]" in rendered


def test_submit_honors_disabled_asr_feature_switches():
    observed = {}

    def handler(request: httpx.Request):
        observed.update(json.loads(parse_qs(request.content.decode())["Task"][0]))
        return httpx.Response(
            200,
            json={
                "TaskId": "task-1",
                "StatusCode": 21050000,
                "StatusText": "SUCCESS",
            },
        )

    client = _client(
        handler,
        asr_enable_words=False,
        asr_auto_split=False,
        asr_enable_sample_rate_adaptive=False,
    )
    client.submit("https://audio.example.test/a.wav")

    assert observed["enable_words"] is False
    assert observed["auto_split"] is False
    assert observed["enable_sample_rate_adaptive"] is False


def test_asr_client_close_respects_pop_client_ownership():
    class InjectedPop:
        closed = 0

        def close(self):
            self.closed += 1

    injected = InjectedPop()
    wrapper = AliyunIsiAsrClient(_config(), pop_client=injected)
    wrapper.close()
    assert injected.closed == 0

    owned = AliyunIsiAsrClient(_config())
    owned_http = owned._pop_client._http_client
    owned.close()
    owned.close()
    assert owned_http.is_closed is True
