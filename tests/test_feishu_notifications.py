"""Feishu transport tests: no credentials and no external network calls."""

import asyncio
import datetime as dt
import json
import logging
import os
import sys
from dataclasses import replace

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.feishu_notifications import (  # noqa: E402
    MAX_REQUEST_BYTES,
    FeishuNotificationConfig,
    PublicEvidenceLink,
    ReviewDigestItem,
    build_daily_digest,
    load_feishu_config,
    send_digest,
    sign_request,
)


HOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/fixture-hook-not-a-real-endpoint"
SECRET = "fixture-signing-secret"


@pytest.fixture
def config():
    return FeishuNotificationConfig(
        enabled=True, webhook_url=HOOK, signing_secret=SECRET,
        review_base_url="https://dorami.example.com",
    )


def item(**kwargs):
    return replace(ReviewDigestItem(
        name="递归自我改进", kind="topic", recommendation="建立新主题",
        reason="多篇独立文章讨论同一概念", article_count=5, source_count=3,
        review_path="/admin/taxonomy/review/42",
        evidence=(PublicEvidenceLink("公开文章", "https://example.com/article", is_public=True),),
    ), **kwargs)


def payload(config, items=None, **kwargs):
    return build_daily_digest(
        config, [item()] if items is None else items,
        digest_date=dt.date.today(), inbox_path="/admin/taxonomy/review", **kwargs,
    )


def test_signature_matches_fixed_empty_message_vector():
    assert sign_request(1599360473, SECRET) == "4v3St5jvcheR0MpzodX8/Mfeyza6fFhhJZyVHUCOsbw="


def test_desktop_review_link_requires_explicit_loopback_opt_in(config):
    with pytest.raises(ValueError):
        replace(config, review_base_url="http://127.0.0.1:5199")
    local = replace(config, review_base_url="http://127.0.0.1:5199", allow_loopback_review_url=True)
    built = payload(local)
    assert "http://127.0.0.1:5199/admin/taxonomy/review" in json.dumps(built)


@pytest.mark.parametrize("url", [
    "http://example.com:5199", "http://127.0.0.1.evil.example:5199",
    "http://127.1:5199", "http://192.168.1.10:5199", "http://user@localhost:5199",
    "http://localhost:5199?token=fixture", "http://localhost:5199/#action", "http://localhost:0",
])
def test_loopback_option_cannot_widen_to_other_insecure_destinations(config, url):
    with pytest.raises(ValueError):
        replace(config, review_base_url=url, allow_loopback_review_url=True)


@pytest.mark.parametrize("url", [
    "http://open.feishu.cn/open-apis/bot/v2/hook/fixture-hook-123456",
    "https://open.feishu.cn.evil.com/open-apis/bot/v2/hook/fixture-hook-123456",
    "https://evil.com/open-apis/bot/v2/hook/fixture-hook-123456",
    "https://user:pass@open.feishu.cn/open-apis/bot/v2/hook/fixture-hook-123456",
    HOOK + "?token=secret", HOOK + "#fragment", HOOK + "/extra",
    HOOK + "?", HOOK + "#", "\n" + HOOK, HOOK + "\n",
    HOOK.replace(".cn/", ".cn:444/"), HOOK.replace("hook/", "hook/%2e%2e/"),
    "https://127.0.0.1/open-apis/bot/v2/hook/fixture-hook-123456",
])
def test_webhook_is_exact_https_origin_and_path(url):
    with pytest.raises(ValueError, match="Invalid Feishu webhook endpoint") as error:
        FeishuNotificationConfig(webhook_url=url)
    assert url not in str(error.value)


def test_config_requires_signing_secret_and_safe_review_url():
    with pytest.raises(ValueError):
        FeishuNotificationConfig(enabled=True, webhook_url=HOOK, review_base_url="https://example.com")
    for url in ["http://example.com", "https://example.com/?token=x", "https://127.0.0.1", "https://x.local"]:
        with pytest.raises(ValueError):
            FeishuNotificationConfig(review_base_url=url)


def test_private_ini_template_and_secret_repr(tmp_path):
    path = tmp_path / "private.ini"
    path.write_text("[feishu_notifications]\nenabled=false\nwebhook_url=\nsigning_secret=\nreview_base_url=\ntimezone=Asia/Shanghai\ndigest_hour=18\n")
    value = load_feishu_config(path)
    assert value.enabled is False and value.digest_hour == 18
    path.write_text(f"[feishu_notifications]\nenabled=true\nwebhook_url={HOOK}\nsigning_secret={SECRET}\nreview_base_url=https://example.com\n")
    value = load_feishu_config(path)
    assert value.webhook_url == HOOK and value.signing_secret == SECRET
    assert HOOK not in repr(value) and SECRET not in repr(value)
    path.write_text(f"[feishu_notifications]\nsigning_secret={SECRET}\nsigning_secret={SECRET}\n")
    with pytest.raises(ValueError) as error:
        load_feishu_config(path)
    assert SECRET not in str(error.value)


def test_empty_changes_do_not_create_a_message(config):
    assert payload(config, []) is None


