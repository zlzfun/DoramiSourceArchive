"""阅读器 AI 服务 (src/services/reader_ai.py)

为用户面（阅读器）提供两项 AI 能力，复用项目既有的 OpenAI 兼容 LLM 客户端：

- translate_article: 整篇正文一键译为简体中文，结果缓存进 extensions_json 复用（省 token）。
- translate_social_post: 一条推文（主推 + 引用推）一次调用译为中文，缓存 extensions_json.translation_zh_social（issue #84）。
- summarize_article: 生成 2~3 句中文要点摘要（阅读入口的「AI 总结」卡 + 列表卡摘要），
  缓存约定与翻译完全一致（extensions_json.summary_zh）。
- answer_question: 基于给定上下文回答读者提问（上下文按 scope 组装，见 assemble_reader_context）。

本模块只负责「正文/上下文 → LLM → 文本」与翻译缓存，不直接依赖 FastAPI/检索层：
问答范围四档（article/articles 显式名单、subscription/all 检索圈定）统一收敛到
「文章名单 → 编号上下文 + sources」的核心层（v3.32），检索管线经 search_fetch 闭包注入。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, List, Optional

from config import LLMConfig
from llm import prompts
from llm.client import (
    ChatMessage,
    LLMStructuredOutputError,
    UsageMeta,
    chat_completion,
    client_session,
    parse_json_object,
)
from sqlmodel import Session
from sqlalchemy import update

from services import image_insights as image_insights_service
from services import user_sources as user_sources_service
from services.paper_content import paper_metadata, strip_layout_markers
from services.translation_markdown import (
    TranslationStructureError, plan_translation, translation_units, restore_translation_units,
)

# 译文/摘要缓存在 ArticleRecord.extensions_json 下的键；只新增键，不触碰正文。
TRANSLATION_KEY = "translation_zh"
SUMMARY_KEY = "summary_zh"
# 缓存的正文指纹旁键(v3.34):正文被重抓/权威刷新后指纹失配即重新生成,
# 防陈旧译文/速读。存量缓存无指纹——视为有效沿用(不作废、不返工),
# 只在新一次生成时补写指纹。
TRANSLATION_FP_KEY = "translation_zh_fp"
TRANSLATION_VERSION_KEY = "translation_zh_version"
TRANSLATION_VERSION = 3
SUMMARY_FP_KEY = "summary_zh_fp"
# 中文标题(v3.45):与正文译文同缓存于 extensions_json,指纹取标题自身——标题被权威
# 刷新改写后失配即重译;存量只有正文译文的缓存命中时补译标题(一次小调用)。
TRANSLATION_TITLE_KEY = "translation_zh_title"
TRANSLATION_TITLE_FP_KEY = "translation_zh_title_fp"

logger = logging.getLogger(__name__)


def looks_chinese(text: str) -> bool:
    """粗判文本是否以中文为主:CJK 字符数 > 拉丁字母数 × 0.2(与 taxonomy_bootstrap 同启发式)。

    用途:中文标题不再送 LLM 翻译(原样即译文)。前端 utils/readerText.looksChinese 是同一
    启发式的镜像(中文源不画翻译二段),两侧阈值须保持一致。
    """
    sample = (text or "")[:2000]
    cjk = len(re.findall(r"[\u3400-\u9fff]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    return cjk > latin * 0.2


def _body_fingerprint(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:16]


def _cache_valid(ext: dict, cache_key: str, fp_key: str, fingerprint: str) -> bool:
    """缓存有效判定:文本非空,且(无指纹[存量兼容] 或 指纹与当前正文一致)。"""
    cached = ext.get(cache_key)
    if not (isinstance(cached, str) and cached.strip()):
        return False
    stored_fp = ext.get(fp_key)
    return not stored_fp or stored_fp == fingerprint

# 摘要输入正文的字符上限（要点摘要不需要全文,开头已承载主旨;超长截断省 token）。
_SUMMARIZE_BODY_CHARS = 12000

# 单段翻译的字符上限（按段落切分后并发翻译再拼接，避免超出模型上下文/输出窗口）。
_TRANSLATE_SEGMENT_CHARS = 3500
# 列表问答上下文：单篇正文截断与整体字符上限。
_LIST_PER_ARTICLE_CHARS = 1500
_LIST_TOTAL_CHARS = 12000
# 配图识别文本在单篇上下文里的上限(issue #69)= image_insights 的统一默认(6000);
# 实际取 max(1000, min(此值, 单篇正文预算))——说明最多与正文同宽、保底 1000:
# 单篇问答(正文预算 12000)给足 6000,检索档单篇 2000 时说明也有 2000。
# 1500 会把文末的图整张截掉(验收实测),故不再取「正文预算的一半」。
_NOTES_PER_ARTICLE_CHARS = image_insights_service.IMAGE_NOTES_MAX_CHARS

# 多轮对话：最多带入的历史消息条数（user/assistant 计）与单条字符上限，控制 token 预算。
MAX_HISTORY_MESSAGES = 8
_HISTORY_CONTENT_CHARS = 2000


class ReaderAIError(Exception):
    """阅读器 AI 业务错误，携带建议的 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _ensure_articles_exportable(db_sink: Any, records: List[Any]) -> None:
    """Final service-layer guard for content-bearing reader AI operations."""

    engine = getattr(db_sink, "engine", None)
    if engine is None:
        # Pure callers may inject dict/SimpleNamespace records without storage;
        # HTTP and production DB-backed paths always provide an engine.
        return
    source_ids = [str(_art_field(record, "source_id") or "") for record in records]
    with Session(engine) as session:
        allowed = set(user_sources_service.external_ai_allowed_source_ids(session, source_ids))
    if any(source_id not in allowed for source_id in source_ids):
        raise ReaderAIError("该来源含访问凭证，不能发送到 AI 服务", status_code=403)


