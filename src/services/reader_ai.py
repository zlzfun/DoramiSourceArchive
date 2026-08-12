"""阅读器 AI 服务 (src/services/reader_ai.py)

为用户面（阅读器）提供两项 AI 能力，复用项目既有的 OpenAI 兼容 LLM 客户端：

- translate_article: 整篇正文一键译为简体中文，结果缓存进 extensions_json 复用（省 token）。
- summarize_article: 生成 2~3 句中文要点摘要（阅读入口的「AI 总结」卡 + 列表卡摘要），
  缓存约定与翻译完全一致（extensions_json.summary_zh）。
- answer_question: 基于给定上下文回答读者提问（上下文按 scope 组装，见 assemble_reader_context）。

本模块只负责「正文/上下文 → LLM → 文本」与翻译缓存，不直接依赖 FastAPI/检索层：
问答范围四档（article/articles 显式名单、subscription/all 检索圈定）统一收敛到
「文章名单 → 编号上下文 + sources」的核心层（v3.32），检索管线经 search_fetch 闭包注入。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, List, Optional

from config import LLMConfig
from llm import prompts
from llm.client import ChatMessage, UsageMeta, chat_completion

# 译文/摘要缓存在 ArticleRecord.extensions_json 下的键；只新增键，不触碰正文。
TRANSLATION_KEY = "translation_zh"
SUMMARY_KEY = "summary_zh"

# 摘要输入正文的字符上限（要点摘要不需要全文,开头已承载主旨;超长截断省 token）。
_SUMMARIZE_BODY_CHARS = 12000

# 单段翻译的字符上限（按段落切分后并发翻译再拼接，避免超出模型上下文/输出窗口）。
_TRANSLATE_SEGMENT_CHARS = 3500
# 列表问答上下文：单篇正文截断与整体字符上限。
_LIST_PER_ARTICLE_CHARS = 1500
_LIST_TOTAL_CHARS = 12000

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


async def _translate_segment(
    title: str, segment: str, llm_config: LLMConfig, usage_meta: Optional[UsageMeta] = None
) -> str:
    raw = await chat_completion(
        messages=[
            ChatMessage(role="system", content=prompts.TRANSLATE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompts.build_translate_user_prompt(title, segment)),
        ],
        config=llm_config,
        usage_meta=usage_meta,
    )
    return raw.strip()


async def translate_article(
    db_sink, article_id: str, llm_config: LLMConfig, usage_meta: Optional[UsageMeta] = None
) -> dict:
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
        translated = await _translate_segment(record.title or "", segments[0], llm_config, usage_meta)
    else:
        concurrency = max(1, getattr(llm_config, "map_concurrency", 4))
        semaphore = asyncio.Semaphore(concurrency)

        async def _guarded(seg: str) -> str:
            async with semaphore:
                return await _translate_segment(record.title or "", seg, llm_config, usage_meta)

        parts = await asyncio.gather(*[_guarded(seg) for seg in segments])
        translated = "\n\n".join(p for p in parts if p)

    translated = translated.strip()
    if not translated:
        raise ReaderAIError("翻译失败，请稍后重试", status_code=502)

    ext[TRANSLATION_KEY] = translated
    await db_sink.update(article_id, {"extensions_json": json.dumps(ext, ensure_ascii=False)})
    return {"translation": translated, "cached": False}


# ==================== 要点摘要 ====================
def _load_extensions(record) -> dict:
    try:
        ext = json.loads(record.extensions_json or "{}")
        return ext if isinstance(ext, dict) else {}
    except (ValueError, TypeError):
        return {}


async def summarize_article(
    db_sink, article_id: str, llm_config: LLMConfig, usage_meta: Optional[UsageMeta] = None
) -> dict:
    """为指定文章生成中文要点摘要；命中缓存直接返回，否则生成后写回 extensions_json。

    返回 {"summary": str, "cached": bool}。缓存/写回约定与 translate_article 一致。
    """
    record = await db_sink.get(article_id)
    if record is None:
        raise ReaderAIError("文章不存在", status_code=404)
    body = (record.content or "").strip()
    if not body:
        raise ReaderAIError("该文章暂无可总结的正文", status_code=400)

    ext = _load_extensions(record)
    cached = ext.get(SUMMARY_KEY)
    if isinstance(cached, str) and cached.strip():
        return {"summary": cached, "cached": True}

    if len(body) > _SUMMARIZE_BODY_CHARS:
        body = body[:_SUMMARIZE_BODY_CHARS] + "\n...(正文已截断)"
    raw = await chat_completion(
        messages=[
            ChatMessage(role="system", content=prompts.SUMMARIZE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompts.build_summarize_user_prompt(record.title or "", body)),
        ],
        config=llm_config,
        usage_meta=usage_meta,
    )
    summary = raw.strip()
    if not summary:
        raise ReaderAIError("摘要生成失败，请稍后重试", status_code=502)

    ext[SUMMARY_KEY] = summary
    await db_sink.update(article_id, {"extensions_json": json.dumps(ext, ensure_ascii=False)})
    return {"summary": summary, "cached": False}


# ==================== 文章问答 ====================
# 核心层唯一出口（v3.32 阅读面 AI 打磨波）：「给定文章名单 → 编号上下文 + sources」。
# 各 scope 只是取得名单的方式：article/articles 显式指定（n=1 是特例），
# subscription/all 经检索管线圈定。编号 [n] 同时是提示词里的引用锚——回答中的
# 行内引用标记与末尾出处列表都以它对齐，故 context 与 sources 必须同源同序。

# 显式多篇（scope=articles）单次上限：护 token 预算，也是未来「多选问答/收藏集问答」的粒度。
EXPLICIT_ARTICLES_MAX = 12


def _art_field(art: Any, key: str) -> Any:
    """文章条目字段读取：与 build_numbered_context 同契约（dict 或对象皆可）。"""
    if isinstance(art, dict):
        return art.get(key)
    return getattr(art, key, None)


def build_sources_payload(records: List[Any]) -> List[dict]:
    """文章记录 → 问答响应的 sources 载荷（与编号上下文同序，序号即引用锚）。

    ``id`` 供前端行内引用 chip / 出处列表做站内跳转（v3.25 深链落地同路），
    ``source_name``/``publish_date`` 供出处行展示。"""
    from services.source_naming import friendly_source_name

    return [
        {
            "id": _art_field(r, "id"),
            "title": _art_field(r, "title"),
            "source_id": _art_field(r, "source_id"),
            "source_name": friendly_source_name(_art_field(r, "source_id") or ""),
            "source_url": _art_field(r, "source_url"),
            "publish_date": (_art_field(r, "publish_date") or "")[:10],
        }
        for r in records
    ]


def build_numbered_context(
    articles: List[Any],
    *,
    per_article_chars: int = _LIST_PER_ARTICLE_CHARS,
    total_chars: int = _LIST_TOTAL_CHARS,
    keep_empty: bool = False,
) -> tuple:
    """多篇文章 → 编号上下文，返回 ``(context, included)``。

    逐篇「[n] 标题 + 截断正文」拼接，整体不超过 total_chars。**编号只对实际
    进入上下文的文章分配**（空正文默认跳过、超预算截断），included 即这份
    名单——sources 必须由它构建，否则回答里的 [n] 会指错文章。
    keep_empty=True 时空正文也保留条目（单篇场景标题本身就是信息）。
    articles 元素可为 dict（含 title/content）或带 .title/.content 属性的对象。
    """
    blocks: List[str] = []
    included: List[Any] = []
    used = 0
    from services.source_naming import friendly_source_name

    for art in articles:
        title = _art_field(art, "title") or "（无标题）"
        if isinstance(art, dict):
            body = (art.get("content") or art.get("content_preview") or "").strip()
        else:
            body = (getattr(art, "content", None) or "").strip()
        if not body and not keep_empty:
            continue
        if len(body) > per_article_chars:
            body = body[:per_article_chars] + "…"
        # 块头带来源名与发布日期(时效性场景的作答依据:「最近」类问题须能按日期
        # 甄别与标注;正文里往往没有自身的发布日期)。
        source_name = friendly_source_name(_art_field(art, "source_id") or "")
        publish_date = (_art_field(art, "publish_date") or "")[:10]
        meta = " | ".join(part for part in (source_name, publish_date) if part)
        header = f"[{len(included) + 1}] {title}" + (f" | {meta}" if meta else "")
        block = f"{header}\n{body}".rstrip()
        # 超预算即止；但至少收一篇，避免「单篇预算=总预算」的边界产出空上下文
        if included and used + len(block) > total_chars:
            break
        blocks.append(block)
        included.append(art)
        used += len(block)
    return "\n\n".join(blocks), included


def assemble_articles_context(records: List[Any], *, total_chars: int = _LIST_TOTAL_CHARS) -> tuple:
    """显式名单的上下文组装：单篇预算按篇数摊分（n=1 独享全额），返回 (context, sources)。"""
    per_article = max(_LIST_PER_ARTICLE_CHARS, total_chars // max(1, len(records)))
    context, included = build_numbered_context(
        records, per_article_chars=per_article, total_chars=total_chars,
        keep_empty=(len(records) == 1),
    )
    return context, build_sources_payload(included)


async def assemble_reader_context(
    *,
    scope: str,
    question: str,
    article_id: Optional[str],
    username: str,
    db_sink: Any,
    search_fetch: Any,
    article_ids: Optional[List[str]] = None,
) -> tuple:
    """按 scope 组装 reader 问答上下文，返回 ``(context, sources)``。

    - ``scope=article``：该文正文（显式名单 n=1 的特例，零检索依赖）；
    - ``scope=articles``：显式多篇 ``article_ids``（≤ EXPLICIT_ARTICLES_MAX，
      缺失的 id 跳过、全部缺失 404）——供上层功能圈定任意一批文章后问答；
    - ``scope=subscription`` / ``scope=all``：``await search_fetch(question, username)``
      走「LLM 计划检索 + FTS5」两段式（services/reader_search，降级链管线内自持）；
      两档的差异只在调用方闭包里解析的 source_ids（订阅域 vs 全库可见源）。

    ``search_fetch``（async，取 ``(question, username)`` → ``(context, sources)``）由
    调用方注入：API 层用闭包承载鉴权作用域与 LLM 配置，worker/CLI 可注入无请求
    版本——从而本组装逻辑与 HTTP 请求解耦、可独立单测复用（阶段4 D11 编排下沉）。
    """
    if scope == "article":
        if not article_id:
            raise ReaderAIError("缺少文章标识", status_code=400)
        record = await db_sink.get(article_id)
        if record is None:
            raise ReaderAIError("文章不存在", status_code=404)
        return assemble_articles_context([record])

    if scope == "articles":
        ids: List[str] = []
        for raw in article_ids or []:
            value = (raw or "").strip()
            if value and value not in ids:
                ids.append(value)
        if not ids:
            raise ReaderAIError("缺少文章标识", status_code=400)
        records = []
        for value in ids[:EXPLICIT_ARTICLES_MAX]:
            record = await db_sink.get(value)
            if record is not None:
                records.append(record)
        if not records:
            raise ReaderAIError("文章不存在", status_code=404)
        return assemble_articles_context(records)

    return await search_fetch(question, username)


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
    usage_meta: Optional[UsageMeta] = None,
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
            # 今天的日期随当前问题注入:「最近/本周」类时效问题须有基准才能按
            # 资料头部的发布日期甄别新旧(检索规划侧同样拿到 today)。
            content=prompts.build_qa_user_prompt(
                question, context, scope=scope,
                today=datetime.now().strftime("%Y-%m-%d"),
            ),
        )
    )
    raw = await chat_completion(messages=messages, config=llm_config, usage_meta=usage_meta)
    answer = raw.strip()
    if not answer:
        raise ReaderAIError("AI 暂时没有返回内容，请稍后重试", status_code=502)
    return answer