def test_rich_text_keeps_content_plain_and_only_public_clean_links(config):
    value = payload(config, [item(
        name="<at user_id=all>everyone</at>",
        evidence=(
            PublicEvidenceLink("private", "https://example.com/private"),
            PublicEvidenceLink("signed", "https://example.com/a?signature=secret", is_public=True),
            PublicEvidenceLink("local", "https://127.0.0.1/a", is_public=True),
            PublicEvidenceLink("public", "https://example.com/a", is_public=True),
        ),
    )])
    nodes = [node for row in value["content"]["post"]["zh_cn"]["content"] for node in row]
    assert all(node["tag"] in {"text", "a"} for node in nodes)
    assert any("<at user_id=all>" in node.get("text", "") for node in nodes)
    links = [node["href"] for node in nodes if node["tag"] == "a"]
    assert "https://example.com/a" in links
    assert not any("private" in link or "signature=" in link or "127.0.0.1" in link for link in links)


@pytest.mark.parametrize("path", ["//evil.com", "/x?approve=true", "/x#token", "/x/../approve", "https://example.com", "/x/%2e%2e"])
def test_review_routes_are_plain_paths_without_action_queries(config, path):
    with pytest.raises(ValueError, match="Invalid review route"):
        payload(config, [item(review_path=path)])


def test_large_digest_is_one_bounded_summary_with_inbox_link(config):
    entries = [item(
        name="字" * 5000, recommendation="建议" * 5000, reason="依据" * 5000,
        evidence=tuple(PublicEvidenceLink("文章" * 500, f"https://example.com/{'x' * 1200}/{i}", True) for i in range(5)),
    ) for _ in range(100)]
    value = payload(config, entries, total_count=120)
    encoded = json.dumps(value, ensure_ascii=False).encode()
    assert len(encoded) < MAX_REQUEST_BYTES - 200
    rows = value["content"]["post"]["zh_cn"]["content"]
    assert rows[-1][0]["href"] == "https://dorami.example.com/admin/taxonomy/review"
    assert "其余" in rows[-2][0]["text"]
    assert "120" in rows[0][0]["text"]


def test_success_makes_one_signed_post_and_returns_only_acceptance(config):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        data = json.loads(request.content)
        assert data["timestamp"] == "1599360473"
        assert data["sign"] == "4v3St5jvcheR0MpzodX8/Mfeyza6fFhhJZyVHUCOsbw="
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    result = asyncio.run(send_digest(config, payload(config), transport=httpx.MockTransport(handler), timestamp=1599360473))
    assert result.status == "accepted"
    assert len(seen) == 1


@pytest.mark.parametrize("status,data,expected,code", [
    (200, {"code": 19021, "msg": SECRET + HOOK}, "failed", "provider_rejected"),
    (200, {"StatusCode": 0}, "unknown", "invalid_response"),
    (200, {"code": False}, "unknown", "invalid_response"),
    (200, [], "unknown", "invalid_response"),
    (200, "not json", "unknown", "invalid_response"),
    (302, {}, "failed", "http_rejected"),
    (429, {}, "failed", "http_rejected"),
    (500, {}, "unknown", "http_uncertain"),
])
def test_http_and_provider_failures_never_retry_or_expose_response(config, status, data, expected, code):
    calls = []

    def handler(request):
        calls.append(request)
        if isinstance(data, str):
            return httpx.Response(status, text=data)
        return httpx.Response(status, json=data, headers={"location": "https://evil.com"})

    result = asyncio.run(send_digest(config, payload(config), transport=httpx.MockTransport(handler)))
    assert (result.status, result.error_code) == (expected, code)
    assert len(calls) == 1
    assert SECRET not in repr(result) and HOOK not in repr(result)


def test_timeout_is_unknown_one_attempt_and_http_logging_is_scoped(config, caplog, monkeypatch):
    # Alembic fileConfig may disable pre-existing loggers in the full suite.
    monkeypatch.setattr(logging.getLogger("httpx"), "disabled", False)
    monkeypatch.setattr(logging.getLogger("httpx"), "propagate", True)
    calls = []

    def handler(request):
        calls.append(request)
        logging.getLogger("httpx").warning("private %s %s", HOOK, SECRET)
        logging.getLogger("httpcore.http11").debug("private %s %s", HOOK, SECRET)
        raise httpx.ReadTimeout(SECRET + HOOK, request=request)

    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(send_digest(config, payload(config), transport=httpx.MockTransport(handler)))
        logging.getLogger("httpx").warning("unrelated request remains observable")
    assert (result.status, result.error_code) == ("unknown", "transport_uncertain")
    assert len(calls) == 1
    assert SECRET not in caplog.text and HOOK not in caplog.text
    assert "unrelated request remains observable" in caplog.text


def test_disabled_empty_and_oversize_messages_do_not_touch_network(config):
    def forbidden(request):
        raise AssertionError("network should not be called")

    transport = httpx.MockTransport(forbidden)
    assert asyncio.run(send_digest(replace(config, enabled=False), payload(config), transport=transport)).status == "skipped"
    assert asyncio.run(send_digest(config, None, transport=transport)).status == "skipped"
    result = asyncio.run(send_digest(config, {"msg_type": "post", "content": "字" * MAX_REQUEST_BYTES}, transport=transport))
    assert (result.status, result.error_code) == ("failed", "payload_too_large")