# ==================== 全文翻译 ====================
async def _translate_title(
    title: str, llm_config: LLMConfig,
    usage_meta: Optional[UsageMeta] = None, http_client=None, *, max_retries: int = 3,
) -> str:
    raw = await chat_completion(
        messages=[
            ChatMessage(role="system", content=prompts.TRANSLATE_TITLE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompts.build_translate_title_user_prompt(title)),
        ],
        config=llm_config,
        usage_meta=usage_meta,
        http_client=http_client,
        require_complete=True,
        max_retries=max_retries,
    )
    # 只取首个非空行:模型偶尔会附一行说明,标题不该带它
    for line in (raw or "").splitlines():
        cleaned = line.strip().strip('"“”「」')
        if cleaned:
            return cleaned
    return ""


async def _translate_segment(
    title: str, segment: str, llm_config: LLMConfig,
    usage_meta: Optional[UsageMeta] = None, http_client=None, *, max_retries: int = 3,
) -> str:
    units = translation_units(segment)
    if not units:
        return segment
    raw = await chat_completion(
        messages=[
            ChatMessage(role="system", content=prompts.TRANSLATE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompts.build_translate_user_prompt(
                title, json.dumps({"units": units}, ensure_ascii=False),
            )),
        ],
        config=llm_config,
        usage_meta=usage_meta,
        http_client=http_client,
        require_complete=True,
        response_json=True,
        max_retries=max_retries,
    )
    return restore_translation_units(segment, raw)


