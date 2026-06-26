"""阅读器 AI 服务 (src/services/reader_ai.py)

为用户面（阅读器）提供两项 AI 能力，复用项目既有的 OpenAI 兼容 LLM 客户端：

- translate_article: 整篇正文一键译为简体中文，结果缓存进 extensions_json 复用（省 token）。
- answer_question: 基于给定上下文回答读者提问（上下文由调用方按三档策略组装）。

本模块只负责「正文/上下文 → LLM → 文本」与翻译缓存，不直接依赖 FastAPI/向量层：
问答的上下文组装（当前文章 / 订阅列表 / RAG 召回）由 API 层完成后传入，避免循环依赖。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List, Optional

from config import LLMConfig
from llm import prompts
from llm.client import ChatMessage, chat_completion

# 译文缓存在 ArticleRecord.extensions_json 下的键；只新增此键，不触碰正文，
# 因此不影响向量化状态（向量化只在 content/title 变更时重置）。
TRANSLATION_KEY = "translation_zh"

# 单段翻译的字符上限（按段落切分后并发翻译再拼接，避免超出模型上下文/输出窗口）。
_TRANSLATE_SEGMENT_CHARS = 3500
# 列表问答上下文：单篇正文截断与整体字符上限、最多纳入的文章数。
_LIST_PER_ARTICLE_CHARS = 1500
_LIST_TOTAL_CHARS = 12000
LIST_MAX_ARTICLES = 25

# 多轮对话：最多带入的历史消息条数（user/assistant 计）与单条字符上限，控制 token 预算。
MAX_HISTORY_MESSAGES = 8
_HISTORY_CONTENT_CHARS = 2000


class ReaderAIError(Exception):
    """阅读器 AI 业务错误，携带建议的 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ==================== 全文翻译 ====================
def _split_for_translation(body: str, *, segment_chars: int = _TRANSLATE_SEGMENT_CHARS) -> List[str]:
    """按段落边界把长正文切成若干段，每段尽量不超过 segment_chars。"""
    body = body.strip()
    if len(body) <= segment_chars:
        return [body]
    segments: List[str] = []
    buffer = ""
    for para in body.split("\n\n"):
        candidate = f"{buffer}\n\n{para}" if buffer else para
        if len(candidate) <= segment_chars:
            buffer = candidate
            continue
        if buffer:
            segments.append(buffer)
        # 单段落本身就超长：硬切。
        while len(para) > segment_chars:
            segments.append(para[:segment_chars])
            para = para[segment_chars:]
        buffer = para
    if buffer:
        segments.append(buffer)
    return segments


async def _translate_segment(title: str, segment: str, llm_config: LLMConfig) -> str:
    raw = await chat_completion(
        messages=[
            ChatMessage(role="system", content=prompts.TRANSLATE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompts.build_translate_user_prompt(title, segment)),
        ],
        config=llm_config,
    )
    return raw.strip()


async def translate_article(db_sink, article_id: str, llm_config: LLMConfig) -> dict:
    """翻译指定文章正文为中文；命中缓存直接返回，否则翻译后写回 extensions_json。

    返回 {"translation": str, "cached": bool}。
    """
    record = await db_sink.get(article_id)
    if record is None:
        raise ReaderAIError("文章不存在", status_code=404)
    body = (record.content or "").strip()
    if not body:
        raise ReaderAIError("该文章暂无可翻译的正文", status_code=400)

    try:
        ext = json.loads(record.extensions_json or "{}")
        if not isinstance(ext, dict):
            ext = {}
    except (ValueError, TypeError):
        ext = {}

    cached = ext.get(TRANSLATION_KEY)
    if isinstance(cached, str) and cached.strip():
        return {"translation": cached, "cached": True}

    segments = _split_for_translation(body)
    if len(segments) == 1:
        translated = await _translate_segment(record.title or "", segments[0], llm_config)
    else:
        concurrency = max(1, getattr(llm_config, "map_concurrency", 4))
        semaphore = asyncio.Semaphore(concurrency)

        async def _guarded(seg: str) -> str:
            async with semaphore:
                return await _translate_segment(record.title or "", seg, llm_config)

        parts = await asyncio.gather(*[_guarded(seg) for seg in segments])
        translated = "\n\n".join(p for p in parts if p)

    translated = translated.strip()
    if not translated:
        raise ReaderAIError("翻译失败，请稍后重试", status_code=502)

    ext[TRANSLATION_KEY] = translated
    await db_sink.update(article_id, {"extensions_json": json.dumps(ext, ensure_ascii=False)})
    return {"translation": translated, "cached": False}


