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
import base64
import json
import logging
import struct
import time
import zlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union

import httpx

from config import LLMConfig

logger = logging.getLogger("dorami.llm")


@dataclass
class UsageMeta:
    """一次 LLM 调用的计量标签：用途 + 归属用户（系统任务用 None/"system"）。"""
    purpose: str  # translate / ask / daily_brief_editorial / daily_brief_dedup / daily_brief_reduce / article_analysis / source_config / detail_profile
    username: Optional[str] = None


# 计量回调：fn(meta, usage_dict, model)。由上层（app.py）注册写库实现，
# 避免本模块直接依赖 db/models（保持分层）。recorder 内异常一律吞掉，绝不阻断主流程。
_usage_recorder: Optional[Callable[[UsageMeta, Dict[str, Any], str], None]] = None


def set_usage_recorder(fn: Optional[Callable[[UsageMeta, Dict[str, Any], str], None]]) -> None:
    global _usage_recorder
    _usage_recorder = fn


PING_MAX_TOKENS = 256


class LLMError(Exception):
    """大模型调用或响应解析失败。"""


class LLMNotConfigured(LLMError):
    """大模型未配置（缺 base_url/api_key/model）。"""


@dataclass
class ChatMessage:
    """一条对话消息。

    ``content`` 通常是纯文本;多模态调用(issue #69)时是 OpenAI 兼容的内容分片数组
    ``[{"type":"text",...},{"type":"image_url",...}]``,由 :func:`text_part` /
    :func:`image_part` 组装。图片分片**只能出现在 user 消息**(DeepSeek 文档:system /
    assistant 带图返回 400)。to_dict 对两种形态都原样透传。
    """
    role: str  # system / user / assistant
    content: Union[str, List[Dict[str, Any]]]

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}

    @property
    def text(self) -> str:
        """内容的纯文本投影(数组形态只拼 text 分片),供哈希/日志/测试用。"""
        if isinstance(self.content, str):
            return self.content
        return "\n".join(
            str(part.get("text") or "") for part in self.content
            if isinstance(part, dict) and part.get("type") == "text"
        )


def text_part(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def image_part(data_url: str, *, detail: str = "high") -> Dict[str, Any]:
    """图片分片。``data_url`` 是 ``data:image/png;base64,...`` 或公网 http(s) 链接;
    项目内一律走 base64(内网 MaaS 端点拉不到公网图链,base64 在两种部署下都成立)。
    detail:low=端侧缩到 512²;high/original=原图(DeepSeek 单图封顶 1024 token,不额外计费)。"""
    return {"type": "image_url", "image_url": {"url": data_url, "detail": detail}}


def image_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


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

    # 两类**协议兼容降级**(response_format / thinking 参数不被端点支持 → 去掉重发)各自至多一次,
    # 且不消耗传输/5xx 的重试预算:此前它们共用 attempt 计数,`max_retries=1` 的探针遇到 400 会
    # 在降级后直接耗尽循环抛「LLM 请求失败: None」(issue #69 检视 F5;vision_ping 恒显式关思考,必现)。
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = await client.post(url, headers=headers, json=_build_payload(want_json, want_thinking))
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning("LLM 请求异常 (%s/%s) [%s | %s]: %s",
                           attempt, max_retries, config.base_url, config.model, exc)
            if attempt >= max_retries:
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
        # response_format 不被支持时，去掉后重发一次(不计入重试次数)
        if resp.status_code == 400 and want_json:
            logger.info("端点疑似不支持 response_format，降级为普通模式重试 [%s | %s]",
                        config.base_url, config.model)
            want_json = False
            attempt -= 1
            continue

        # 思考模式参数不被支持时，去掉后重发一次(配置是 opt-in,但换端点后旧覆盖可能残留;不计入重试次数)
        if resp.status_code == 400 and want_thinking:
            logger.warning("端点疑似不支持思考模式参数(thinking/reasoning_effort)，去掉后重试 [%s | %s]",
                           config.base_url, config.model)
            want_thinking = False
            attempt -= 1
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = LLMError(f"HTTP {resp.status_code}: {body_preview}")
            logger.warning("LLM 响应可重试 (%s/%s) HTTP %s [%s | %s]",
                           attempt, max_retries, resp.status_code, config.base_url, config.model)
            if attempt >= max_retries:
                raise last_error
            await asyncio.sleep(2 ** (attempt - 1))
            continue

        # 其它 4xx：不重试
        raise LLMError(f"LLM 调用失败 HTTP {resp.status_code}: {body_preview}")


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
    # 独立兼容修复(与视觉无关,issue #69 验收中撞上):主模型设为默认开思考的现役模型
    # (deepseek-flash)且未配 thinking_mode 时,思考先吃掉几十 token,16 的上限让 content 空产、
    # 探针误报「连接失败」。256 是有界上限不是固定消费,实测足以越过默认思考回出正文;
    # 探针不替主模型关思考——它要验证的正是当前主模型配置的真实可用性(codex 检视 F11 拍板)。
    content = await chat_completion(
        messages=[ChatMessage(role="user", content="ping，请只回复 pong")],
        config=config,
        max_tokens=PING_MAX_TOKENS,
        max_retries=1,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": True,
        "model": config.model,
        "latency_ms": latency_ms,
        "sample": (content or "").strip()[:120],
    }


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """程序生成一张纯色 PNG(无 Pillow 依赖),供视觉连通性探针用。"""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )
    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


async def vision_ping(config: LLMConfig) -> dict:
    """视觉档连通性探针(issue #69):发一张内置的 64×64 纯红 PNG 问主色。

    调用方传入 ``config.for_vision()``。端点不认 image_url 分片会以 4xx 报错,
    经 LLMError 冒出;返回形状与 :func:`ping` 同构,多一个 ``sample``。
    """
    if not config.configured:
        raise LLMNotConfigured("LLM 未配置（需 base_url / api_key / model）")
    started = time.monotonic()
    content = await chat_completion(
        messages=[ChatMessage(role="user", content=[
            text_part("这张图的主色是什么?只回答一个颜色词。"),
            image_part(image_data_url(_solid_png(64, 64, (220, 30, 30)), "image/png"), detail="low"),
        ])],
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
