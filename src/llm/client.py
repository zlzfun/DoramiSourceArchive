"""OpenAI 兼容大模型客户端 (src/llm/client.py)

只依赖 httpx，统一走 {base_url}/chat/completions，覆盖 OpenAI/DeepSeek/Kimi/
智谱/通义/火山方舟/OpenRouter/Ollama/vLLM 等。提供：
- chat_completion: 异步对话补全 + 指数退避重试 + 可选 JSON 模式
- parse_json_object: 鲁棒解析「纯 JSON」输出（去围栏、截取首尾大括号）
- ping: 测试连接

机密安全：函数只接收 LLMConfig，日志只打 base_url/model，绝不打印 api_key。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import httpx

from config import LLMConfig

logger = logging.getLogger("dorami.llm")


class LLMError(Exception):
    """大模型调用或响应解析失败。"""


class LLMNotConfigured(LLMError):
    """大模型未配置（缺 base_url/api_key/model）。"""


@dataclass
class ChatMessage:
    role: str  # system / user / assistant
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


def _endpoint(config: LLMConfig) -> str:
    """规范化 chat completions 端点。

    用户可能填到 https://host/v1 或 https://host/v1/ ，统一拼成
    .../chat/completions；若已带 /chat/completions 则原样使用。
    """
    base = (config.base_url or "").strip().rstrip("/")
    if not base:
        raise LLMNotConfigured("LLM base_url 未配置")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


async def chat_completion(
    *,
    messages: List[ChatMessage],
    config: LLMConfig,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_json: bool = False,
    max_retries: int = 3,
) -> str:
    """调用 chat completions，返回 choices[0].message.content。

    - 429 / 5xx / 网络错误：指数退避重试（1s, 2s, 4s ...）。
    - 其它 4xx：直接抛 LLMError（不重试）。
    - response_json=True：附带 response_format={"type":"json_object"}；若端点不
      支持（返回 400），自动去掉该字段重试一次（degrade gracefully）。
    """
    if not config.configured:
        raise LLMNotConfigured("LLM 未配置（需 base_url / api_key / model）")

    url = _endpoint(config)
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    def _build_payload(with_json: bool) -> dict:
        payload: dict = {
            "model": config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": config.temperature if temperature is None else temperature,
            "max_tokens": config.max_tokens if max_tokens is None else max_tokens,
        }
        if with_json:
            payload["response_format"] = {"type": "json_object"}
        return payload

    want_json = response_json
    last_error: Optional[Exception] = None

    async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=True) as client:
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.post(url, headers=headers, json=_build_payload(want_json))
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("LLM 请求异常 (%s/%s) [%s | %s]: %s",
                               attempt, max_retries, config.base_url, config.model, exc)
                if attempt == max_retries:
                    raise LLMError(f"LLM 请求失败: {exc}") from exc
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            if resp.status_code == 200:
                return _extract_content(resp)

            body_preview = resp.text[:500]
            # response_format 不被支持时，去掉后重试一次
            if resp.status_code == 400 and want_json:
                logger.info("端点疑似不支持 response_format，降级为普通模式重试 [%s | %s]",
                            config.base_url, config.model)
                want_json = False
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = LLMError(f"HTTP {resp.status_code}: {body_preview}")
                logger.warning("LLM 响应可重试 (%s/%s) HTTP %s [%s | %s]",
                               attempt, max_retries, resp.status_code, config.base_url, config.model)
                if attempt == max_retries:
                    raise last_error
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            # 其它 4xx：不重试
            raise LLMError(f"LLM 调用失败 HTTP {resp.status_code}: {body_preview}")

    raise LLMError(f"LLM 请求失败: {last_error}")


def _extract_content(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"LLM 响应非 JSON: {resp.text[:300]}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM 响应缺少 choices/message/content: {str(data)[:300]}") from exc
    if content is None:
        raise LLMError("LLM 返回空内容")
    return content


def parse_json_object(text: str) -> dict:
    """鲁棒解析模型输出的 JSON 对象。

    处理 ```json 围栏、前后多余文字：截取首个 '{' 到末个 '}' 之间内容后 json.loads。
    """
    if not text:
        raise LLMError("待解析文本为空")
    cleaned = text.strip()
    # 去掉 markdown 代码围栏
    if cleaned.startswith("```"):
        cleaned = cleaned.lstrip("`")
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    # 截取首尾大括号
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError(f"未找到 JSON 对象: {text[:200]}")
    snippet = cleaned[start:end + 1]
    try:
        result = json.loads(snippet)
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON 解析失败: {exc} | 原文: {snippet[:200]}") from exc
    if not isinstance(result, dict):
        raise LLMError("解析结果不是 JSON 对象")
    return result


async def ping(config: LLMConfig) -> dict:
    """测试连接：发一条极短 prompt，返回 {ok, model, latency_ms, sample}。"""
    if not config.configured:
        raise LLMNotConfigured("LLM 未配置（需 base_url / api_key / model）")
    started = time.monotonic()
    content = await chat_completion(
        messages=[ChatMessage(role="user", content="ping，请只回复 pong")],
        config=config,
        max_tokens=16,
        max_retries=1,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": True,
        "model": config.model,
        "latency_ms": latency_ms,
        "sample": (content or "").strip()[:120],
    }