# ==================== 文章问答 ====================
def build_article_context(title: str, body: str, *, max_chars: int = _LIST_TOTAL_CHARS) -> str:
    """单篇文章问答的上下文：标题 + 正文（必要时截断）。"""
    body = (body or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n...(正文已截断)"
    return f"# {title or '（无标题）'}\n\n{body}".strip()


def build_list_context(
    articles: List[Any],
    *,
    per_article_chars: int = _LIST_PER_ARTICLE_CHARS,
    total_chars: int = _LIST_TOTAL_CHARS,
) -> str:
    """多篇文章问答的上下文：逐篇「标题 + 截断正文」，整体不超过 total_chars。

    articles 元素可为 dict（含 title/content）或带 .title/.content 属性的对象。
    """
    blocks: List[str] = []
    used = 0
    for art in articles:
        if isinstance(art, dict):
            title = art.get("title") or "（无标题）"
            body = (art.get("content") or art.get("content_preview") or "").strip()
        else:
            title = getattr(art, "title", None) or "（无标题）"
            body = (getattr(art, "content", None) or "").strip()
        if not body:
            continue
        if len(body) > per_article_chars:
            body = body[:per_article_chars] + "…"
        block = f"## {title}\n{body}"
        if used + len(block) > total_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def _sanitize_history(history: Optional[List[Any]]) -> List[ChatMessage]:
    """把前端传入的历史消息清洗为合法的 user/assistant 轮次。

    只保留 role∈{user,assistant} 且 content 非空的条目，单条截断、整体取最近若干条，
    防止注入异常 role / 超长上下文。历史轮次只含纯文本问答，不携带各自当时的参考资料
    （当前问题的参考资料由调用方单独附在最后一条 user 消息上）。
    """
    if not history:
        return []
    cleaned: List[ChatMessage] = []
    for item in history:
        if isinstance(item, dict):
            role = item.get("role")
            content = item.get("content")
        else:
            role = getattr(item, "role", None)
            content = getattr(item, "content", None)
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        if len(content) > _HISTORY_CONTENT_CHARS:
            content = content[:_HISTORY_CONTENT_CHARS] + "…"
        cleaned.append(ChatMessage(role=role, content=content))
    return cleaned[-MAX_HISTORY_MESSAGES:]


async def answer_question(
    question: str,
    context: str,
    *,
    scope: str,
    llm_config: LLMConfig,
    history: Optional[List[Any]] = None,
) -> str:
    """基于给定上下文 + 多轮历史回答提问。上下文为空时仍调用，由提示词约束模型如实说明资料不足。

    消息结构：system → 历史轮次（纯文本问答）→ 当前问题（附本轮参考资料）。
    历史让模型支持「追问/指代」，参考资料只随当前问题刷新，token 预算可控。
    """
    question = (question or "").strip()
    if not question:
        raise ReaderAIError("请输入你的问题", status_code=400)
    messages = [ChatMessage(role="system", content=prompts.QA_SYSTEM_PROMPT)]
    messages.extend(_sanitize_history(history))
    messages.append(
        ChatMessage(
            role="user",
            content=prompts.build_qa_user_prompt(question, context, scope=scope),
        )
    )
    raw = await chat_completion(messages=messages, config=llm_config)
    answer = raw.strip()
    if not answer:
        raise ReaderAIError("AI 暂时没有返回内容，请稍后重试", status_code=502)
    return answer
