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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from config import LLMConfig

logger = logging.getLogger("dorami.llm")


@dataclass
class UsageMeta:
    """一次 LLM 调用的计量标签：用途 + 归属用户（系统任务用 None/"system"）。"""
    purpose: str  # translate / ask / daily_brief_map / daily_brief_dedup / daily_brief_reduce / source_config / detail_profile
    username: Optional[str] = None


# 计量回调：fn(meta, usage_dict, model)。由上层（app.py）注册写库实现，
# 避免本模块直接依赖 db/models（保持分层）。recorder 内异常一律吞掉，绝不阻断主流程。
_usage_recorder: Optional[Callable[[UsageMeta, Dict[str, Any], str], None]] = None


def set_usage_recorder(fn: Optional[Callable[[UsageMeta, Dict[str, Any], str], None]]) -> None:
    global _usage_recorder
    _usage_recorder = fn


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


@asynccontextmanager
async def client_session(config: LLMConfig):
    """按 config 建一个可跨多次 chat_completion 复用的 HTTP 连接池。

    高频调用段(日报 map 一晚上百次、翻译分段并发、ask 的规划→选篇→作答三连)
    用它包住整段并把 client 经 ``http_client`` 传入 chat_completion,避免逐次
    调用重建连接/TLS 握手;不传时 chat_completion 仍每次自建短连接(行为不变)。
    """
    async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=True) as client:
        yield client


async def chat_completion(
    *,
    messages: List[ChatMessage],
    config: LLMConfig,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_json: bool = False,
    max_retries: int = 3,
    usage_meta: Optional[UsageMeta] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> str:
    """调用 chat completions，返回 choices[0].message.content。

    - 429 / 5xx / 网络错误：指数退避重试（1s, 2s, 4s ...）。
    - 其它 4xx：直接抛 LLMError（不重试）。
    - response_json=True：附带 response_format={"type":"json_object"}；若端点不
      支持（返回 400），自动去掉该字段重试一次（degrade gracefully）。
    - usage_meta：提供时把响应里的 token usage 交给已注册的计量 recorder（可选、不阻断）。
    - http_client：可选的共享连接（见 client_session）；传入时本函数不负责其生命周期。
    """
    if not config.configured:
        raise LLMNotConfigured("LLM 未配置（需 base_url / api_key / model）")

    if http_client is not None:
        return await _chat_completion_on(
            http_client, messages=messages, config=config, temperature=temperature,
            max_tokens=max_tokens, response_json=response_json,
            max_retries=max_retries, usage_meta=usage_meta,
        )
    async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=True) as client:
        return await _chat_completion_on(
            client, messages=messages, config=config, temperature=temperature,
            max_tokens=max_tokens, response_json=response_json,
            max_retries=max_retries, usage_meta=usage_meta,
        )


async def _chat_completion_on(
    client: httpx.AsyncClient,
    *,
    messages: List[ChatMessage],
    config: LLMConfig,
    temperature: Optional[float],
    max_tokens: Optional[int],
    response_json: bool,
    max_retries: int,
    usage_meta: Optional[UsageMeta],
) -> str:
    url = _endpoint(config)
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    def _build_payload(with_json: bool, with_thinking: bool) -> dict:
        payload: dict = {
            "model": config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": config.temperature if temperature is None else temperature,
            "max_tokens": config.max_tokens if max_tokens is None else max_tokens,
        }
        if with_json:
            payload["response_format"] = {"type": "json_object"}
        if with_thinking:
            # OpenAI 兼容格式的思考模式参数(DeepSeek V4 系文档口径):
            # disabled → thinking.type=disabled;low/high/max → 开启 + reasoning_effort。
            mode = config.thinking_mode.strip().lower()
            if mode == "disabled":
                payload["thinking"] = {"type": "disabled"}
            elif mode in ("low", "high", "max"):
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = mode
        return payload

    want_json = response_json
    want_thinking = bool(config.thinking_mode.strip())
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = await client.post(url, headers=headers, json=_build_payload(want_json, want_thinking))
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning("LLM 请求异常 (%s/%s) [%s | %s]: %s",
                           attempt, max_retries, config.base_url, config.model, exc)
            if attempt == max_retries:
                raise LLMError(f"LLM 请求失败: {exc}") from exc
            await asyncio.sleep(2 ** (attempt - 1))
            continue

        if resp.status_code == 200:
            content, usage, finish_reason = _extract_content_and_usage(resp)
            if finish_reason == "length":
                logger.warning("LLM 输出触及 max_tokens 上限被截断 [%s | %s]——考虑调大 max_tokens 或关闭思考模式",
                               config.base_url, config.model)
            _maybe_record_usage(usage_meta, usage, config.model)
            return content

        body_preview = resp.text[:500]
        # response_format 不被支持时，去掉后重试一次
        if resp.status_code == 400 and want_json:
            logger.info("端点疑似不支持 response_format，降级为普通模式重试 [%s | %s]",
                        config.base_url, config.model)
            want_json = False
            continue

        # 思考模式参数不被支持时，去掉后重试一次(配置是 opt-in,但换端点后旧覆盖可能残留)
        if resp.status_code == 400 and want_thinking:
            logger.warning("端点疑似不支持思考模式参数(thinking/reasoning_effort)，去掉后重试 [%s | %s]",
                           config.base_url, config.model)
            want_thinking = False
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


def _extract_content_and_usage(resp: httpx.Response) -> tuple[str, Dict[str, Any], Optional[str]]:
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"LLM 响应非 JSON: {resp.text[:300]}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM 响应缺少 choices/message/content: {str(data)[:300]}") from exc
    finish_reason = None
    if isinstance(data.get("choices"), list) and data["choices"]:
        raw_reason = data["choices"][0].get("finish_reason")
        finish_reason = str(raw_reason) if raw_reason is not None else None
    # 空串与 None 同判:思考型模型把输出配额耗尽在思考里时,content 是 "" 而非 None——
    # 静默放行曾让空日报以 status=success 落库(2026-08 生产事故),必须当错误抛出。
    if content is None or not str(content).strip():
        raise LLMError(
            f"LLM 返回空内容(finish_reason={finish_reason})——"
            "若为思考型模型,考虑调大 max_tokens 或将思考模式设为 disabled"
        )
    usage = data.get("usage") if isinstance(data, dict) else None
    return content, (usage if isinstance(usage, dict) else {}), finish_reason


def _maybe_record_usage(
    meta: Optional[UsageMeta], usage: Dict[str, Any], model: str
) -> None:
    """把一次调用的 token 用量交给已注册的 recorder；计量绝不阻断主流程。"""
    recorder = _usage_recorder
    if meta is None or recorder is None:
        return
    try:
        recorder(meta, usage or {}, model)
    except Exception as exc:  # noqa: BLE001
        # 计量失败不阻断主流程，但升级为 warning 以便可见（仅异常摘要，无敏感字段）。
        logger.warning("usage recorder 异常（忽略）: %s", exc)


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
