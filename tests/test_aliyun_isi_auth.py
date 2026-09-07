import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import AliyunIsiConfig  # noqa: E402
from services.aliyun_isi_auth import (  # noqa: E402
    AliyunIsiConfigurationError,
    AliyunIsiProtocolError,
    AliyunIsiTransportError,
    AliyunPopClient,
    NlsToken,
    NlsTokenManager,
    canonicalized_query,
    pop_signature,
)


FIXED_TIME = dt.datetime(2019, 4, 18, 8, 32, 31, tzinfo=dt.timezone.utc)
FIXED_NONCE = "b924c8c3-6d03-4c5d-ad36-d984d3116788"


def _config(**updates):
    values = {
        "access_key_id": "my_access_key_id",
        "access_key_secret": "my_access_key_secret",
        "app_key": "app-key",
        "token_url": "https://token.example.test/",
        "token_refresh_skew_seconds": 30,
    }
    values.update(updates)
    return AliyunIsiConfig(**values)


def test_pop_signature_matches_official_create_token_vector():
    parameters = {
        "AccessKeyId": "my_access_key_id",
        "Action": "CreateToken",
        "Version": "2019-02-28",
        "Timestamp": "2019-04-18T08:32:31Z",
        "Format": "JSON",
        "RegionId": "cn-shanghai",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": FIXED_NONCE,
    }

    assert canonicalized_query(parameters).startswith("AccessKeyId=my_access_key_id&")
    assert (
        pop_signature("GET", parameters, "my_access_key_secret")
        == "hHq4yNsPitlfDJ2L0nQPdugdEzM="
    )


def test_pop_post_signs_form_body_and_keeps_secrets_out_of_url():
    observed = {}

    def handle(request: httpx.Request):
        observed["url"] = str(request.url)
        observed["form"] = parse_qs(request.content.decode("utf-8"))
        observed["content_type"] = request.headers["Content-Type"]
        return httpx.Response(200, json={"Token": {"Id": "token", "ExpireTime": 2000}})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    client = AliyunPopClient(
        _config(security_token="sts-token"),
        http_client=http,
        nonce_factory=lambda: FIXED_NONCE,
        clock=lambda: FIXED_TIME,
    )
    payload = client.call(
        action="CreateToken",
        version="2019-02-28",
        url="https://token.example.test/",
        parameters={"RegionId": "cn-shanghai"},
    )

    assert payload["Token"]["Id"] == "token"
    assert observed["url"] == "https://token.example.test/"
    assert observed["form"]["AccessKeyId"] == ["my_access_key_id"]
    assert observed["form"]["SecurityToken"] == ["sts-token"]
    assert observed["form"]["Signature"]
    assert observed["form"]["Signature"] == ["MbUc6hZPbeAvPrIXuAfgFVHUFqU="]
    assert observed["content_type"].startswith("application/x-www-form-urlencoded")
    assert "my_access_key_secret" not in request_text(observed)


def test_pop_get_uses_query_and_signs_unicode_without_a_body():
    observed = {}

    def handle(request: httpx.Request):
        observed["method"] = request.method
        observed["query"] = parse_qs(request.url.query.decode("ascii"))
        observed["body"] = request.content
        return httpx.Response(200, json={"StatusCode": 21050001})

    client = AliyunPopClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        nonce_factory=lambda: FIXED_NONCE,
        clock=lambda: FIXED_TIME,
    )
    client.call(
        action="GetTaskResult",
        version="2018-08-17",
        url="https://asr.example.test/",
        method="GET",
        parameters={"TaskId": "中文 + % task"},
    )

    assert observed["method"] == "GET"
    assert observed["body"] == b""
    assert observed["query"]["TaskId"] == ["中文 + % task"]
    unsigned = {key: values[0] for key, values in observed["query"].items()}
    signature = unsigned.pop("Signature")
    assert signature == pop_signature("GET", unsigned, "my_access_key_secret")


