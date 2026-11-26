"""A bounded observation must stop new POSTs even after retry or process restart."""

import asyncio
import datetime as dt
from dataclasses import replace

import httpx
import pytest

from config import LLMConfig
from config_bailian import BailianSpeechConfig
from llm import client as llm_client
from services.bailian_speech_client import BailianSpeechClient, BailianSpeechError


@pytest.mark.parametrize("deadline", ["invalid", "2020-01-01T00:00:00Z", "2999-01-01T00:00:00"])
def test_llm_bad_or_expired_fence_never_posts(deadline):
    requests = []
    config = LLMConfig(base_url="https://example.com", api_key="test", model="test", paid_authorized_until=deadline)

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: requests.append(req))) as client:
            with pytest.raises(llm_client.LLMSpendingExpired):
                await llm_client.chat_completion(messages=[], config=config, http_client=client)

    asyncio.run(check())
    assert not requests


def test_llm_rechecks_after_wait_and_before_retry(monkeypatch):
    now = dt.datetime.now(dt.timezone.utc)
    clock = [now]

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(llm_client.dt, "datetime", Clock)
    deadline = now + dt.timedelta(seconds=10)
    config = LLMConfig(base_url="https://example.com", api_key="test", model="test", paid_authorized_until=deadline.isoformat())
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(429, json={})

    async def wait(_):
        clock[0] = deadline

    monkeypatch.setattr(llm_client.asyncio, "sleep", wait)

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(llm_client.LLMSpendingExpired):
                await llm_client.chat_completion(messages=[], config=config, http_client=client, max_retries=3)

    asyncio.run(check())
    assert len(requests) == 1


def test_speech_restart_blocks_new_posts_but_keeps_polling():
    deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)
    config = BailianSpeechConfig(api_key="test", account_scope="test", asr_entitlement_ends_at=deadline.isoformat(), tts_entitlement_ends_at=deadline.isoformat())
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"output": {}})

    for _restart in range(2):
        client = BailianSpeechClient(config, transport=httpx.MockTransport(handle), clock=lambda: deadline)
        try:
            with pytest.raises(BailianSpeechError, match="spending_authorization_expired"):
                client.submit_asr("https://example.com/audio.mp3")
            with pytest.raises(BailianSpeechError, match="spending_authorization_expired"):
                client.synthesize("期限已到，不应提交。")
            client.poll_asr("existing-task")
        finally:
            client.close()
    assert [r.method for r in requests] == ["GET", "GET"]


def test_aux_and_vision_inherit_llm_deadline():
    config = LLMConfig(aux_model="aux", vision_model="vision", paid_authorized_until="2030-01-01T00:00:00Z")
    assert config.for_aux().paid_authorized_until == config.paid_authorized_until
    assert config.for_vision().paid_authorized_until == config.paid_authorized_until
