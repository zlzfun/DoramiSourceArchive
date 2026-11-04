import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import LLMConfig  # noqa: E402
from llm import client as llm_client  # noqa: E402
from llm.client import (  # noqa: E402
    ChatMessage,
    LLMError,
    LLMNotConfigured,
    LLMStructuredOutputError,
    chat_completion,
    parse_json_object,
)


CONFIGURED = LLMConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test-model")


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text if text is not None else (json.dumps(json_data) if json_data is not None else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


def _chat_ok(content):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


class _FakeAsyncClient:
    """按序返回预设响应；记录每次 post 的 payload，供断言。"""

    instances = []

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        _FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_client(monkeypatch, responses):
    _FakeAsyncClient.instances = []

    def _factory(*args, **kwargs):
        return _FakeAsyncClient(responses)

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", _factory)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(llm_client.asyncio, "sleep", _no_sleep)


def test_chat_completion_success(monkeypatch):
    _patch_client(monkeypatch, [_chat_ok("hello")])
    out = asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=CONFIGURED))
    assert out == "hello"
    call = _FakeAsyncClient.instances[0].calls[0]
    assert call["url"] == "https://api.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"]["model"] == "test-model"


def test_chat_completion_retries_on_429(monkeypatch):
    _patch_client(monkeypatch, [_FakeResponse(429, text="rate limited"), _chat_ok("after-retry")])
    out = asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=CONFIGURED, max_retries=3))
    assert out == "after-retry"
    assert len(_FakeAsyncClient.instances[0].calls) == 2


def test_chat_completion_4xx_raises(monkeypatch):
    _patch_client(monkeypatch, [_FakeResponse(401, text="unauthorized")])
    with pytest.raises(LLMError):
        asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=CONFIGURED))


def test_response_format_degrade(monkeypatch):
    # response_json=True 首次 400（端点不支持 response_format），去掉后重试成功
    _patch_client(monkeypatch, [_FakeResponse(400, text="response_format unsupported"), _chat_ok("ok-json-off")])
    out = asyncio.run(chat_completion(
        messages=[ChatMessage("user", "hi")], config=CONFIGURED, response_json=True
    ))
    assert out == "ok-json-off"
    calls = _FakeAsyncClient.instances[0].calls
    assert "response_format" in calls[0]["json"]
    assert "response_format" not in calls[1]["json"]


def test_thinking_mode_disabled_payload(monkeypatch):
    # thinking_mode=disabled → payload 带 thinking.type=disabled,不带 reasoning_effort
    cfg = LLMConfig(base_url="https://api.example.com/v1", api_key="sk-test",
                    model="test-model", thinking_mode="disabled")
    _patch_client(monkeypatch, [_chat_ok("ok")])
    asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=cfg))
    payload = _FakeAsyncClient.instances[0].calls[0]["json"]
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload


def test_thinking_mode_effort_payload(monkeypatch):
    # thinking_mode=low → 开启思考 + reasoning_effort=low
    cfg = LLMConfig(base_url="https://api.example.com/v1", api_key="sk-test",
                    model="test-model", thinking_mode="low")
    _patch_client(monkeypatch, [_chat_ok("ok")])
    asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=cfg))
    payload = _FakeAsyncClient.instances[0].calls[0]["json"]
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "low"


def test_thinking_mode_default_absent(monkeypatch):
    # 默认(空)不发送任何思考参数——兼容不支持该参数的端点
    _patch_client(monkeypatch, [_chat_ok("ok")])
    asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=CONFIGURED))
    payload = _FakeAsyncClient.instances[0].calls[0]["json"]
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


def test_thinking_degrade_on_400(monkeypatch):
    # 端点不支持思考参数(400)→ 去掉后重试成功
    cfg = LLMConfig(base_url="https://api.example.com/v1", api_key="sk-test",
                    model="test-model", thinking_mode="disabled")
    _patch_client(monkeypatch, [_FakeResponse(400, text="unknown param thinking"), _chat_ok("ok-degraded")])
    out = asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=cfg))
    assert out == "ok-degraded"
    calls = _FakeAsyncClient.instances[0].calls
    assert "thinking" in calls[0]["json"]
    assert "thinking" not in calls[1]["json"]


def test_empty_content_raises(monkeypatch):
    # 空串 content(思考型模型把输出配额耗尽在思考里)必须当错误抛出,不得静默放行
    resp = _FakeResponse(200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    _patch_client(monkeypatch, [resp])
    with pytest.raises(LLMError, match="空内容"):
        asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=CONFIGURED))


def test_not_configured():
    empty = LLMConfig()
    with pytest.raises(LLMNotConfigured):
        asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=empty))