def test_pop_create_token_never_follows_redirect_from_injected_client():
    observed = []

    def handle(request: httpx.Request):
        observed.append(str(request.url))
        if request.url.host == "token.example.test":
            return httpx.Response(
                302,
                headers={"Location": "https://redirect.example.test/steal"},
                json={"Code": "Redirect"},
            )
        raise AssertionError("redirect target must not be requested")

    client = AliyunPopClient(
        _config(),
        http_client=httpx.Client(
            transport=httpx.MockTransport(handle),
            follow_redirects=True,
        ),
    )
    with pytest.raises(AliyunIsiProtocolError) as caught:
        client.call(
            action="CreateToken",
            version="2019-02-28",
            url="https://token.example.test/",
            parameters={"RegionId": "cn-shanghai"},
        )

    assert caught.value.status_code == 302
    assert observed == ["https://token.example.test/"]


def request_text(observed):
    return f"{observed['url']} {observed['form']}"


def test_token_manager_uses_valid_cached_token_without_network():
    class NoCall:
        def call(self, **_kwargs):
            raise AssertionError("provider must not be called")

    manager = NlsTokenManager(
        _config(access_token="cached-token", token_expires_at=2000),
        pop_client=NoCall(),
        clock=lambda: 1000,
    )

    token = manager.get()

    assert token.value == "cached-token"
    assert token.expires_at == 2000
    assert "cached-token" not in repr(token)


def test_token_manager_refreshes_once_from_provider_expiry_across_threads():
    calls = []
    refreshed = []

    class FakePop:
        def call(self, **kwargs):
            calls.append(kwargs)
            return {
                "RequestId": "request-1",
                "Token": {"Id": "fresh-token", "ExpireTime": 5000},
            }

    manager = NlsTokenManager(
        _config(access_token="old", token_expires_at=1010),
        pop_client=FakePop(),
        clock=lambda: 1000,
        on_refresh=refreshed.append,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _index: manager.get(), range(16)))

    assert len(calls) == 1
    assert len(refreshed) == 1
    assert all(item.value == "fresh-token" for item in values)
    assert calls[0]["parameters"] == {"RegionId": "cn-shanghai"}


def test_token_manager_fails_closed_without_refresh_credentials():
    config = AliyunIsiConfig(
        app_key="app-key",
        access_token="expired",
        token_expires_at=100,
        token_refresh_skew_seconds=30,
    )
    manager = NlsTokenManager(config, clock=lambda: 1000)

    with pytest.raises(AliyunIsiConfigurationError, match="refresh is unavailable"):
        manager.get()


def test_token_manager_invalidate_forces_refresh_without_exposing_old_token():
    calls = []

    class FakePop:
        def call(self, **kwargs):
            calls.append(kwargs)
            return {"Token": {"Id": "fresh-token", "ExpireTime": 5000}}

    manager = NlsTokenManager(
        _config(access_token="provider-rejected-token", token_expires_at=2000),
        pop_client=FakePop(),
        clock=lambda: 1000,
    )

    manager.invalidate()
    refreshed = manager.get()

    assert refreshed.value == "fresh-token"
    assert len(calls) == 1
    assert "provider-rejected-token" not in repr(manager._token)


def test_token_manager_stale_rejection_cannot_invalidate_newer_token():
    manager = NlsTokenManager(
        _config(access_token="new-token", token_expires_at=2000),
        pop_client=None,
        clock=lambda: 1000,
    )

    assert manager.invalidate("old-token") is False
    assert manager.get().value == "new-token"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"Token": None},
        {"Token": {"Id": "", "ExpireTime": 5000}},
        {"Token": {"Id": "token", "ExpireTime": "bad"}},
        {"Token": {"Id": "token", "ExpireTime": 1010}},
    ],
)
def test_token_manager_rejects_malformed_or_already_expiring_token(payload):
    class FakePop:
        def call(self, **_kwargs):
            return payload

    manager = NlsTokenManager(
        _config(),
        pop_client=FakePop(),
        clock=lambda: 1000,
    )

    with pytest.raises(AliyunIsiProtocolError):
        manager.get()


