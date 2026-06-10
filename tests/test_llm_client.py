import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import LLMConfig  # noqa: E402
from llm import client as llm_client  # noqa: E402
from llm.client import ChatMessage, LLMError, LLMNotConfigured, chat_completion, parse_json_object  # noqa: E402


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


def test_endpoint_normalization():
    cfg = LLMConfig(base_url="https://h/v1/chat/completions", api_key="k", model="m")
    assert llm_client._endpoint(cfg) == "https://h/v1/chat/completions"
    cfg2 = LLMConfig(base_url="https://h/v1/", api_key="k", model="m")
    assert llm_client._endpoint(cfg2) == "https://h/v1/chat/completions"