def test_parse_json_object_fenced():
    assert parse_json_object("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_parse_json_object_with_surrounding_text():
    assert parse_json_object("解释：\n{\"b\": 2}\n以上。") == {"b": 2}


def test_parse_json_object_invalid():
    with pytest.raises(LLMError):
        parse_json_object("not json at all")


def test_parse_json_object_exposes_location_without_leaking_output():
    malformed = '{"summary":"private article text" "quality_score":8.2}'
    with pytest.raises(LLMStructuredOutputError) as raised:
        parse_json_object(malformed)
    error = raised.value
    assert error.code == "json_decode_error"
    assert (error.line, error.column, error.position) == (1, 35, 34)
    assert "private article text" not in str(error)


def test_endpoint_normalization():
    cfg = LLMConfig(base_url="https://h/v1/chat/completions", api_key="k", model="m")
    assert llm_client._endpoint(cfg) == "https://h/v1/chat/completions"
    cfg2 = LLMConfig(base_url="https://h/v1/", api_key="k", model="m")
    assert llm_client._endpoint(cfg2) == "https://h/v1/chat/completions"


def test_shared_http_client_reused(monkeypatch):
    # 传入 http_client 时不自建连接(工厂不被调用),请求走共享 client
    _FakeAsyncClient.instances = []

    def _factory(*args, **kwargs):
        raise AssertionError("传入 http_client 时不应自建 AsyncClient")

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", _factory)
    shared = _FakeAsyncClient([_chat_ok("one"), _chat_ok("two")])
    out1 = asyncio.run(chat_completion(
        messages=[ChatMessage("user", "hi")], config=CONFIGURED, http_client=shared))
    out2 = asyncio.run(chat_completion(
        messages=[ChatMessage("user", "again")], config=CONFIGURED, http_client=shared))
    assert (out1, out2) == ("one", "two")
    assert len(shared.calls) == 2  # 两次调用共用同一连接


def test_ping_reports_served_model_without_changing_request_policy(monkeypatch):
    cfg = LLMConfig(base_url="https://api.example.com/v1", api_key="fixture-secret",
                    model="deepseek-v4-flash", thinking_mode="disabled", max_tokens=4096,
                    aux_model="aux-private", vision_model="vision-private", temperature=0.3)
    _patch_client(monkeypatch, [_FakeResponse(200, {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
    })])
    result = asyncio.run(llm_client.ping(cfg))
    assert result["model"] == "deepseek-v4-flash"
    assert result["served_model"] == "deepseek-flash"
    assert result["sample"] == "pong"
    calls = _FakeAsyncClient.instances[0].calls
    assert len(calls) == 1
    assert calls[0]["json"]["model"] == cfg.model
    assert calls[0]["json"]["thinking"] == {"type": "disabled"}
    assert calls[0]["json"]["max_tokens"] == llm_client.PING_MAX_TOKENS
    assert calls[0]["json"]["temperature"] == 0.3
    assert cfg.max_tokens == 4096 and cfg.aux_model == "aux-private"
    assert cfg.vision_model == "vision-private"


@pytest.mark.parametrize("reported", [
    None, "", "   ", 123, {"name": "model"}, ["model"], "m" * 201,
    "model\nprivate", "model\x1b[31m", "https://private.example/audio?signature=fake",
    "https://private.example/audio", "sk-fake-provider-key", "provider/sk-fake-key",
    "prefix-fixture-secret-suffix",
])
def test_ping_omits_untrusted_model_metadata(monkeypatch, reported):
    cfg = LLMConfig(base_url="https://api.example.com/v1", api_key="fixture-secret", model="configured")
    _patch_client(monkeypatch, [_FakeResponse(200, {
        "model": reported, "choices": [{"message": {"content": "pong"}}],
    })])
    result = asyncio.run(llm_client.ping(cfg))
    assert result["ok"] is True
    assert result["model"] == "configured"
    assert "served_model" not in result
    assert "fixture-secret" not in json.dumps(result)
    assert len(_FakeAsyncClient.instances[0].calls) == 1


def test_response_meta_is_optional_and_does_not_reuse_stale_model(monkeypatch):
    # Shared clients and caller-owned metadata retain their original contracts.
    shared = _FakeAsyncClient([
        _FakeResponse(200, {"model": "org/model:v1", "choices": [{"message": {"content": "one"}}]}),
        _chat_ok("two"), _chat_ok("three"),
    ])
    meta = {"served_model": "stale", "caller_marker": "keep"}
    kwargs = dict(messages=[ChatMessage("user", "hi")], config=CONFIGURED, http_client=shared)
    assert asyncio.run(chat_completion(**kwargs, response_meta=meta)) == "one"
    assert meta == {"served_model": "org/model:v1", "caller_marker": "keep"}
    assert asyncio.run(chat_completion(**kwargs, response_meta=meta)) == "two"
    assert meta == {"caller_marker": "keep"}
    assert asyncio.run(chat_completion(**kwargs)) == "three"
    assert len(shared.calls) == 3


@pytest.mark.parametrize("content,finish_reason,require_complete", [
    ("", "stop", False), ("partial", "length", True),
])
def test_failed_completion_does_not_publish_served_model(monkeypatch, content, finish_reason, require_complete):
    _patch_client(monkeypatch, [_FakeResponse(200, {
        "model": "new-model",
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
    })])
    meta = {"served_model": "stale"}
    with pytest.raises(LLMError):
        asyncio.run(chat_completion(messages=[ChatMessage("user", "hi")], config=CONFIGURED,
                                    require_complete=require_complete, response_meta=meta))
    assert meta == {}