def test_pop_errors_never_include_credentials_or_provider_body():
    secret_echo = "my_access_key_secret"
    token_echo = "sensitive-provider-token"

    def bad_response(_request: httpx.Request):
        return httpx.Response(
            403,
            json={
                "Code": "Forbidden",
                "Message": f"credential={secret_echo}; token={token_echo}",
                "RequestId": "safe-request-id",
            },
        )

    client = AliyunPopClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(bad_response)),
    )
    with pytest.raises(AliyunIsiProtocolError) as caught:
        client.call(
            action="CreateToken",
            version="2019-02-28",
            url="https://token.example.test/",
        )

    rendered = f"{caught.value!r} {caught.value}"
    assert secret_echo not in rendered
    assert token_echo not in rendered
    assert "safe-request-id" not in rendered
    assert "request_ref=" in rendered


def test_pop_error_exposes_only_safe_throttle_category():
    client = AliyunPopClient(
        _config(),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    400,
                    json={"Code": "Throttling.User", "RequestId": "request-1"},
                )
            )
        ),
    )
    with pytest.raises(AliyunIsiProtocolError) as caught:
        client.call(
            action="GetTaskResult",
            version="2018-08-17",
            url="https://asr.example.test/",
            method="GET",
            parameters={"TaskId": "task-1"},
        )
    assert caught.value.provider_error_kind == "throttled"
    assert "Throttling.User" not in str(caught.value)


def test_provider_parse_cause_cannot_retain_sensitive_response():
    secret = "provider-secret-value"

    def malformed(_request: httpx.Request):
        return httpx.Response(200, text=secret)

    client = AliyunPopClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(malformed)),
    )
    with pytest.raises(AliyunIsiProtocolError) as caught:
        client.call(
            action="CreateToken",
            version="2019-02-28",
            url="https://token.example.test/",
        )
    assert caught.value.__cause__ is None
    assert secret not in str(caught.value)


def test_pop_transport_error_is_redacted():
    def timeout(request: httpx.Request):
        raise httpx.ReadTimeout("contains-sensitive-url", request=request)

    client = AliyunPopClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(timeout)),
    )
    with pytest.raises(AliyunIsiTransportError) as caught:
        client.call(
            action="CreateToken",
            version="2019-02-28",
            url="https://token.example.test/",
        )
    assert "contains-sensitive-url" not in str(caught.value)


def test_pop_rejects_reserved_parameter_override_and_unclean_endpoint():
    client = AliyunPopClient(_config())
    try:
        with pytest.raises(ValueError, match="cannot override"):
            client.call(
                action="CreateToken",
                version="2019-02-28",
                url="https://token.example.test/",
                parameters={"AccessKeyId": "attacker"},
            )
        with pytest.raises(ValueError, match="cannot override"):
            client.call(
                action="CreateToken",
                version="2019-02-28",
                url="https://token.example.test/",
                parameters={"SecurityToken": "attacker"},
            )
        with pytest.raises(ValueError, match="clean HTTPS"):
            client.call(
                action="CreateToken",
                version="2019-02-28",
                url="http://token.example.test/?secret=value",
            )
        with pytest.raises(ValueError, match="clean HTTPS"):
            client.call(
                action="CreateToken",
                version="2019-02-28",
                url="https://token.example.test/not-root",
            )
    finally:
        client.close()


def test_nls_token_expiry_uses_unix_seconds_and_skew():
    token = NlsToken("value", 1300)
    assert token.is_valid_at(1000, skew_seconds=299) is True
    assert token.is_valid_at(1000, skew_seconds=300) is False


def test_client_close_respects_transport_ownership():
    injected_http = httpx.Client(transport=httpx.MockTransport(lambda _request: None))
    injected = AliyunPopClient(_config(), http_client=injected_http)
    injected.close()
    injected.close()
    assert injected_http.is_closed is False
    injected_http.close()

    owned = AliyunPopClient(_config())
    owned_http = owned._http_client
    owned.close()
    owned.close()
    assert owned_http.is_closed is True