async def _save_translation(
    db_sink, article_id: str, body: str, title: str, changes: dict,
    verify_ext: Optional[Callable[[dict], bool]] = None,
) -> None:
    """Merge cache fields without overwriting newer metadata or source text.

    verify_ext(issue #84):原文不在 content 里的场景(推文的 reposted / quoted 正文在 extensions_json)
    用它对最新的 extensions 复核原文未变,返回 False 即 409、不落缓存。
    """
    engine = getattr(db_sink, "engine", None)
    if engine is None:  # Small in-memory adapters used outside the HTTP stack.
        latest = await db_sink.get(article_id)
        if latest is None or (latest.content or "").strip() != body or (latest.title or "").strip() != title:
            raise ReaderAIError("原文已更新，请重新翻译", status_code=409)
        ext = _load_extensions(latest)
        if verify_ext is not None and not verify_ext(ext):
            raise ReaderAIError("原文已更新，请重新翻译", status_code=409)
        ext.update(changes)
        await db_sink.update(article_id, {"extensions_json": json.dumps(ext, ensure_ascii=False)})
        return

    from models.db import ArticleRecord
    with Session(engine) as session:
        latest = session.get(ArticleRecord, article_id)
        if latest is None or (latest.content or "").strip() != body or (latest.title or "").strip() != title:
            raise ReaderAIError("原文已更新，请重新翻译", status_code=409)
        ext = _load_extensions(latest)
        if verify_ext is not None and not verify_ext(ext):
            raise ReaderAIError("原文已更新，请重新翻译", status_code=409)
        ext.update(changes)
        result = session.execute(
            update(ArticleRecord).where(
                ArticleRecord.id == article_id,
                ArticleRecord.content == latest.content,
                ArticleRecord.title == latest.title,
                ArticleRecord.extensions_json == latest.extensions_json,
            ).values(extensions_json=json.dumps(ext, ensure_ascii=False)),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            session.rollback()
            raise ReaderAIError("文章已更新，请重新翻译", status_code=409)
        session.commit()


async def translate_article(
    db_sink, article_id: str, llm_config: LLMConfig, usage_meta: Optional[UsageMeta] = None,
    pre_llm_check: Optional[Callable[[], None]] = None,
) -> dict:
    """翻译指定文章正文(及标题)为中文；命中缓存直接返回，否则翻译后写回 extensions_json。

    返回 {"translation": str, "title": str | None, "cached": bool}。`title` 是中文标题:标题本已是
    中文则原样返回不调 LLM;标题翻译失败不拖垮正文——响应 `title=None` 且不写缓存,
    前端见 None 下次切译文时再调一次(正文命中缓存,只补标题)。
    `cached` 表示正文与标题均命中缓存(零 LLM 调用)。
    `pre_llm_check`(v3.43.2 codex 交叉检视 M02):成本准入回调,仅在缓存未命中、
    即将发起真实 LLM 调用前执行(抛异常即中止)——配额/预算是成本闸,缓存命中
    零成本不应被 429 拦截。
    """
    record = await db_sink.get(article_id)
    if record is None:
        raise ReaderAIError("文章不存在", status_code=404)
    _ensure_articles_exportable(db_sink, [record])
    body = (record.content or "").strip()
    if not body:
        raise ReaderAIError("该文章暂无可翻译的正文", status_code=400)

    try:
        ext = json.loads(record.extensions_json or "{}")
        if not isinstance(ext, dict):
            ext = {}
    except (ValueError, TypeError):
        ext = {}

    title = (record.title or "").strip()
    fingerprint = _body_fingerprint(body)
    title_fp = _body_fingerprint(title)
    body_cached = _cache_valid(ext, TRANSLATION_KEY, TRANSLATION_FP_KEY, fingerprint)
    if paper_metadata(record).get("content_level") in {"fulltext", "partial"}:
        body_cached = body_cached and ext.get(TRANSLATION_VERSION_KEY) == TRANSLATION_VERSION
    # 中文标题不送翻译:原样即译文(与前端「中文源不画翻译二段」同一启发式)
    title_needed = bool(title) and not looks_chinese(title)
    title_cached = (not title_needed) or _cache_valid(
        ext, TRANSLATION_TITLE_KEY, TRANSLATION_TITLE_FP_KEY, title_fp
    )
    if body_cached and title_cached:
        return {
            "translation": ext[TRANSLATION_KEY],
            "title": ext[TRANSLATION_TITLE_KEY] if title_needed else title,
            "cached": True,
        }
    try:
        plan = plan_translation(body, segment_chars=_TRANSLATE_SEGMENT_CHARS) if not body_cached else None
    except TranslationStructureError as exc:
        raise ReaderAIError(str(exc), status_code=422) from exc
    chunks = plan.chunks if plan is not None else ()
    needs_llm = not title_cached or any(chunk.needs_translation for chunk in chunks)
    if needs_llm and pre_llm_check is not None:
        pre_llm_check()

    concurrency = max(1, getattr(llm_config, "map_concurrency", 4))
    semaphore = asyncio.Semaphore(concurrency)

    async def _title_safely(http_client) -> str:
        # 标题翻译失败不拖垮正文:不写缓存、响应 title=None,前端据此下次再补;
        # 与正文分段同过并发信号量(codex 检视:map_concurrency=1 时曾并发两请求)
        try:
            async with semaphore:
                return await _translate_title(title, llm_config, usage_meta, http_client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("标题翻译失败 article=%s: %s", article_id, exc)
            return ""

    # 正文分段与标题并发共享一个连接池,避免逐段重建连接/TLS 握手。
    async with client_session(llm_config) as http_client:
        async def _guarded(chunk) -> str:
            if not chunk.needs_translation:
                return chunk.text
            async with semaphore:
                translated = await _translate_segment(title, chunk.text, llm_config, usage_meta, http_client)
                return chunk.validate(translated)

        jobs = [asyncio.create_task(_guarded(chunk)) for chunk in chunks]
        if not title_cached:
            jobs.append(asyncio.create_task(_title_safely(http_client)))
        try:
            results = await asyncio.gather(*jobs)
        except BaseException as exc:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if isinstance(exc, TranslationStructureError):
                raise ReaderAIError(str(exc), status_code=502) from exc
            raise

    translated_title = ""
    if not title_cached:
        translated_title = (results.pop() or "").strip()
    if body_cached:
        translated = ext[TRANSLATION_KEY]
    else:
        try:
            translated = plan.assemble(results)
        except TranslationStructureError as exc:
            raise ReaderAIError(str(exc), status_code=502) from exc
        if not translated:
            raise ReaderAIError("翻译失败，请稍后重试", status_code=502)
        ext[TRANSLATION_KEY] = translated
        ext[TRANSLATION_FP_KEY] = fingerprint
        ext[TRANSLATION_VERSION_KEY] = TRANSLATION_VERSION
    if translated_title:
        ext[TRANSLATION_TITLE_KEY] = translated_title
        ext[TRANSLATION_TITLE_FP_KEY] = title_fp
    keys = (
        TRANSLATION_KEY, TRANSLATION_FP_KEY, TRANSLATION_VERSION_KEY,
        TRANSLATION_TITLE_KEY, TRANSLATION_TITLE_FP_KEY,
    )
    await _save_translation(db_sink, article_id, body, title, {key: ext[key] for key in keys if key in ext})

    if title_needed:
        # 译名不可得(本次失败)→ None:明确告诉前端「没有译名」,勿把原标题当译名缓存
        resolved_title = translated_title or (ext.get(TRANSLATION_TITLE_KEY) if title_cached else "") or None
    else:
        resolved_title = title
    return {"translation": translated, "title": resolved_title, "cached": False}


# ==================== 推文翻译(issue #84) ====================
# 缓存与文章译文 translation_zh 分开:后者被文章旁白(article_listen_guides)读取,
# 且推文要译的原文在 extensions(reposted / quoted)而不只是 content。
SOCIAL_TRANSLATION_KEY = "translation_zh_social"
SOCIAL_TRANSLATION_VERSION = 1
# X 的「无语言内容」判定码:纯链接 / 纯表情 / 纯媒体链接 / 纯提及 / 纯话题等,没有可译文字
SOCIAL_NO_LANGUAGE_CODES = frozenset({"zxx", "art", "qme", "qam", "qht", "qst", "qct"})
# 链接到第一个空白或尖括号为止,与实际翻译时的链接保护(translation_markdown._protect)同一分隔规则——
# 判定说要译、保护却把文字吞进链接,就会出现「没调模型的译文」(检视复检)。空白集合显式写出(前端
# utils/socialTranslate.js 逐字同一份):各语言的 \s 不等价——
# Python 含 \x1c-\x1f / \x85、不含 \ufeff,JS 反之(检视 R1-04),依赖 \S 会让两侧对同一段文字给出不同判定。
_SOCIAL_URL_RE = re.compile(
    "https?://[^\t\n\x0b\x0c\r \x1c-\x1f\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff<>]+"
)
_SOCIAL_LANG_JUNK_RE = re.compile(r"[^a-z0-9-]")


def social_text_needs_translation(text: str, lang: Optional[str] = None) -> bool:
    """一段推文文字是否需要译成中文。前端 utils/socialTranslate.socialTextNeedsTranslation
    是同一规则的镜像,两侧须同步。

    去掉链接后没有任何字母 → 否;X 判为中文或无语言内容 → 否;其它明确语言码 → 是;
    und / 缺失 → 退回中文启发式(引用推、转推的存量记录没有存 lang)。
    """
    stripped = _SOCIAL_URL_RE.sub(" ", text or "")
    # isalpha = Unicode 字母类(L*),与前端 /\p{L}/u 同口径;纯表情 / 数字 / 链接都没有字母
    if not any(ch.isalpha() for ch in stripped):
        return False
    # 语言码只留 [a-z0-9-](X 给的是 ASCII BCP47);不依赖 strip / trim 各自的空白集合
    code = _SOCIAL_LANG_JUNK_RE.sub("", str(lang or "").lower())
    if code.startswith("zh") or code in SOCIAL_NO_LANGUAGE_CODES:
        return False
    if code and code != "und":
        return True
    return not looks_chinese(stripped)


def social_translation_sources(content: str, ext: dict) -> dict:
    """推文卡上需要翻译的原文:{"text": 主推正文, "quoted": 引用推正文},不需要的一侧不出现。

    转推的正文取原推全文 reposted.text(顶层 content 是「RT @xxx: …」截断形式),
    语言取 reposted.lang、缺失时用顶层 lang;引用推语言取 quoted.lang、缺失时走启发式。
    """
    reposted = ext.get("reposted") if isinstance(ext.get("reposted"), dict) else None
    quoted = ext.get("quoted") if isinstance(ext.get("quoted"), dict) else None
    if reposted:
        main_text = str(reposted.get("text") or content or "")
        main_lang = reposted.get("lang") or ext.get("lang")
    else:
        main_text = content or ""
        main_lang = ext.get("lang")
    sources = {}
    if social_text_needs_translation(main_text, main_lang):
        sources["text"] = main_text.strip()
    if quoted:
        quoted_text = str(quoted.get("text") or "")
        if social_text_needs_translation(quoted_text, quoted.get("lang")):
            sources["quoted"] = quoted_text.strip()
    return sources


def _social_fingerprint(sources: dict) -> str:
    return _body_fingerprint(json.dumps(sources, ensure_ascii=False, sort_keys=True))


async def _translate_social_batch(batch, llm_config: LLMConfig, usage_meta, http_client) -> dict:
    """一批片段一次调用,沿用 #206 的逐行单元协议:各片段的行单元编成全局 id 混进同一个 units 列表
    (主推与引用推同一请求),回包按 id 拆回各片段,交给 restore_translation_units 逐行校验与还原——
    与文章翻译同一套结构校验,模型多吐 / 少吐一个换行不会让整条失败。"""
    request_units: list = []
    owners: dict = {}
    for key, chunk in batch:
        for unit in translation_units(chunk.text):
            owners[len(request_units)] = (key, unit["id"])
            request_units.append({"id": len(request_units), "text": unit["text"]})
    raw = await chat_completion(
        messages=[
            ChatMessage(role="system", content=prompts.SOCIAL_TRANSLATE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompts.build_social_translate_user_prompt(request_units)),
        ],
        config=llm_config,
        response_json=True,
        usage_meta=usage_meta,
        http_client=http_client,
        require_complete=True,
    )
    per_chunk: dict = {key: [] for key, _ in batch}
    try:
        payload = parse_json_object(raw)
        if not isinstance(payload, dict) or set(payload) != {"units"} or not isinstance(payload["units"], list):
            raise ValueError
        for unit in payload["units"]:
            if not isinstance(unit, dict) or set(unit) != {"id", "text"}:
                raise ValueError
            if type(unit["id"]) is not int or unit["id"] not in owners:
                raise ValueError
            key, line_id = owners[unit["id"]]
            per_chunk[key].append({"id": line_id, "text": unit["text"]})
    except (ValueError, TypeError, RecursionError, LLMStructuredOutputError) as exc:
        raise TranslationStructureError("翻译单元不完整或格式无效，译文未保存") from exc
    # 缺失 / 重复 / 空串 / 含换行 / 占位符或链接结构变化,都由 restore_translation_units 拒绝
    return {
        key: restore_translation_units(chunk.text, json.dumps({"units": per_chunk[key]}, ensure_ascii=False))
        for key, chunk in batch
    }


async def translate_social_post(
    db_sink, article_id: str, llm_config: LLMConfig, usage_meta: Optional[UsageMeta] = None,
    pre_llm_check: Optional[Callable[[], None]] = None,
) -> dict:
    """把一条推文的主推与引用推译为中文;返回 {"text", "quoted", "cached"},不需要翻译的一侧为 None。

    一次点击一次调用:两段原文经 plan_translation 保护链接等字面量后,片段的行单元装进同一个 JSON 批次
    (#206 单元协议;超长推文才按 _TRANSLATE_SEGMENT_CHARS 拆多批)。缓存 extensions_json.translation_zh_social,
    指纹取两段原文,任一变化即重译。pre_llm_check 同 translate_article:只在缓存未命中、
    即将发起真实调用时执行。
    """
    record = await db_sink.get(article_id)
    if record is None:
        # 与隐藏源 / 未订阅私有源的拒绝同形(检视 R1-01):不让响应正文泄露条目是否存在
        raise ReaderAIError("文章不存在", status_code=404)
    if record.content_type != "social_post":
        raise ReaderAIError("只支持翻译社交媒体推文", status_code=400)
    _ensure_articles_exportable(db_sink, [record])
    content = (record.content or "").strip()
    ext = _load_extensions(record)
    sources = social_translation_sources(content, ext)
    if not sources:
        raise ReaderAIError("这条推文没有需要翻译的内容", status_code=400)
    fingerprint = _social_fingerprint(sources)
    cached = ext.get(SOCIAL_TRANSLATION_KEY)
    if (
        isinstance(cached, dict)
        and cached.get("fp") == fingerprint
        and cached.get("v") == SOCIAL_TRANSLATION_VERSION
    ):
        return {"text": cached.get("text"), "quoted": cached.get("quoted"), "cached": True}

    try:
        plans = {
            name: plan_translation(text, segment_chars=_TRANSLATE_SEGMENT_CHARS)
            for name, text in sources.items()
        }
    except TranslationStructureError as exc:
        raise ReaderAIError(str(exc), status_code=422) from exc
    # 兜底(检视复检):判定说要译、分段计划里却没有任何要交给模型的片段(全被保护成字面量)时,这段不出译文;
    # 一段都没有就按「没有需要翻译的内容」拒绝——绝不把原文当译文缓存
    plans = {
        name: plan for name, plan in plans.items()
        if any(chunk.needs_translation for chunk in plan.chunks)
    }
    if not plans:
        raise ReaderAIError("这条推文没有需要翻译的内容", status_code=400)
    items = [
        (f"{name}.{index}", chunk)
        for name, plan in plans.items()
        for index, chunk in enumerate(plan.chunks)
        if chunk.needs_translation
    ]
    batches: List[list] = []
    current: list = []
    size = 0
    for key, chunk in items:
        if current and size + len(chunk.text) > _TRANSLATE_SEGMENT_CHARS:
            batches.append(current)
            current, size = [], 0
        current.append((key, chunk))
        size += len(chunk.text)
    if current:
        batches.append(current)
    if batches and pre_llm_check is not None:
        pre_llm_check()

    translated: dict = {}
    if batches:
        semaphore = asyncio.Semaphore(max(1, getattr(llm_config, "map_concurrency", 4)))
        async with client_session(llm_config) as http_client:
            async def _guarded(batch) -> dict:
                async with semaphore:
                    return await _translate_social_batch(batch, llm_config, usage_meta, http_client)

            jobs = [asyncio.create_task(_guarded(batch)) for batch in batches]
            try:
                for result in await asyncio.gather(*jobs):
                    translated.update(result)
            except BaseException as exc:
                for job in jobs:
                    job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
                if isinstance(exc, TranslationStructureError):
                    raise ReaderAIError(str(exc), status_code=502) from exc
                raise

    entry = {"text": None, "quoted": None, "fp": fingerprint, "v": SOCIAL_TRANSLATION_VERSION}
    try:
        for name, plan in plans.items():
            entry[name] = plan.assemble([
                translated.get(f"{name}.{index}", chunk.text) for index, chunk in enumerate(plan.chunks)
            ])
    except TranslationStructureError as exc:
        raise ReaderAIError(str(exc), status_code=502) from exc

    def _sources_unchanged(latest_ext: dict) -> bool:
        return _social_fingerprint(social_translation_sources(content, latest_ext)) == fingerprint

    await _save_translation(
        db_sink, article_id, content, (record.title or "").strip(),
        {SOCIAL_TRANSLATION_KEY: entry}, verify_ext=_sources_unchanged,
    )
    return {"text": entry["text"], "quoted": entry["quoted"], "cached": False}


# ==================== 要点摘要 ====================
def _analysis_summary_for(db_sink, article_id: str) -> str:
    """读文章分析的权威 summary(status=succeeded 且非空),否则空串。

    db_sink 无 engine(测试替身)时视为无分析结果,保持既有 LLM 路径可测。"""
    engine = getattr(db_sink, "engine", None)
    if engine is None:
        return ""
    from sqlmodel import Session

    from services import article_analysis as article_analysis_service

    with Session(engine) as session:
        analysis = article_analysis_service.get_article_analysis(session, article_id)
    if analysis and analysis.get("summary"):
        return str(analysis["summary"]).strip()
    return ""


def _load_extensions(record) -> dict:
    try:
        ext = json.loads(record.extensions_json or "{}")
        return ext if isinstance(ext, dict) else {}
    except (ValueError, TypeError):
        return {}


async def summarize_article(
    db_sink, article_id: str, llm_config: LLMConfig, usage_meta: Optional[UsageMeta] = None,
    pre_llm_check: Optional[Callable[[], None]] = None,
) -> dict:
    """为指定文章生成中文要点摘要；命中缓存直接返回，否则生成后写回 extensions_json。

    返回 {"summary": str, "cached": bool}。缓存/写回与 pre_llm_check 约定均与
    translate_article 一致(成本闸只拦真实 LLM 调用,缓存命中零成本直过)。
    """
    record = await db_sink.get(article_id)
    if record is None:
        raise ReaderAIError("文章不存在", status_code=404)
    _ensure_articles_exportable(db_sink, [record])
    body = (record.content or "").strip()
    if not body:
        raise ReaderAIError("该文章暂无可总结的正文", status_code=400)

    # issue #13 复用:文章分析(v3.44)已产出的 summary 是全站唯一摘要——列表投影
    # 把它覆盖进 summary_zh,速读卡通常直接命中。读者仍点到本端点的情形只剩
    # 「分析尚未成功 / 源关闭分析 / 私有自定源」,故先查分析表,有即零成本返回,
    # 不进成本闸也不计费;只有真的没有分析结果才走本端点自己的轻提示词。
    analysis_summary = _analysis_summary_for(db_sink, article_id)
    if analysis_summary:
        return {"summary": analysis_summary, "cached": True, "source": "analysis"}

    ext = _load_extensions(record)
    fingerprint = _body_fingerprint(body)
    if _cache_valid(ext, SUMMARY_KEY, SUMMARY_FP_KEY, fingerprint):
        return {"summary": ext[SUMMARY_KEY], "cached": True}
    if pre_llm_check is not None:
        pre_llm_check()

    body = strip_layout_markers(body).strip()
    if len(body) > _SUMMARIZE_BODY_CHARS:
        body = body[:_SUMMARIZE_BODY_CHARS] + "\n...(正文已截断)"
    # 配图说明(issue #69,cached-only):速读兜底只在没有分析结果时走到这里,不为它发起识别;
    # 带了说明才在 system 级追加使用边界规则,否则消息与 main 逐字一致。
    image_notes = image_insights_service.cached_notes_map([article_id], llm_config).get(article_id, "")
    system_prompt = prompts.SUMMARIZE_SYSTEM_PROMPT
    if image_notes:
        body = f"{body}\n{image_notes}"
        system_prompt = system_prompt + prompts.IMAGE_NOTES_UNTRUSTED_RULE
    raw = await chat_completion(
        messages=[
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=prompts.build_summarize_user_prompt(record.title or "", body)),
        ],
        config=llm_config,
        usage_meta=usage_meta,
    )
    summary = raw.strip()
    if not summary:
        raise ReaderAIError("摘要生成失败，请稍后重试", status_code=502)

    ext[SUMMARY_KEY] = summary
    ext[SUMMARY_FP_KEY] = fingerprint
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


def build_sources_payload(records: List[Any], *, notes_by_id: Optional[dict] = None) -> List[dict]:
    """文章记录 → 问答响应的 sources 载荷（与编号上下文同序，序号即引用锚）。

    ``id`` 供前端行内引用 chip / 出处列表做站内跳转（v3.25 深链落地同路），
    ``source_name``/``publish_date`` 供出处行展示。
    ``has_image_notes``(issue #69):该篇上下文里是否实际带了配图说明——显式布尔元数据,
    作答侧据此决定是否追加图片说明的 system 规则,**不靠嗅探正文里的哨兵字符串**
    (正文可被攻击者写入「【图片内容】」,codex 检视 F8)。"""
    from services.source_naming import friendly_source_name

    notes = notes_by_id or {}
    return [
        {
            "id": _art_field(r, "id"),
            "title": _art_field(r, "title"),
            "source_id": _art_field(r, "source_id"),
            "source_name": friendly_source_name(_art_field(r, "source_id") or ""),
            "source_url": _art_field(r, "source_url"),
            "publish_date": (_art_field(r, "publish_date") or "")[:10],
            "has_image_notes": bool((notes.get(_art_field(r, "id")) or "").strip()),
        }
        for r in records
    ]


def sources_have_image_notes(sources: Optional[List[dict]]) -> bool:
    return any(bool(s.get("has_image_notes")) for s in (sources or []) if isinstance(s, dict))


_INTERNAL_SOURCE_FIELDS = ("has_image_notes",)


def public_sources(sources: Optional[List[dict]]) -> List[dict]:
    """对外响应用的 sources:剥掉服务端内部控制位(复检 R2:读者面不得出现「配图已识别」标记,
    且视觉关闭时响应形状须与 main 一致)。"""
    return [
        {k: v for k, v in s.items() if k not in _INTERNAL_SOURCE_FIELDS} if isinstance(s, dict) else s
        for s in (sources or [])
    ]


def build_numbered_context(
    articles: List[Any],
    *,
    per_article_chars: int = _LIST_PER_ARTICLE_CHARS,
    total_chars: int = _LIST_TOTAL_CHARS,
    keep_empty: bool = False,
    notes_by_id: Optional[dict] = None,
    notes_chars: int = _NOTES_PER_ARTICLE_CHARS,
) -> tuple:
    """多篇文章 → 编号上下文，返回 ``(context, included)``。

    逐篇「[n] 标题 + 截断正文」拼接，整体不超过 total_chars。**编号只对实际
    进入上下文的文章分配**（空正文默认跳过、超预算截断），included 即这份
    名单——sources 必须由它构建，否则回答里的 [n] 会指错文章。
    keep_empty=True 时空正文也保留条目（单篇场景标题本身就是信息）。
    articles 元素可为 dict（含 title/content）或带 .title/.content 属性的对象。
    notes_by_id(issue #69):文章 id → 配图识别文本。``per_article_chars`` 是该篇**正文 + 说明的
    组合内容预算**(codex 检视 F4:各占一份会撑爆 total_chars,8 篇只剩 2 篇):说明份额
    = min(per_article, max(1000, min(notes_chars, per_article // 2))),正文拿扣除**实际渲染**说明后的
    余额——短说明的空间还给正文;无说明时正文预算与 main 完全一致(字节不变)。
    单篇问答 per_article=12000 → 说明仍拿到 6000(用户拍板下限);检索档 2000 → 1000。
    只有配图说明而无正文的条目(纯图推文)也算有内容,不会被 keep_empty=False 跳过。
    """
    blocks: List[str] = []
    included: List[Any] = []
    used = 0
    notes_map = notes_by_id or {}
    from services.source_naming import friendly_source_name

    for art in articles:
        title = _art_field(art, "title") or "（无标题）"
        if isinstance(art, dict):
            body = (art.get("content") or art.get("content_preview") or "").strip()
        else:
            body = (getattr(art, "content", None) or "").strip()
        body = strip_layout_markers(body).strip()
        notes = (notes_map.get(_art_field(art, "id")) or "").strip()
        if not body and not notes and not keep_empty:
            continue
        if notes:
            # 有说明的新路径:正文 + 换行分隔 + 说明恒 ≤ per_article_chars(省略号计入预算,复检 R2 尾差返修)
            notes_limit = min(per_article_chars, max(1000, min(notes_chars, per_article_chars // 2)))
            if len(notes) > notes_limit:
                notes = notes[: max(0, notes_limit - 1)] + "…"
            body_budget = max(0, per_article_chars - len(notes) - (1 if body else 0))
            if len(body) > body_budget:
                body = (body[: body_budget - 1] + "…") if body_budget > 0 else ""
            body = f"{body}\n{notes}" if body else notes
        elif len(body) > per_article_chars:
            # 无说明:与 main 逐字一致
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


def assemble_articles_context(
    records: List[Any], *, total_chars: int = _LIST_TOTAL_CHARS,
    notes_by_id: Optional[dict] = None,
) -> tuple:
    """显式名单的上下文组装：单篇预算按篇数摊分（n=1 独享全额），返回 (context, sources)。"""
    per_article = max(_LIST_PER_ARTICLE_CHARS, total_chars // max(1, len(records)))
    context, included = build_numbered_context(
        records, per_article_chars=per_article, total_chars=total_chars,
        keep_empty=(len(records) == 1), notes_by_id=notes_by_id,
    )
    return context, build_sources_payload(included, notes_by_id=notes_by_id)


async def _explicit_image_notes(
    records: List[Any], llm_config: Optional[LLMConfig], usage_meta: Optional[UsageMeta],
) -> dict:
    """显式篇目(≤12)的配图说明:能力启用时缺则识(问答档预算);未启用 / 未传配置恒空(issue #69)。

    新文章通常 worker 已识完,这里多为零成本命中;存量文章被显式打开时按需补识。
    任何失败都退回空 map——识图是补充,不能让问答 5xx。
    """
    ids = [str(_art_field(r, "id") or "") for r in records]
    return await image_insights_service.ensure_notes_map(
        ids, llm_config, usage_meta=usage_meta,
        budget_seconds=image_insights_service.QA_ENSURE_BUDGET_SECONDS,
        max_chars=_NOTES_PER_ARTICLE_CHARS,
    )


async def assemble_reader_context(
    *,
    scope: str,
    question: str,
    article_id: Optional[str],
    username: str,
    db_sink: Any,
    search_fetch: Any,
    article_ids: Optional[List[str]] = None,
    llm_config: Optional[LLMConfig] = None,
    usage_meta: Optional[UsageMeta] = None,
) -> tuple:
    """按 scope 组装 reader 问答上下文，返回 ``(context, sources)``。

    ``llm_config`` / ``usage_meta``(issue #69)只用于显式篇目的配图识别(缺则识);不传或
    能力未启用时恒无说明。检索档的配图说明由 search_fetch 内部(reader_search)按 cached-only 附带。

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
        _ensure_articles_exportable(db_sink, [record])
        notes = await _explicit_image_notes([record], llm_config, usage_meta)
        return assemble_articles_context([record], notes_by_id=notes)

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
        _ensure_articles_exportable(db_sink, records)
        notes = await _explicit_image_notes(records, llm_config, usage_meta)
        return assemble_articles_context(records, notes_by_id=notes)

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
    with_image_notes: bool = False,
) -> str:
    """基于给定上下文 + 多轮历史回答提问。上下文为空时仍调用，由提示词约束模型如实说明资料不足。

    消息结构：system → 历史轮次（纯文本问答）→ 当前问题（附本轮参考资料）。
    历史让模型支持「追问/指代」，参考资料只随当前问题刷新，token 预算可控。
    ``with_image_notes``(issue #69)由调用方按 sources 的显式布尔元数据给出(见
    :func:`sources_have_image_notes`),为 True 时 system 追加图片说明使用边界;不嗅探 context。
    """
    question = (question or "").strip()
    if not question:
        raise ReaderAIError("请输入你的问题", status_code=400)
    system_prompt = prompts.QA_SYSTEM_PROMPT
    if with_image_notes:
        system_prompt = system_prompt + prompts.IMAGE_NOTES_UNTRUSTED_RULE
    messages = [ChatMessage(role="system", content=system_prompt)]
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
