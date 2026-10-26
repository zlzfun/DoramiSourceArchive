"""事件分配服务(issue #121 事件层 wave 1):把分析成功的文章增量归入稳定事件。

``events`` / ``event_members`` 是热点榜(``event_board``)、个人早报去重(wave 2)与 Agent 查询(wave 3)
共用的一份归属;本模块是 authority 侧唯一写者(replica 上 ``analysis_authority_id`` 非空的文章不入池)。
流程按方案 §3:

1. 入池 :func:`scan_pending`——分析 ``succeeded``、本机权威、非私有、内容类型 ∉ {论文, 汇编}、非播客、
   7 天内入库 → ledger ``pending``;不进事件层的直接记 ``skipped``(不再重扫)。
2. 机械直连(零 LLM)——规范 URL(:func:`canonical_url`)或归一化标题(≥ 8 字、数字串一致)与开放事件成员相同
   → 直接入该事件;``docs_`` / ``github_`` 这类共享页面 URL 的源不走 URL 直连,同源同题非同日(连载 / 日更栏目)不走标题直连。
3. 召回 :func:`recall_candidates`——开放事件(距首篇首见 ≤ 48h)里 ① 共享 ``entity.*`` 码的 ② 成员被标题 / 分析摘要 /
   译中标题三路 :func:`recall_fts` top-10 命中的;并集按 ``last_member_at`` 倒序截 20;同源 + 标题全同 + 非同日的事件剔除。
4. 一次辅助模型调用判整批(``llm.prompts.EVENT_ASSIGN_SYSTEM_PROMPT``):回显校验、非法 eid、低置信跨日、空共同事实
   一律单例(:func:`parse_event_assign_response`);零候选且批内无相似者的文章不问模型直接 ``singleton``。
5. :func:`apply_assignments` 单事务落成员(``report_at`` 钳在首见 −72h 与首见之间、``publisher_key`` 按注册表
   ``source_owner`` → 别名表 → ``source_id``)、计数、代表稿(官方 > 非社交 > 分数 > 发布更早)、事件题
   (中文成员题 → 译题缓存 → 模型题 → 代表稿原题)与 ledger。

失败与恢复全在 ledger、无内存队列:模型失败 / 输出非法 → ``attempts+1`` 与 5 / 15 / 45 分钟退避;``pending`` 超 72h →
机械单例 + ``skipped``(早报键始终可用);LLM 未配置 → 全部单例。事件 ``status`` 懒更新(open → closed 当 now − 首见 > 48h)。
服务本身是同步函数,异步调用方用 ``asyncio.to_thread`` 包;进程内互斥锁(照 ``rankings._SNAPSHOT_BUILD_LOCK``)防重入。
不回溯:不做事后合并,误并靠管理员摘出 / 合并修正(``event_board``)。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import datetime as dt
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import literal_column, text, tuple_
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from config import LLMConfig
from llm.client import ChatMessage, UsageMeta, chat_completion
from llm.prompts import (
    EVENT_ASSIGN_PROMPT_VERSION,
    EVENT_ASSIGN_SYSTEM_PROMPT,
    build_event_assign_user_prompt,
)
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    EventAssignmentLedgerRecord,
    EventMemberRecord,
    EventRecord,
    EventRedirectRecord,
    SourceConfigRecord,
)
from services import user_sources
from services.article_time import SHANGHAI, parse_article_time
from services.content_classification import classify_item, source_default_kinds
from services.daily_brief import _DIGIT_RUN_RE, _normalized_title, _titles_look_same
from services.reader_ai import (
    TRANSLATION_TITLE_FP_KEY,
    TRANSLATION_TITLE_KEY,
    _body_fingerprint,
    _cache_valid,
)
from services.source_naming import source_role
from storage.fts import FTS_TABLE, MIN_TRIGRAM_CHARS, normalize_for_search

logger = logging.getLogger(__name__)

# ── 版本与参数(方案 §2 / §3)──
ASSIGNMENT_VERSION = "event-assign-v1"          # 提示词或召回改动时 +1;只影响新文章,不自动重跑存量
OPEN_WINDOW_HOURS = 48                           # 事件按首篇首见关窗
INTAKE_WINDOW_DAYS = 7                           # 入池只看最近 7 天入库的文章
PENDING_MAX_AGE_HOURS = 72                       # pending 超时 → 机械单例 + skipped
REPORT_AT_BACKDATE_HOURS = 72                    # report_at 最多比首见早 72h
CANDIDATE_LIMIT = 20
RECALL_TOP_K = 10
MAX_RECALL_TOKENS = 48
MIN_TITLE_DIRECT_CHARS = 8                       # 归一化标题短于此不走标题直连(与日报预聚类同口径)
CROSS_DAY_HOURS = 24                             # 并入首篇早于本文 report_at 24h 以上的事件需高置信
CROSS_DAY_MIN_CONFIDENCE = 0.8
MIN_FACT_CHARS = 4
RETRY_BACKOFF_MINUTES = (5, 15, 45)
SCAN_LIMIT = 500
SCAN_PAGE_SIZE = 200
SCAN_MAX_ROWS = 5000                             # 单次入池最多翻看的行数(keyset 翻页上限)
MAX_BATCHES_PER_RUN = 10
ECHO_CHARS = 12
EXCLUDED_KINDS = frozenset({"paper", "digest"})
# 汇编体裁(GitHub Trending 日榜、Arena 排行榜变更、AINews 日报……)同样不入事件层(方案 §2「汇编不入」):
# 这类行的内容类型常是 project / news,只按 EXCLUDED_KINDS 判会漏进来,生产 14 天回放里造成日榜跨日误并(方案 §13)。
EXCLUDED_GENRES = frozenset({"aggregation"})
PODCAST_CONTENT_TYPE = "podcast_episode"
URL_DIRECT_EXCLUDED_SOURCE_PREFIXES = ("docs_", "github_")
URL_DIRECT_EXCLUDED_CONTENT_TYPES = frozenset({"github_release", "github_repository"})
# 同一机构多个账号 / 子品牌只算一个发布主体(方案 §4)。
PUBLISHER_ALIASES: dict[str, str] = {
    "sam_altman": "openai",
    "google_deepmind": "google",
}
ENTITY_TAG_PREFIX = "entity."
_TRACKING_QUERY_KEYS = frozenset({
    "fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "ref", "source", "spm", "igshid",
    "_hsenc", "_hsmi", "mkt_tok",
})
_TRACKING_QUERY_PREFIXES = ("utm_",)

LEDGER_PENDING = "pending"
LEDGER_ASSIGNED = "assigned"
LEDGER_SKIPPED = "skipped"
LEDGER_BLOCKED = "blocked"

ASSIGNED_BY_URL = "url"
ASSIGNED_BY_TITLE = "title"
ASSIGNED_BY_LLM = "llm"
ASSIGNED_BY_SINGLETON = "singleton"

# 与 rankings._SNAPSHOT_BUILD_LOCK 同一姿态:分析周期挂钩与 10 分钟兜底 interval job 可能同时到达,
# 后到者直接返回 busy,避免两份扫描互相抢 SQLite 写锁。
_ASSIGN_LOCK = threading.Lock()

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_LETTER_RE = re.compile(r"[^\W\d_]")
_TOKEN_SPLIT_RE = re.compile(r"[\s,\u3001\uff0c\u3002;\uff1b:\uff1a!\uff01?\uff1f()\uff08\uff09\[\]\u3010\u3011\u300a\u300b\"\u201c\u201d'\u2018\u2019|/\u300c\u300d]+")
_WS_RE = re.compile(r"\s+")


# ══════════════════════════════════════════════════════════════════════════════
# 时间与通用工具
# ══════════════════════════════════════════════════════════════════════════════

def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _as_utc(value: Optional[dt.datetime]) -> dt.datetime:
    value = value or _utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds")


def _parse_iso(value: Optional[str]) -> Optional[dt.datetime]:
    return parse_article_time(value)


def _shanghai_day(value: Optional[str], fallback: dt.datetime) -> str:
    parsed = parse_article_time(value) or fallback
    return parsed.astimezone(SHANGHAI).date().isoformat()


def _chunks(items: Sequence[Any], size: int = 400) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _load_extensions(article: ArticleRecord) -> dict[str, Any]:
    try:
        data = json.loads(article.extensions_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def translated_title(article: ArticleRecord) -> str:
    """译题缓存,与阅读窗 / 早报同一指纹口径(``reader_ai._cache_valid``):标题刷新后旧译题失效 → 空串,
    事件题与译中召回都不采纳;无指纹的存量缓存按兼容口径仍有效。"""
    ext = _load_extensions(article)
    fingerprint = _body_fingerprint(article.title or "")
    if not _cache_valid(ext, TRANSLATION_TITLE_KEY, TRANSLATION_TITLE_FP_KEY, fingerprint):
        return ""
    return str(ext[TRANSLATION_TITLE_KEY]).strip()


def _is_chinese_title(title: str) -> bool:
    """标题是否「中文题」:≥ 2 个 CJK 字且(≥ 4 个或占字母数 15% 以上)——「OpenAI 发布 GPT-5.5」算中文题。"""
    cjk = len(_CJK_RE.findall(title or ""))
    if cjk < 2:
        return False
    letters = len(_LETTER_RE.findall(title or "")) or 1
    return cjk >= 4 or cjk / letters >= 0.15


def _run_async(coro: Awaitable[Any]) -> Any:
    """同步服务里跑 ``chat_completion``:线程池 / 调度线程无事件循环时直接 ``asyncio.run``,
    被误从事件循环线程调用则退到独立线程(与 personal_digest_titles 同一写法)。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ══════════════════════════════════════════════════════════════════════════════
# 规范 URL / 标题
# ══════════════════════════════════════════════════════════════════════════════

def _is_tracking_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _TRACKING_QUERY_KEYS or lowered.startswith(_TRACKING_QUERY_PREFIXES)


def canonical_url(value: Optional[str]) -> str:
    """URL 直连用的规范形:小写 scheme / 主机、去 utm_* / fbclid 等跟踪参数、去 fragment、折叠重复斜杠、去尾斜杠、
    查询参数按键排序。解析失败退回 casefold 原串;空串原样返回。"""
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.casefold()
    kept = sorted(
        (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    )
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(kept), ""))


def _url_direct_allowed(article: ArticleRecord) -> bool:
    """共享页面 URL 的源(docs changelog / GitHub releases)不走 URL 直连,否则整条变更日志并成一个事件。"""
    if (article.content_type or "") in URL_DIRECT_EXCLUDED_CONTENT_TYPES:
        return False
    return not str(article.source_id or "").startswith(URL_DIRECT_EXCLUDED_SOURCE_PREFIXES)


def _title_direct_key(title: str) -> str:
    """标题直连键:归一化标题(日报同口径)且 ≥ 8 字;短题返回空串(「Qwen 更新」类共享前缀太易误并)。"""
    normalized = _normalized_title(title)
    return normalized if len(normalized) >= MIN_TITLE_DIRECT_CHARS else ""


def _titles_equal(a: str, b: str) -> bool:
    """归一化相同且数字串一致——归一化只去标点与空白,数字留着,这里显式再守一道。"""
    return bool(a) and a == b and _DIGIT_RUN_RE.findall(a) == _DIGIT_RUN_RE.findall(b)


# ══════════════════════════════════════════════════════════════════════════════
# FTS 召回
# ══════════════════════════════════════════════════════════════════════════════

def _split_runs(piece: str) -> Iterable[tuple[str, bool]]:
    """把一段无分隔文本拆成「CJK 连续段 / 非 CJK 连续段」。"""
    if not piece:
        return
    current = piece[0]
    is_cjk = bool(_CJK_RE.match(piece[0]))
    for char in piece[1:]:
        char_cjk = bool(_CJK_RE.match(char))
        if char_cjk == is_cjk:
            current += char
            continue
        yield current, is_cjk
        current, is_cjk = char, char_cjk
    yield current, is_cjk


def recall_tokens(text_value: Optional[str], *, max_tokens: int = MAX_RECALL_TOKENS) -> list[str]:
    """召回用 token:归一化后按空白 / 标点切分;拉丁 / 数字段整段保留(≥ 3 字符,「GPT-5.5」不拆),
    CJK 段切成滑动三字组(trigram 下限);去重、超量时等距抽样。"""
    normalized = normalize_for_search(text_value or "") or ""
    tokens: list[str] = []
    seen: set[str] = set()
    for piece in _TOKEN_SPLIT_RE.split(normalized):
        for run, is_cjk in _split_runs(piece):
            if len(run) < MIN_TRIGRAM_CHARS:
                continue
            candidates = (
                [run[i:i + 3] for i in range(len(run) - 2)] if is_cjk else [run]
            )
            for token in candidates:
                key = token.casefold()
                if key not in seen:
                    seen.add(key)
                    tokens.append(token)
    if len(tokens) > max_tokens:
        step = -(-len(tokens) // max_tokens)
        tokens = tokens[::step][:max_tokens]
    return tokens


def _match_query(tokens: Sequence[str]) -> str:
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def recall_fts(bind, text_value: Optional[str], limit: int = RECALL_TOP_K, *,
               rowids: Optional[Iterable[int]] = None) -> list[int]:
    """OR 语义的 FTS5 召回(与 ``fts_search_ranked`` 的 AND 用户搜索相反):≥ 3 字符的归一化 token 用 OR 连成
    MATCH,``ORDER BY rank LIMIT k``,返回 ``articles.rowid`` 列表(越前越相关)。``rowids`` 给定时只在这些行里找
    (开放事件成员 / 本批文章),空集直接返回 [];FTS 不可用或异常返回 []——召回层退化不抛到调用方。"""
    tokens = recall_tokens(text_value)
    if not tokens:
        return []
    scope: Optional[list[int]] = None
    if rowids is not None:
        scope = sorted({int(r) for r in rowids})
        if not scope:
            return []
    sql = f"SELECT rowid FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :q"
    if scope is not None:
        # rowid 是整数,直接内联(避开 SQLite 绑定变量上限);开放事件成员至多几百行。
        sql += " AND rowid IN (" + ",".join(str(r) for r in scope) + ")"
    sql += " ORDER BY rank LIMIT :k"
    params = {"q": _match_query(tokens), "k": int(limit)}
    try:
        if isinstance(bind, Engine):
            with bind.connect() as conn:
                rows = conn.execute(text(sql), params).all()
        elif isinstance(bind, Session):
            rows = bind.connection().execute(text(sql), params).all()
        else:  # Connection
            rows = bind.execute(text(sql), params).all()
    except Exception as exc:  # noqa: BLE001 - FTS 缺席 / 老 SQLite:召回退化,不阻断分配
        logger.debug("事件召回 FTS 不可用:%s", exc)
        return []
    return [int(row[0]) for row in rows]


# ══════════════════════════════════════════════════════════════════════════════
# 来源元数据(角色 / 形态 / 发布主体)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SourceMeta:
    role: str = "media"          # official / media / personal / leaderboard
    shape: str = "article"       # article / social / podcast
    owner: str = ""              # 注册表 source_owner → 配置 source_owner


def _registry_class(source_id: str):
    try:
        from fetchers.registry import fetcher_registry

        return fetcher_registry.get_class(source_id)
    except Exception:  # noqa: BLE001 - 注册表不可用时按配置 / 兜底
        return None


def load_source_meta(session: Session, source_ids: Iterable[str]) -> dict[str, SourceMeta]:
    """source_id → (角色, 形态, 主体):注册表类优先给形态与主体,配置行优先给角色(与早报重大事件通道同口径)。"""
    wanted = sorted({str(sid) for sid in source_ids if sid})
    if not wanted:
        return {}
    configs: dict[str, SourceConfigRecord] = {}
    for chunk in _chunks(wanted):
        for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(list(chunk)))
        ).all():
            configs[row.source_id] = row
    result: dict[str, SourceMeta] = {}
    for source_id in wanted:
        config = configs.get(source_id)
        cls = _registry_class(source_id)
        if config is not None:
            role = source_role(
                source_id,
                source_scope=config.source_scope or "",
                provenance_tier=config.provenance_tier or "",
            )
            source_type = str(config.source_type or "").strip().lower()
            config_shape = "social" if source_type in {"x", "x_timeline"} else (
                "podcast" if user_sources.source_content_kind(config) == "podcast" else "article"
            )
        else:
            role = source_role(source_id)
            config_shape = "article"
        shape = str(getattr(cls, "content_shape", "") or "").strip() if cls is not None else ""
        owner = str(getattr(cls, "source_owner", "") or "").strip() if cls is not None else ""
        if not owner and config is not None:
            owner = str(config.source_owner or "").strip()
        result[source_id] = SourceMeta(role=role, shape=shape or config_shape, owner=owner)
    return result


def publisher_key(source_id: str, meta: Optional[SourceMeta]) -> str:
    """发布主体键:``source_owner`` 小写 → 别名表合并 → 回落 ``source_id``。"""
    owner = (meta.owner if meta else "").strip().casefold()
    key = owner or str(source_id or "").strip()
    return PUBLISHER_ALIASES.get(key, key)


def _is_private_source(session: Session, source_id: str, owner_backed: set[str],
                       configs: Mapping[str, SourceConfigRecord]) -> bool:
    """私有内容永不进事件层:``user_rss_`` 前缀、带 owner_username 的配置源、凭据源(沿用 user_sources 判定)。"""
    if user_sources.is_user_source(source_id) or source_id in owner_backed:
        return True
    return user_sources.source_is_credentialed(configs.get(source_id))


# ══════════════════════════════════════════════════════════════════════════════
# 入池
# ══════════════════════════════════════════════════════════════════════════════

def _new_ledger(article_id: str, status: str, now: dt.datetime, note: str = "") -> EventAssignmentLedgerRecord:
    stamp = _iso(now)
    return EventAssignmentLedgerRecord(
        article_id=article_id, status=status, attempts=0, next_attempt_at="",
        last_error=note[:300], blocked_event_ids_json="[]", created_at=stamp, updated_at=stamp,
    )


def _intake_reason(article: ArticleRecord, analysis: ArticleAnalysisRecord, *,
                   meta: Mapping[str, SourceMeta], source_kinds: Mapping[str, str]) -> str:
    """不进事件层的原因(空串 = 可入池)。论文 / 汇编按读取时推导的内容类型判,播客按数据结构 / 源形态判。"""
    if (article.content_type or "") == PODCAST_CONTENT_TYPE:
        return "podcast"
    source_meta = meta.get(article.source_id)
    if source_meta is not None and source_meta.shape == "podcast":
        return "podcast"
    kind = classify_item(
        source_id=article.source_id,
        source_url=article.source_url,
        content_type=article.content_type,
        content_genre=analysis.content_genre,
        source_kinds=source_kinds,
        content_kind=getattr(analysis, "content_kind", None),
    ).kind
    if kind in EXCLUDED_KINDS:
        return f"kind:{kind}"
    genre = str(getattr(analysis, "content_genre", "") or "").strip().lower()
    if genre in EXCLUDED_GENRES:
        return f"genre:{genre}"
    return ""


def scan_pending(session: Session, *, now: Optional[dt.datetime] = None, limit: int = SCAN_LIMIT,
                 page_size: int = SCAN_PAGE_SIZE) -> int:
    """入池:分析 ``succeeded`` 且无 ledger 行、无成员行、本机权威、7 天内入库的文章 → ledger ``pending``;
    不进事件层的(私有 / 论文 / 汇编 / 播客 / 窗口外 / 时间不可解析)记 ``skipped`` 并写原因,不再重扫。

    SQL 只能按日期前缀粗筛(``fetched_date`` 历史格式混杂),精确窗口在 Python 判;按 ``(fetched_date, id)``
    keyset 翻页直到凑够 ``limit`` 篇 pending 或翻完 ``SCAN_MAX_ROWS`` 行——被排除的行都记了账,
    不会反复占住扫描额度(R1 #9)。返回新写 ``pending`` 的条数。"""
    now = _as_utc(now)
    cutoff = now - dt.timedelta(days=INTAKE_WINDOW_DAYS)
    prefix = (cutoff - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    owner_backed = user_sources.user_source_ids(session)
    source_kinds = source_default_kinds()
    meta: dict[str, SourceMeta] = {}
    configs: dict[str, SourceConfigRecord] = {}
    pending = 0
    examined = 0
    last_key: Optional[tuple[str, str]] = None
    while pending < limit and examined < SCAN_MAX_ROWS:
        query = (
            select(ArticleRecord, ArticleAnalysisRecord)
            .join(ArticleAnalysisRecord, ArticleAnalysisRecord.article_id == ArticleRecord.id)
            .where(
                ArticleAnalysisRecord.status == "succeeded",
                ArticleRecord.analysis_authority_id == "",
                ArticleRecord.fetched_date >= prefix,
                ArticleRecord.id.not_in(select(EventAssignmentLedgerRecord.article_id)),
                ArticleRecord.id.not_in(select(EventMemberRecord.article_id)),
            )
            .order_by(ArticleRecord.fetched_date.asc(), ArticleRecord.id.asc())
            .limit(max(1, int(page_size)))
        )
        if last_key is not None:
            query = query.where(tuple_(ArticleRecord.fetched_date, ArticleRecord.id) > last_key)
        rows = session.exec(query).all()
        if not rows:
            break
        examined += len(rows)
        last_key = (rows[-1][0].fetched_date, rows[-1][0].id)
        wanted = {article.source_id for article, _analysis in rows} - set(meta)
        if wanted:
            meta.update(load_source_meta(session, wanted))
            for chunk in _chunks(sorted(wanted)):
                for row in session.exec(
                    select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(list(chunk)))
                ).all():
                    configs[row.source_id] = row
        for article, analysis in rows:
            fetched = parse_article_time(article.fetched_date)
            if fetched is None:
                session.add(_new_ledger(article.id, LEDGER_SKIPPED, now, "bad_fetched_date"))
                continue
            if fetched < cutoff:
                session.add(_new_ledger(article.id, LEDGER_SKIPPED, now, "outside_intake_window"))
                continue
            if _is_private_source(session, article.source_id, owner_backed, configs):
                session.add(_new_ledger(article.id, LEDGER_SKIPPED, now, "private_source"))
                continue
            reason = _intake_reason(article, analysis, meta=meta, source_kinds=source_kinds)
            if reason:
                session.add(_new_ledger(article.id, LEDGER_SKIPPED, now, reason))
                continue
            session.add(_new_ledger(article.id, LEDGER_PENDING, now))
            pending += 1
            if pending >= limit:
                break
    session.commit()
    return pending


# ══════════════════════════════════════════════════════════════════════════════
# 开放事件上下文(直连索引 + 召回域)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _MemberInfo:
    article_id: str
    rowid: int
    source_id: str
    title: str
    norm_title: str
    canonical: str
    day: str


@dataclass
class _OpenEvent:
    event: EventRecord
    members: list[_MemberInfo] = field(default_factory=list)
    entity_codes: set[str] = field(default_factory=set)
    representative_title: str = ""
    representative_summary: str = ""
    first_day: str = ""

    @property
    def event_id(self) -> int:
        return int(self.event.id)


@dataclass
class _OpenContext:
    now: dt.datetime
    events: dict[int, _OpenEvent] = field(default_factory=dict)
    by_canonical: dict[str, int] = field(default_factory=dict)
    by_title: dict[str, list[tuple[int, str, str]]] = field(default_factory=dict)   # key → [(event_id, source_id, day)]
    by_entity: dict[str, set[int]] = field(default_factory=dict)
    rowid_to_event: dict[int, int] = field(default_factory=dict)

    def register(self, event_id: int, member: _MemberInfo, entity_codes: Iterable[str]) -> None:
        """把一个成员登记进索引(建上下文与本批内新落的分配都走这里,后续文章能直连 / 召回到它)。"""
        open_event = self.events.get(event_id)
        if open_event is None:
            return
        open_event.members.append(member)
        if member.canonical and member.canonical not in self.by_canonical:
            self.by_canonical[member.canonical] = event_id
        key = _title_direct_key(member.title)
        if key:
            self.by_title.setdefault(key, []).append((event_id, member.source_id, member.day))
        for code in entity_codes:
            open_event.entity_codes.add(code)
            self.by_entity.setdefault(code, set()).add(event_id)
        self.rowid_to_event[member.rowid] = event_id

    def add_event(self, event: EventRecord, *, representative_title: str = "",
                  representative_summary: str = "") -> _OpenEvent:
        open_event = _OpenEvent(
            event=event,
            representative_title=representative_title,
            representative_summary=representative_summary,
            first_day=_shanghai_day(event.first_seen_at, self.now),
        )
        self.events[int(event.id)] = open_event
        return open_event


def entity_codes_by_article(session: Session, article_ids: Sequence[str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for chunk in _chunks(list(article_ids)):
        rows = session.exec(
            select(ArticleTagAssignmentRecord.article_id, CmsTagRecord.code)
            .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
            .where(
                ArticleTagAssignmentRecord.article_id.in_(list(chunk)),
                ArticleTagAssignmentRecord.tag_kind == "entity",
            )
        ).all()
        for article_id, code in rows:
            if str(code).startswith(ENTITY_TAG_PREFIX):
                result.setdefault(str(article_id), set()).add(str(code))
    return result


def _rowids_by_article(session: Session, article_ids: Sequence[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for chunk in _chunks(list(article_ids)):
        rows = session.exec(
            select(ArticleRecord.id, literal_column("articles.rowid"))
            .where(ArticleRecord.id.in_(list(chunk)))
        ).all()
        for article_id, rowid in rows:
            result[str(article_id)] = int(rowid)
    return result


def _member_info(article: ArticleRecord, rowid: int, now: dt.datetime) -> _MemberInfo:
    return _MemberInfo(
        article_id=article.id,
        rowid=rowid,
        source_id=article.source_id,
        title=article.title or "",
        norm_title=_normalized_title(article.title or ""),
        canonical=canonical_url(article.source_url) if _url_direct_allowed(article) else "",
        day=_shanghai_day(article.publish_date or article.fetched_date, now),
    )


def close_stale_events(session: Session, *, now: Optional[dt.datetime] = None) -> int:
    """懒更新事件状态:本机产生的 ``open`` 事件距首篇首见 > 48h → ``closed``(同步来的事件由 authority 下发状态)。"""
    now = _as_utc(now)
    window_start = now - dt.timedelta(hours=OPEN_WINDOW_HOURS)
    closed = 0
    for event in session.exec(
        select(EventRecord).where(EventRecord.status == "open", EventRecord.authority_id == "")
    ).all():
        first_seen = _parse_iso(event.first_seen_at)
        if first_seen is None or first_seen < window_start:
            event.status = "closed"
            event.updated_at = _iso(now)
            session.add(event)
            closed += 1
    if closed:
        session.commit()
    return closed


def _build_open_context(session: Session, now: dt.datetime) -> _OpenContext:
    context = _OpenContext(now=now)
    window_start = now - dt.timedelta(hours=OPEN_WINDOW_HOURS)
    # 只取本地可写事件(authority_id 为空):同步来的 authority 事件只接受同 authority 的文章做成员,
    # 进候选池会让直连 / 模型归并撞上围栏(R1 #7)。
    events = [
        event for event in session.exec(
            select(EventRecord).where(EventRecord.status == "open", EventRecord.authority_id == "")
        ).all()
        if (_parse_iso(event.first_seen_at) or now) >= window_start
    ]
    if not events:
        return context
    for event in events:
        context.add_event(event)
    event_ids = [int(event.id) for event in events]
    members: list[tuple[EventMemberRecord, ArticleRecord]] = []
    for chunk in _chunks(event_ids):
        members.extend(session.exec(
            select(EventMemberRecord, ArticleRecord)
            .join(ArticleRecord, ArticleRecord.id == EventMemberRecord.article_id)
            .where(EventMemberRecord.event_id.in_(list(chunk)))
        ).all())
    article_ids = [article.id for _member, article in members]
    rowids = _rowids_by_article(session, article_ids)
    entity_codes = entity_codes_by_article(session, article_ids)
    representative_ids = {
        context.events[int(member.event_id)].event.representative_article_id
        for member, _article in members
    } - {None}
    summaries: dict[str, str] = {}
    for chunk in _chunks(sorted(representative_ids)):
        for article_id, summary in session.exec(
            select(ArticleAnalysisRecord.article_id, ArticleAnalysisRecord.summary)
            .where(ArticleAnalysisRecord.article_id.in_(list(chunk)))
        ).all():
            summaries[str(article_id)] = str(summary or "")
    for member, article in members:
        open_event = context.events.get(int(member.event_id))
        if open_event is None:
            continue
        context.register(
            int(member.event_id),
            _member_info(article, rowids.get(article.id, -1), now),
            entity_codes.get(article.id, ()),
        )
        if article.id == open_event.event.representative_article_id or not open_event.representative_title:
            open_event.representative_title = article.title or ""
            open_event.representative_summary = summaries.get(article.id, "")
    return context


# ══════════════════════════════════════════════════════════════════════════════
# 待分配文章与召回
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PendingItem:
    article: ArticleRecord
    analysis: Optional[ArticleAnalysisRecord]
    ledger: Optional[EventAssignmentLedgerRecord]
    rowid: int
    role: str
    shape: str
    entity_codes: frozenset[str]
    title_zh: str
    canonical: str
    norm_title: str
    day: str
    report_at: dt.datetime
    blocked: frozenset[int] = frozenset()
    candidates: list["EventCandidate"] = field(default_factory=list)
    batch_similar: bool = False

    @property
    def summary(self) -> str:
        return str(self.analysis.summary or "") if self.analysis is not None else ""


@dataclass(frozen=True)
class EventCandidate:
    event_id: int
    title: str
    summary: str
    member_count: int
    first_seen_at: str
    first_day: str
    last_member_at: str
    channels: frozenset[str] = frozenset()


def _blocked_ids(ledger: Optional[EventAssignmentLedgerRecord]) -> frozenset[int]:
    if ledger is None:
        return frozenset()
    try:
        data = json.loads(ledger.blocked_event_ids_json or "[]")
    except (TypeError, ValueError):
        return frozenset()
    return frozenset(int(v) for v in data if isinstance(v, (int, float, str)) and str(v).lstrip("-").isdigit())


def report_at_for(publish_date: Optional[str], first_seen: dt.datetime) -> dt.datetime:
    """``report_at = clamp(publish_date, first_seen − 72h, first_seen)``;缺发布时间取首见。"""
    first_seen = _as_utc(first_seen)
    published = parse_article_time(publish_date)
    if published is None:
        return first_seen
    floor = first_seen - dt.timedelta(hours=REPORT_AT_BACKDATE_HOURS)
    return max(floor, min(published, first_seen))


def _pending_item(session: Session, article: ArticleRecord, analysis: Optional[ArticleAnalysisRecord],
                  ledger: Optional[EventAssignmentLedgerRecord], *, now: dt.datetime,
                  meta: Mapping[str, SourceMeta], rowids: Mapping[str, int],
                  entity_codes: Mapping[str, set[str]]) -> PendingItem:
    source_meta = meta.get(article.source_id) or SourceMeta()
    return PendingItem(
        article=article,
        analysis=analysis,
        ledger=ledger,
        rowid=rowids.get(article.id, -1),
        role=source_meta.role,
        shape=source_meta.shape,
        entity_codes=frozenset(entity_codes.get(article.id, ())),
        title_zh=translated_title(article),
        canonical=canonical_url(article.source_url) if _url_direct_allowed(article) else "",
        norm_title=_normalized_title(article.title or ""),
        day=_shanghai_day(article.publish_date or article.fetched_date, now),
        report_at=report_at_for(article.publish_date, now),
        blocked=_blocked_ids(ledger),
    )


def _structurally_excluded(item: PendingItem, open_event: _OpenEvent) -> bool:
    """同源 + 标题全同 + 非同日 → 连载 / 日更栏目,不并(模型前机械执行)。"""
    for member in open_event.members:
        if (
            member.source_id == item.article.source_id
            and _titles_equal(member.norm_title, item.norm_title)
            and member.day != item.day
        ):
            return True
    return False



def _recall_for_item(session: Session, item: PendingItem, context: _OpenContext,
                     *, limit: int = CANDIDATE_LIMIT) -> list[EventCandidate]:
    hits: dict[int, set[str]] = {}
    for code in item.entity_codes:
        for event_id in context.by_entity.get(code, ()):
            hits.setdefault(event_id, set()).add("entity")
    scope = list(context.rowid_to_event)
    if scope:
        channels = (
            ("fts_title", item.article.title),
            ("fts_summary", item.summary),
            ("fts_zh", item.title_zh),
        )
        for channel, text_value in channels:
            if not (text_value or "").strip():
                continue
            for rowid in recall_fts(session, text_value, RECALL_TOP_K, rowids=scope):
                event_id = context.rowid_to_event.get(rowid)
                if event_id is not None:
                    hits.setdefault(event_id, set()).add(channel)
    candidates: list[EventCandidate] = []
    for event_id, channels in hits.items():
        open_event = context.events.get(event_id)
        if open_event is None or event_id in item.blocked or _structurally_excluded(item, open_event):
            continue
        candidates.append(EventCandidate(
            event_id=event_id,
            title=open_event.representative_title,
            summary=open_event.representative_summary,
            member_count=len(open_event.members),
            first_seen_at=open_event.event.first_seen_at,
            first_day=open_event.first_day,
            last_member_at=open_event.event.last_member_at,
            channels=frozenset(channels),
        ))
    candidates.sort(key=lambda c: (c.last_member_at, c.event_id), reverse=True)
    return candidates[:limit]


def recall_candidates(session: Session, article: ArticleRecord, *,
                      analysis: Optional[ArticleAnalysisRecord] = None,
                      now: Optional[dt.datetime] = None,
                      limit: int = CANDIDATE_LIMIT) -> list[EventCandidate]:
    """对一篇文章做三通道召回(实体码 ∪ FTS 标题 / 摘要 / 译中标题),只看开放事件,结构规则剔除后按
    ``last_member_at`` 倒序截 ``limit``。独立可调用(回放 / 排障);批处理内部走同一实现但复用上下文。"""
    now = _as_utc(now)
    context = _build_open_context(session, now)
    if analysis is None:
        analysis = session.get(ArticleAnalysisRecord, article.id)
    ledger = session.get(EventAssignmentLedgerRecord, article.id)
    item = _pending_item(
        session, article, analysis, ledger, now=now,
        meta=load_source_meta(session, [article.source_id]),
        rowids=_rowids_by_article(session, [article.id]),
        entity_codes=entity_codes_by_article(session, [article.id]),
    )
    return _recall_for_item(session, item, context, limit=limit)


# ══════════════════════════════════════════════════════════════════════════════
# 模型判定与解析
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ParsedAssignment:
    idx: int
    target: str                      # event / new / single
    event_id: Optional[int] = None
    new_key: Optional[str] = None
    confidence: Optional[float] = None
    fact: str = ""


@dataclass
class ParsedResponse:
    assignments: dict[int, ParsedAssignment] = field(default_factory=dict)
    new_titles: dict[str, str] = field(default_factory=dict)
    echo_mismatch: int = 0
    illegal_target: int = 0
    low_confidence: int = 0
    empty_fact: int = 0
    echo_samples: list[dict[str, str]] = field(default_factory=list)   # 失配明细:{"title": 前 12 字, "echo": 模型回显}


ECHO_MIN_CHARS = 6


def _echo_key(value: str) -> str:
    """回显比较键:NFKC(全角 → 半角)、大小写折叠、去空白与标点,只留字母 / 数字 / CJK。"""
    folded = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def _echo_matches(echo: Any, title: str) -> bool:
    """回显校验(防 idx 滑位):归一化后 echo 是标题归一化串的前缀即通过;长度只要求模型抄了 ≥ 6 个非空白字符
    (标点计入,短题则全题)。实测模型常把「12 个字符」数成 7–8 个汉字、或在标点处截断,严格等长会把 12% 的文章误判单例。"""
    if not isinstance(echo, str):
        return False
    expected = _echo_key(title)
    got = _echo_key(echo)
    if not got or not expected.startswith(got):
        return False
    written = len(_WS_RE.sub("", unicodedata.normalize("NFKC", echo)))
    return written >= min(ECHO_MIN_CHARS, len(expected))


def _extract_json(raw: str) -> dict[str, Any]:
    text_value = (raw or "").strip()
    if text_value.startswith("```"):
        text_value = re.sub(r"^```[a-zA-Z]*\s*", "", text_value)
        text_value = re.sub(r"\s*```$", "", text_value)
    start, end = text_value.find("{"), text_value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出不含 JSON 对象")
    data = json.loads(text_value[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return data


def _coerce_confidence(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(0.0, min(1.0, number))


def parse_event_assign_response(raw: str, *, titles: Sequence[str],
                                candidates: Sequence[Mapping[int, str]],
                                report_ats: Sequence[dt.datetime]) -> ParsedResponse:
    """解析模型输出并机械兜底(方案 §3.3):eid 不在**该篇自己的**候选集合(含 blocked 剔除后)→ single(R1 #6 ①);
    未声明的 new 键 → single;echo 与标题前缀不符 → single 并计数;并入既有事件且事件首篇早于本文 report_at 24h 以上且
    confidence < 0.8 → single;``new:k`` 组无共同事实 → 各自 single;漏掉的 idx → single。
    ``candidates[idx]`` 为该篇的 eid → first_seen_at(ISO)。输出 JSON 非法时抛 ``ValueError``(调用方按模型失败退避)。"""
    data = _extract_json(raw)
    parsed = ParsedResponse()
    raw_new = data.get("new_events")
    if isinstance(raw_new, list):
        for entry in raw_new:
            if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                parsed.new_titles[entry["key"].strip()] = str(entry.get("title") or "").strip()[:80]
    raw_assignments = data.get("assignments")
    if not isinstance(raw_assignments, list):
        raise ValueError("模型输出缺少 assignments 数组")
    for entry in raw_assignments:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("idx"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(titles) or idx in parsed.assignments:
            continue
        fact = str(entry.get("fact") or "").strip()
        confidence = _coerce_confidence(entry.get("confidence"))
        assignment = ParsedAssignment(idx=idx, target="single", confidence=confidence, fact=fact)
        if not _echo_matches(entry.get("echo"), titles[idx]):
            parsed.echo_mismatch += 1
            parsed.echo_samples.append({
                "title": (titles[idx] or "")[:ECHO_CHARS], "echo": str(entry.get("echo") or "")[:40],
            })
            parsed.assignments[idx] = assignment
            continue
        target = str(entry.get("event") or "single").strip()
        if target.startswith("e") and target[1:].isdigit():
            event_id = int(target[1:])
            own = candidates[idx] if idx < len(candidates) else {}
            if event_id not in own:
                parsed.illegal_target += 1
            else:
                first_seen = _parse_iso(own[event_id])
                cross_day = (
                    first_seen is not None
                    and report_ats[idx] - first_seen > dt.timedelta(hours=CROSS_DAY_HOURS)
                )
                if cross_day and (confidence is None or confidence < CROSS_DAY_MIN_CONFIDENCE):
                    parsed.low_confidence += 1
                else:
                    assignment.target, assignment.event_id = "event", event_id
        elif target.startswith("new:"):
            if target in parsed.new_titles:
                assignment.target, assignment.new_key = "new", target
            else:
                parsed.illegal_target += 1
        elif target != "single":
            parsed.illegal_target += 1
        parsed.assignments[idx] = assignment
    # 批内并组:≥ 2 篇的组须有一句共同事实,否则各自单例;单篇的 new 组就是单例。
    groups: dict[str, list[ParsedAssignment]] = {}
    for assignment in parsed.assignments.values():
        if assignment.target == "new" and assignment.new_key:
            groups.setdefault(assignment.new_key, []).append(assignment)
    for key, members in groups.items():
        if len(members) < 2:
            for member in members:
                member.target, member.new_key = "single", None
            continue
        if not any(len(member.fact) >= MIN_FACT_CHARS for member in members):
            parsed.empty_fact += len(members)
            for member in members:
                member.target, member.new_key = "single", None
    for idx in range(len(titles)):
        parsed.assignments.setdefault(idx, ParsedAssignment(idx=idx, target="single"))
    return parsed


def _call_llm(llm_config: LLMConfig, user_prompt: str) -> str:
    """一次辅助档调用;``chat_completion`` 永不打印 api_key。测试经 monkeypatch 本模块的 ``chat_completion`` 打桩。"""
    return _run_async(chat_completion(
        messages=[
            ChatMessage(role="system", content=EVENT_ASSIGN_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_prompt),
        ],
        config=llm_config.for_aux(),
        temperature=0.1,
        max_tokens=3000,
        response_json=True,
        usage_meta=UsageMeta(purpose="event_assignment", username="system"),
    ))


# ══════════════════════════════════════════════════════════════════════════════
# 落库
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AssignmentDecision:
    """一篇文章的归属决定:``event_id`` 入既有事件;``new_key`` 同键的文章共建一个新事件;两者皆空 = 单例。"""
    article_id: str
    assigned_by: str
    event_id: Optional[int] = None
    new_key: Optional[str] = None
    confidence: Optional[float] = None
    ledger_status: str = LEDGER_ASSIGNED
    ledger_note: str = ""


def _representative_sort_key(member: EventMemberRecord, article: ArticleRecord,
                             analysis: Optional[ArticleAnalysisRecord],
                             meta: Optional[SourceMeta]) -> tuple[Any, ...]:
    """官方 > 非社交 > 分数 > 发布更早 > article_id(与 digest_selection._representative_key 同序)。"""
    role = meta.role if meta else "media"
    shape = meta.shape if meta else "article"
    score = float(analysis.quality_score or 0.0) if analysis is not None else 0.0
    published = parse_article_time(article.publish_date) or parse_article_time(member.report_at)
    return (
        role != "official",
        shape == "social",
        -score,
        published.timestamp() if published else float("inf"),
        article.id,
    )


def _choose_title(ordered: Sequence[tuple[EventMemberRecord, ArticleRecord]], *,
                  llm_title: str, current: EventRecord) -> tuple[str, str]:
    """事件题优先级:中文成员原题(代表稿优先)→ 代表稿 / 任一成员的译题缓存 → 模型拟题 → 代表稿原题。"""
    for _member, article in ordered:
        title = (article.title or "").strip()
        if title and _is_chinese_title(title):
            return title, "zh_member"
    for _member, article in ordered:
        translated = translated_title(article)
        if translated:
            return translated, "translation"
    if llm_title:
        return llm_title, "llm"
    if current.title_source == "llm" and current.title:
        return current.title, "llm"
    representative_title = (ordered[0][1].title or "").strip() if ordered else ""
    return representative_title, "representative"


def _recompute_events(session: Session, event_ids: Iterable[int], *, now: dt.datetime,
                      llm_titles: Mapping[int, str]) -> None:
    ids = sorted({int(v) for v in event_ids})
    if not ids:
        return
    rows: list[tuple[EventMemberRecord, ArticleRecord]] = []
    for chunk in _chunks(ids):
        rows.extend(session.exec(
            select(EventMemberRecord, ArticleRecord)
            .join(ArticleRecord, ArticleRecord.id == EventMemberRecord.article_id)
            .where(EventMemberRecord.event_id.in_(list(chunk)))
        ).all())
    article_ids = [article.id for _member, article in rows]
    analyses: dict[str, ArticleAnalysisRecord] = {}
    for chunk in _chunks(article_ids):
        for analysis in session.exec(
            select(ArticleAnalysisRecord).where(ArticleAnalysisRecord.article_id.in_(list(chunk)))
        ).all():
            analyses[analysis.article_id] = analysis
    meta = load_source_meta(session, {article.source_id for _member, article in rows})
    grouped: dict[int, list[tuple[EventMemberRecord, ArticleRecord]]] = {}
    for member, article in rows:
        grouped.setdefault(int(member.event_id), []).append((member, article))
    stamp = _iso(now)
    for event_id in ids:
        event = session.get(EventRecord, event_id)
        if event is None:
            continue
        members = grouped.get(event_id, [])
        ordered = sorted(
            members,
            key=lambda pair: _representative_sort_key(
                pair[0], pair[1], analyses.get(pair[1].id), meta.get(pair[1].source_id)
            ),
        )
        for member, _article in ordered:
            member.is_representative = False
            session.add(member)
        if ordered:
            ordered[0][0].is_representative = True
            session.add(ordered[0][0])
            event.representative_article_id = ordered[0][1].id
        else:
            event.representative_article_id = None
        event.member_count = len(ordered)
        event.publisher_count = len({member.publisher_key for member, _article in ordered})
        event.title, event.title_source = _choose_title(
            ordered, llm_title=llm_titles.get(event_id, ""), current=event,
        )
        event.updated_at = stamp
        session.add(event)


def _mark_retry(ledger: EventAssignmentLedgerRecord, *, now: dt.datetime, error: str) -> None:
    ledger.attempts = int(ledger.attempts or 0) + 1
    minutes = RETRY_BACKOFF_MINUTES[min(ledger.attempts, len(RETRY_BACKOFF_MINUTES)) - 1]
    ledger.next_attempt_at = _iso(now + dt.timedelta(minutes=minutes))
    ledger.last_error = (error or "")[:300]
    ledger.updated_at = _iso(now)


def _resolve_target_event(session: Session, event_id: int, *, blocked: frozenset[int]) -> tuple[Optional[EventRecord], str]:
    """落库前重验目标(R1 #3):沿 ``event_redirects`` 追到终点;终点须存在、非 ``merged``、本地可写(authority 为空)、
    不在该文章的 blocked 集合。返回 (终点事件, 不合法原因);原因非空即终点不可用。"""
    current = int(event_id)
    seen: set[int] = set()
    while current not in seen:
        seen.add(current)
        redirect = session.get(EventRedirectRecord, current)
        if redirect is None:
            break
        current = int(redirect.new_event_id)
    else:
        return None, "redirect_cycle"
    event = session.get(EventRecord, current)
    if event is None:
        return None, "missing"
    if event.status == "merged":
        return None, "merged"
    if (event.authority_id or "").strip():
        return None, "authority"
    if current in blocked:
        return None, "blocked"
    return event, ""


_Shape = tuple[str, str, str]   # (source_id, 归一化标题, 上海日)


def _shape_of(article: ArticleRecord, now: dt.datetime) -> _Shape:
    return (
        article.source_id,
        _normalized_title(article.title or ""),
        _shanghai_day(article.publish_date or article.fetched_date, now),
    )


def _serial_conflict(shape: _Shape, others: Iterable[_Shape]) -> bool:
    """同源 + 标题全同 + 非同日 → 连载 / 日更栏目,不并。"""
    source_id, norm_title, day = shape
    return any(
        other_source == source_id and _titles_equal(other_title, norm_title) and other_day != day
        for other_source, other_title, other_day in others
    )


def _event_member_shapes(session: Session, event_id: int, now: dt.datetime) -> list[_Shape]:
    rows = session.exec(
        select(ArticleRecord)
        .join(EventMemberRecord, EventMemberRecord.article_id == ArticleRecord.id)
        .where(EventMemberRecord.event_id == int(event_id))
    ).all()
    return [_shape_of(article, now) for article in rows]


def _validate_decisions(session: Session, decisions: Sequence[AssignmentDecision],
                        articles: Mapping[str, ArticleRecord], *, now: dt.datetime,
                        key_events: Optional[Mapping[str, int]] = None) -> tuple[list[AssignmentDecision], dict[str, int]]:
    """落库前统一校验(直连 / 模型 / 批内组一视同仁,R1 #3 #6):既有事件目标经 redirect / merged / authority / blocked
    重验,并按事件级结构规则(同源同题非同日)复核;``new:*`` 组内同样执行结构规则,冲突成员拆成单例。
    ``key_events`` 给出已经落库的组键映射(隔离重放):组键已有事件的决定按「并入已有事件」路径校验——跨文章的
    组内结构状态随前一篇的提交进了库,只校验本篇与组内其它篇就会漏掉(R3 组合路径);冲突篇拆单例,组键映射保留给其它篇。
    返回调整后的决定副本与计数 ``{"invalid_target", "retargeted", "serial_split"}``。"""
    counts = {"invalid_target": 0, "retargeted": 0, "serial_split": 0}
    validated: list[AssignmentDecision] = []
    group_shapes: dict[str, list[_Shape]] = {}
    event_shapes: dict[int, list[_Shape]] = {}
    for decision in decisions:
        article = articles.get(decision.article_id)
        if article is None:
            validated.append(decision)
            continue
        adjusted = AssignmentDecision(**vars(decision))
        shape = _shape_of(article, now)
        if adjusted.event_id is None and adjusted.new_key and key_events and adjusted.new_key in key_events:
            cached = int(key_events[adjusted.new_key])
            blocked = _blocked_ids(session.get(EventAssignmentLedgerRecord, article.id))
            event, reason = _resolve_target_event(session, cached, blocked=blocked)
            if event is None:
                counts["invalid_target"] += 1
                logger.info("组键 %s 的事件 %s 不可用(%s),文章 %s 改为单例", adjusted.new_key, cached, reason, article.id)
                adjusted.new_key, adjusted.assigned_by, adjusted.confidence = None, ASSIGNED_BY_SINGLETON, None
            else:
                if int(event.id) != cached:
                    counts["retargeted"] += 1
                adjusted.event_id, adjusted.new_key = int(event.id), None   # 转成「并入已有事件」,下面按事件成员复核
        if adjusted.event_id is not None:
            blocked = _blocked_ids(session.get(EventAssignmentLedgerRecord, article.id))
            event, reason = _resolve_target_event(session, adjusted.event_id, blocked=blocked)
            if event is None:
                counts["invalid_target"] += 1
                logger.info("事件目标 %s 不可用(%s),文章 %s 改为单例", adjusted.event_id, reason, article.id)
                adjusted.event_id, adjusted.assigned_by, adjusted.confidence = None, ASSIGNED_BY_SINGLETON, None
            else:
                if int(event.id) != int(adjusted.event_id):
                    counts["retargeted"] += 1
                    adjusted.event_id = int(event.id)
                shapes = event_shapes.setdefault(int(event.id), _event_member_shapes(session, int(event.id), now))
                if _serial_conflict(shape, shapes):
                    counts["serial_split"] += 1
                    adjusted.event_id, adjusted.assigned_by, adjusted.confidence = None, ASSIGNED_BY_SINGLETON, None
                else:
                    shapes.append(shape)
        if adjusted.event_id is None and adjusted.new_key:
            shapes = group_shapes.setdefault(adjusted.new_key, [])
            if _serial_conflict(shape, shapes):
                counts["serial_split"] += 1
                adjusted.new_key, adjusted.assigned_by, adjusted.confidence = None, ASSIGNED_BY_SINGLETON, None
            else:
                shapes.append(shape)
        validated.append(adjusted)
    return validated, counts


def _apply_batch(session: Session, decisions: Sequence[AssignmentDecision], *, now: dt.datetime,
                 articles: Mapping[str, ArticleRecord], meta: Mapping[str, SourceMeta],
                 llm_titles: Mapping[str, str], key_events: dict[str, int],
                 key_titles: dict[int, str]) -> tuple[int, set[int]]:
    """把一组(已校验的)决定写进会话(不提交):成员、新事件、ledger。``key_events`` 跨调用共享
    ``new_key → event_id``,逐篇隔离重放时同组成员仍落同一事件。返回 (新成员数, 触及事件 id)。"""
    stamp = _iso(now)
    touched: set[int] = set()
    assigned = 0
    for decision in decisions:
        article = articles.get(decision.article_id)
        ledger = session.get(EventAssignmentLedgerRecord, decision.article_id)
        if article is None:
            if ledger is not None:
                session.delete(ledger)
            continue
        existing = session.exec(
            select(EventMemberRecord).where(EventMemberRecord.article_id == article.id)
        ).first()
        if existing is not None:
            # 已有归属(管理员手工分配 / 并发写入):只收口 ledger,不改成员。
            touched.add(int(existing.event_id))
        else:
            event_id = decision.event_id
            if event_id is None:
                key = decision.new_key or f"single:{article.id}"
                event_id = key_events.get(key)
                if event_id is not None:
                    # 跨事务复用的组事件可能已被管理员合并 / 接管:沿 redirect 取当前终点,不合法就另建(R2 N1)
                    blocked = _blocked_ids(session.get(EventAssignmentLedgerRecord, article.id))
                    current, _reason = _resolve_target_event(session, event_id, blocked=blocked)
                    event_id = int(current.id) if current is not None else None
                    if event_id is None:
                        key_titles.pop(key_events.pop(key), None)
                    else:
                        key_events[key] = event_id
                if event_id is None:
                    event = EventRecord(
                        status="open", title="", title_source="", member_count=0, publisher_count=0,
                        first_seen_at=stamp, last_member_at=stamp,
                        assignment_version=ASSIGNMENT_VERSION, authority_id="",
                        created_at=stamp, updated_at=stamp,
                    )
                    session.add(event)
                    session.flush()
                    event_id = int(event.id)
                    key_events[key] = event_id
                    if decision.new_key and llm_titles.get(decision.new_key):
                        key_titles[event_id] = llm_titles[decision.new_key]
            session.add(EventMemberRecord(
                event_id=event_id,
                article_id=article.id,
                publisher_key=publisher_key(article.source_id, meta.get(article.source_id)),
                report_at=_iso(report_at_for(article.publish_date, now)),
                first_seen_at=stamp,
                assigned_by=decision.assigned_by,
                confidence=decision.confidence if decision.assigned_by == ASSIGNED_BY_LLM else None,
                prompt_version=EVENT_ASSIGN_PROMPT_VERSION if decision.assigned_by == ASSIGNED_BY_LLM else "",
                is_representative=False,
                created_at=stamp,
            ))
            event = session.get(EventRecord, event_id)
            if event is not None:
                event.last_member_at = stamp
                session.add(event)
            touched.add(event_id)
            assigned += 1
        if ledger is None:
            ledger = _new_ledger(article.id, decision.ledger_status, now, decision.ledger_note)
        ledger.status = decision.ledger_status
        ledger.last_error = (decision.ledger_note or "")[:300]
        ledger.next_attempt_at = ""
        ledger.updated_at = stamp
        session.add(ledger)
    session.flush()
    return assigned, touched


def _apply_isolated(session: Session, decision: AssignmentDecision, *, now: dt.datetime,
                    articles: Mapping[str, ArticleRecord], meta: Mapping[str, SourceMeta],
                    llm_titles: Mapping[str, str], key_events: dict[str, int],
                    key_titles: dict[int, str]) -> tuple[int, set[int], dict[str, int]]:
    """隔离重放的一篇:在本篇自己的事务里重新校验目标后写入并提交。异常原样抛出,由调用方回滚并退避。"""
    validated, counts = _validate_decisions(session, [decision], articles, now=now, key_events=key_events)
    one_assigned, one_touched = _apply_batch(
        session, validated, now=now, articles=articles, meta=meta,
        llm_titles=llm_titles, key_events=key_events, key_titles=key_titles,
    )
    _recompute_events(session, one_touched, now=now, llm_titles=key_titles)
    session.commit()
    return one_assigned, one_touched, counts


def apply_assignments(session: Session, decisions: Sequence[AssignmentDecision], *,
                      now: Optional[dt.datetime] = None,
                      llm_titles: Optional[Mapping[str, str]] = None,
                      meta: Optional[Mapping[str, SourceMeta]] = None) -> dict[str, Any]:
    """落库:先 :func:`_validate_decisions` 统一重验(redirect / merged / authority / blocked / 结构规则),再整批一个事务写
    成员、新事件、ledger 并重算事件(计数 / 代表稿 / 事件题)。整批失败时回滚并**逐篇隔离重放**(R1 #7):成功的照常提交,
    失败的那篇 ledger 退避重试,不拖垮同批其它文章。返回 ``{"assigned", "new_events", "event_ids", "events_by_key",
    "invalid_target", "retargeted", "serial_split", "isolated_failures"}``。"""
    now = _as_utc(now)
    llm_titles = dict(llm_titles or {})
    empty = {
        "assigned": 0, "new_events": 0, "event_ids": [], "events_by_key": {},
        "invalid_target": 0, "retargeted": 0, "serial_split": 0, "isolated_failures": 0,
    }
    if not decisions:
        return empty
    article_ids = [decision.article_id for decision in decisions]
    articles: dict[str, ArticleRecord] = {}
    for chunk in _chunks(article_ids):
        for article in session.exec(select(ArticleRecord).where(ArticleRecord.id.in_(list(chunk)))).all():
            articles[article.id] = article
    if meta is None:
        meta = load_source_meta(session, {article.source_id for article in articles.values()})
    validated, counts = _validate_decisions(session, decisions, articles, now=now)
    key_events: dict[str, int] = {}
    key_titles: dict[int, str] = {}
    touched: set[int] = set()
    assigned = 0
    isolated_failures = 0
    try:
        assigned, touched = _apply_batch(
            session, validated, now=now, articles=articles, meta=meta,
            llm_titles=llm_titles, key_events=key_events, key_titles=key_titles,
        )
        _recompute_events(session, touched, now=now, llm_titles=key_titles)
        session.commit()
    except Exception as exc:  # noqa: BLE001 - 整批失败 → 逐篇隔离重放
        session.rollback()
        logger.warning("事件落库整批失败,改为逐篇隔离重放:%s: %s", type(exc).__name__, exc)
        key_events.clear()
        key_titles.clear()
        touched = set()
        assigned = 0
        counts = {"invalid_target": 0, "retargeted": 0, "serial_split": 0}
        # 用**原始**决定逐篇重放:两次提交之间管理员可能合并 / 接管了目标,首轮校验结果已过时(R2 N1),
        # 每篇在自己的事务里重新校验(redirect 终点 / merged / authority / blocked / 结构规则)再写。
        for decision in decisions:
            try:
                one_assigned, one_touched, one_counts = _apply_isolated(
                    session, decision, now=now, articles=articles, meta=meta,
                    llm_titles=llm_titles, key_events=key_events, key_titles=key_titles,
                )
                assigned += one_assigned
                touched |= one_touched
                for key, value in one_counts.items():
                    counts[key] += value
            except Exception as inner:  # noqa: BLE001 - 单篇失败只影响它自己
                session.rollback()
                isolated_failures += 1
                # 本篇 flush 出来又被回滚的新事件不能留在键表里(否则同键后续成员会指向不存在的事件、计数虚高)
                for key in [k for k, v in key_events.items() if session.get(EventRecord, v) is None]:
                    key_titles.pop(key_events.pop(key), None)
                message = f"apply: {type(inner).__name__}: {inner}"
                logger.warning("文章 %s 事件落库失败,退避重试:%s", decision.article_id, message)
                ledger = session.get(EventAssignmentLedgerRecord, decision.article_id)
                if ledger is None:
                    ledger = _new_ledger(decision.article_id, LEDGER_PENDING, now)
                ledger.status = LEDGER_PENDING
                _mark_retry(ledger, now=now, error=message)
                session.add(ledger)
                try:
                    session.commit()
                except Exception:  # noqa: BLE001 - 连记账都失败就放弃这一篇,下轮扫描重来
                    session.rollback()
    return {
        "assigned": assigned,
        "new_events": len(key_events),
        "event_ids": sorted(touched),
        "events_by_key": dict(key_events),
        "invalid_target": counts["invalid_target"],
        "retargeted": counts["retargeted"],
        "serial_split": counts["serial_split"],
        "isolated_failures": isolated_failures,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 批处理与入口
# ══════════════════════════════════════════════════════════════════════════════

def _new_stats() -> dict[str, Any]:
    return {
        "scanned": 0, "direct": 0, "singleton": 0, "llm_assigned": 0, "new_events": 0,
        "failed": 0, "echo_mismatch": 0, "skipped": 0, "closed": 0, "batches": 0,
        "llm_calls": 0, "llm_items": 0, "illegal_target": 0, "low_confidence": 0, "empty_fact": 0,
        "llm_unconfigured": False, "busy": False, "echo_samples": [],
        "invalid_target": 0, "retargeted": 0, "serial_split": 0, "isolated_failures": 0,
        "recall": {
            "articles": 0, "with_candidates": 0, "candidates_total": 0, "batch_similar": 0,
            "entity": 0, "fts_title": 0, "fts_summary": 0, "fts_zh": 0,
        },
    }


def _sweep_expired_pending(session: Session, *, now: dt.datetime,
                           meta_cache: Optional[Mapping[str, SourceMeta]] = None) -> int:
    """``pending`` 超 72h → 机械单例 + ledger ``skipped``(保证早报键始终可用)。"""
    deadline = _iso(now - dt.timedelta(hours=PENDING_MAX_AGE_HOURS))
    expired = session.exec(
        select(EventAssignmentLedgerRecord).where(
            EventAssignmentLedgerRecord.status == LEDGER_PENDING,
            EventAssignmentLedgerRecord.created_at <= deadline,
        )
    ).all()
    if not expired:
        return 0
    decisions = [
        AssignmentDecision(
            article_id=ledger.article_id, assigned_by=ASSIGNED_BY_SINGLETON,
            ledger_status=LEDGER_SKIPPED, ledger_note="pending_timeout",
        )
        for ledger in expired
    ]
    return int(apply_assignments(session, decisions, now=now, meta=meta_cache)["assigned"])


def _load_due_batch(session: Session, *, now: dt.datetime, batch_size: int) -> list[
    tuple[EventAssignmentLedgerRecord, ArticleRecord, Optional[ArticleAnalysisRecord]]
]:
    now_iso = _iso(now)
    ledgers = session.exec(
        select(EventAssignmentLedgerRecord)
        .where(
            EventAssignmentLedgerRecord.status == LEDGER_PENDING,
            (EventAssignmentLedgerRecord.next_attempt_at == "")
            | (EventAssignmentLedgerRecord.next_attempt_at <= now_iso),
        )
        .order_by(EventAssignmentLedgerRecord.created_at.asc(), EventAssignmentLedgerRecord.article_id.asc())
        .limit(max(1, int(batch_size)))
    ).all()
    result = []
    for ledger in ledgers:
        article = session.get(ArticleRecord, ledger.article_id)
        if article is None:
            session.delete(ledger)
            continue
        result.append((ledger, article, session.get(ArticleAnalysisRecord, ledger.article_id)))
    if len(result) != len(ledgers):
        session.commit()
    result.sort(key=lambda row: (parse_article_time(row[1].fetched_date) or now, row[1].id))
    return result


def _direct_connect(item: PendingItem, context: _OpenContext) -> Optional[tuple[int, str]]:
    """机械直连;结构规则按**事件级**执行:事件里只要有一篇同源同题非同日的成员,整个事件都不接(R1 #6 ②,
    否则昨日事件含 A / B 同题稿时,今日 A 的同题稿能借 B 的标题绕过连载规则)。"""

    def _acceptable(event_id: int) -> bool:
        open_event = context.events.get(event_id)
        return (
            open_event is not None
            and event_id not in item.blocked
            and not _structurally_excluded(item, open_event)
        )

    if item.canonical:
        event_id = context.by_canonical.get(item.canonical)
        if event_id is not None and _acceptable(event_id):
            return event_id, ASSIGNED_BY_URL
    key = _title_direct_key(item.article.title or "")
    if key:
        eligible = [
            (event_id, day) for event_id, _source_id, day in context.by_title.get(key, ())
            if _acceptable(event_id)
        ]
        if eligible:
            event_id = next((eid for eid, day in eligible if day == item.day), eligible[0][0])
            return event_id, ASSIGNED_BY_TITLE
    return None


def _mechanical_groups(items: Sequence[PendingItem]) -> dict[str, tuple[PendingItem, str]]:
    """批内机械同组:与本批更早一篇同规范 URL(``url``)或同标题键(``title``,数字串一致、同源非同日除外)的文章
    跟随那篇「组头」的归属——组头并进哪个事件 / 新建哪个事件 / 单例,跟随者就落同一事件。返回 跟随者 id → (组头, 方式)。
    不这样做,同一轮到达的同题稿在模型未配置 / 失败时会被拆成两个单例且永不回溯。"""
    heads_by_canonical: dict[str, PendingItem] = {}
    heads_by_title: dict[str, list[PendingItem]] = {}
    groups: dict[str, list[PendingItem]] = {}          # 组头 id → 组内全部成员(含组头)
    follow: dict[str, tuple[PendingItem, str]] = {}

    def _group_ok(head: PendingItem) -> bool:
        # 组级结构规则:组里任一成员与本文同源同题非同日 → 整组不接(与事件级同口径)
        return not any(
            member.article.source_id == item.article.source_id
            and _titles_equal(member.norm_title, item.norm_title)
            and member.day != item.day
            for member in groups.get(head.article.id, ())
        )

    for item in items:
        head: Optional[PendingItem] = None
        how = ""
        if item.canonical and item.canonical in heads_by_canonical:
            candidate = heads_by_canonical[item.canonical]
            if _group_ok(candidate):
                head, how = candidate, ASSIGNED_BY_URL
        if head is None:
            key = _title_direct_key(item.article.title or "")
            eligible = [c for c in (heads_by_title.get(key, ()) if key else ()) if _group_ok(c)]
            if eligible:
                # 同题多个组头(如同源日更栏目昨天 / 今天各一篇):优先同日那篇,其次最早的。
                head = next((c for c in eligible if c.day == item.day), eligible[0])
                how = ASSIGNED_BY_TITLE
        if head is not None:
            follow[item.article.id] = (head, how)
            groups.setdefault(head.article.id, [head]).append(item)
            continue
        groups.setdefault(item.article.id, [item])
        if item.canonical:
            heads_by_canonical.setdefault(item.canonical, item)
        key = _title_direct_key(item.article.title or "")
        if key:
            heads_by_title.setdefault(key, []).append(item)
    return follow


def _batch_similarity(session: Session, items: Sequence[PendingItem]) -> None:
    """零候选的文章若与本批其他文章共享实体码、标题明显同一或 FTS 互相命中,仍交模型判(批内并组);否则直接单例。"""
    rowid_to_item = {item.rowid: item for item in items if item.rowid >= 0}
    for item in items:
        if item.batch_similar:
            continue
        for other in items:
            if other is item:
                continue
            if (item.entity_codes & other.entity_codes) or (
                len(item.norm_title) >= MIN_TITLE_DIRECT_CHARS
                and len(other.norm_title) >= MIN_TITLE_DIRECT_CHARS
                and _titles_look_same(item.norm_title, other.norm_title)
            ):
                item.batch_similar = other.batch_similar = True
        if item.batch_similar:
            continue
        others = [rowid for rowid, other in rowid_to_item.items() if other is not item]
        for text_value in (item.article.title, item.summary, item.title_zh):
            if not (text_value or "").strip():
                continue
            for rowid in recall_fts(session, text_value, RECALL_TOP_K, rowids=others):
                other = rowid_to_item.get(rowid)
                if other is not None:
                    item.batch_similar = other.batch_similar = True
            if item.batch_similar:
                break


def _process_batch(session: Session, rows: Sequence[tuple[EventAssignmentLedgerRecord, ArticleRecord,
                                                          Optional[ArticleAnalysisRecord]]],
                   llm_config: LLMConfig, *, now: dt.datetime, stats: dict[str, Any]) -> bool:
    """处理一批 pending;返回 False 表示模型调用失败(调用方停止本轮继续取批)。"""
    context = _build_open_context(session, now)
    article_ids = [article.id for _ledger, article, _analysis in rows]
    meta = load_source_meta(session, {article.source_id for _ledger, article, _analysis in rows})
    rowids = _rowids_by_article(session, article_ids)
    entity_codes = entity_codes_by_article(session, article_ids)
    all_items = [
        _pending_item(session, article, analysis, ledger, now=now, meta=meta, rowids=rowids,
                      entity_codes=entity_codes)
        for ledger, article, analysis in rows
    ]
    follow = _mechanical_groups(all_items)
    items = [item for item in all_items if item.article.id not in follow]
    decisions: list[AssignmentDecision] = []
    undecided: list[PendingItem] = []

    # 1. 机械直连(URL / 同题),命中即登记进上下文,后续同批文章可继续直连 / 召回到它。
    for item in items:
        hit = _direct_connect(item, context)
        if hit is None:
            undecided.append(item)
            continue
        event_id, assigned_by = hit
        decisions.append(AssignmentDecision(article_id=item.article.id, assigned_by=assigned_by, event_id=event_id))
        context.register(event_id, _member_info(item.article, item.rowid, now), item.entity_codes)
        stats["direct"] += 1

    # 2. 召回 + 批内相似判定。
    recall_stats = stats["recall"]
    for item in undecided:
        item.candidates = _recall_for_item(session, item, context)
        recall_stats["articles"] += 1
        if item.candidates:
            recall_stats["with_candidates"] += 1
            recall_stats["candidates_total"] += len(item.candidates)
            for channel in {c for cand in item.candidates for c in cand.channels}:
                recall_stats[channel] = recall_stats.get(channel, 0) + 1
    _batch_similarity(session, undecided)
    to_judge = [item for item in undecided if item.candidates or item.batch_similar]
    recall_stats["batch_similar"] += sum(1 for item in undecided if not item.candidates and item.batch_similar)
    for item in undecided:
        if item not in to_judge:
            decisions.append(AssignmentDecision(article_id=item.article.id, assigned_by=ASSIGNED_BY_SINGLETON))
            stats["singleton"] += 1

    # 3. 一次辅助模型调用判整批。
    llm_titles: dict[str, str] = {}
    failed = False
    if to_judge:
        if not llm_config.configured:
            stats["llm_unconfigured"] = True
            for item in to_judge:
                decisions.append(AssignmentDecision(article_id=item.article.id, assigned_by=ASSIGNED_BY_SINGLETON))
                stats["singleton"] += 1
        else:
            candidate_map: dict[int, EventCandidate] = {}
            for item in to_judge:
                for candidate in item.candidates:
                    candidate_map.setdefault(candidate.event_id, candidate)
            new_items = [
                {
                    "idx": idx, "title": item.article.title or "", "role": item.role, "day": item.day,
                    "candidates": [c.event_id for c in item.candidates],
                }
                for idx, item in enumerate(to_judge)
            ]
            candidate_events = [
                {
                    "eid": candidate.event_id, "title": candidate.title, "summary": candidate.summary,
                    "member_count": candidate.member_count, "first_day": candidate.first_day,
                }
                for candidate in sorted(candidate_map.values(), key=lambda c: c.event_id)
            ]
            user_prompt = build_event_assign_user_prompt(new_items, candidate_events)
            stats["llm_calls"] += 1
            stats["llm_items"] += len(to_judge)
            try:
                raw = _call_llm(llm_config, user_prompt)
                parsed = parse_event_assign_response(
                    raw,
                    titles=[item.article.title or "" for item in to_judge],
                    candidates=[{c.event_id: c.first_seen_at for c in item.candidates} for item in to_judge],
                    report_ats=[item.report_at for item in to_judge],
                )
            except Exception as exc:  # noqa: BLE001 - 模型失败 / 输出非法:退避重试,不抛到调用方
                failed = True
                message = f"{type(exc).__name__}: {exc}"
                logger.warning("事件分配模型调用失败,%s 篇退避重试:%s", len(to_judge), message)
                retried = {item.article.id for item in to_judge}
                followers = [
                    item for item in all_items
                    if item.article.id in follow and follow[item.article.id][0].article.id in retried
                ]
                for item in [*to_judge, *followers]:
                    if item.ledger is not None:
                        _mark_retry(item.ledger, now=now, error=message if item in to_judge else "head_retry")
                        session.add(item.ledger)
                stats["failed"] += len(to_judge) + len(followers)
            else:
                stats["echo_mismatch"] += parsed.echo_mismatch
                stats["echo_samples"].extend(parsed.echo_samples[:20])
                stats["illegal_target"] += parsed.illegal_target
                stats["low_confidence"] += parsed.low_confidence
                stats["empty_fact"] += parsed.empty_fact
                llm_titles = dict(parsed.new_titles)
                for idx, item in enumerate(to_judge):
                    verdict = parsed.assignments[idx]
                    if verdict.target == "event":
                        decisions.append(AssignmentDecision(
                            article_id=item.article.id, assigned_by=ASSIGNED_BY_LLM,
                            event_id=verdict.event_id, confidence=verdict.confidence,
                        ))
                        stats["llm_assigned"] += 1
                    elif verdict.target == "new":
                        decisions.append(AssignmentDecision(
                            article_id=item.article.id, assigned_by=ASSIGNED_BY_LLM,
                            new_key=verdict.new_key, confidence=verdict.confidence,
                        ))
                        stats["llm_assigned"] += 1
                    else:
                        decisions.append(AssignmentDecision(
                            article_id=item.article.id, assigned_by=ASSIGNED_BY_SINGLETON,
                        ))
                        stats["singleton"] += 1

    # 4. 批内跟随者落到组头所在事件;组头仍 pending(模型失败退避)时跟随者一并留待下轮。
    by_article = {decision.article_id: decision for decision in decisions}
    for follower_id, (head, how) in follow.items():
        head_decision = by_article.get(head.article.id)
        if head_decision is None:
            continue
        if head_decision.event_id is None and head_decision.new_key is None:
            head_decision.new_key = f"single:{head.article.id}"
        decisions.append(AssignmentDecision(
            article_id=follower_id, assigned_by=how,
            event_id=head_decision.event_id, new_key=head_decision.new_key,
        ))
        stats["direct"] += 1

    # 5. 单事务落库(直连 / 单例的决定不依赖模型,模型失败时照常落)。
    outcome = apply_assignments(session, decisions, now=now, llm_titles=llm_titles, meta=meta)
    stats["new_events"] += int(outcome["new_events"])
    for key in ("invalid_target", "retargeted", "serial_split", "isolated_failures"):
        stats[key] += int(outcome[key])
    stats["batches"] += 1
    if failed:
        session.commit()  # 退避账已 add,随本次提交
    return not failed


def _assign_pending_locked(engine: Engine, llm_config: LLMConfig, *, now: dt.datetime,
                           batch_size: int, stats: dict[str, Any]) -> dict[str, Any]:
    with Session(engine) as session:
        stats["closed"] = close_stale_events(session, now=now)
        stats["scanned"] = scan_pending(session, now=now)
        stats["skipped"] += _sweep_expired_pending(session, now=now)
        for _ in range(MAX_BATCHES_PER_RUN):
            rows = _load_due_batch(session, now=now, batch_size=batch_size)
            if not rows:
                break
            if not _process_batch(session, rows, llm_config, now=now, stats=stats):
                break
        if stats["llm_unconfigured"]:
            logger.info("LLM 未配置,事件分配本轮全部按单成员事件落库(%s 篇)", stats["singleton"])
    return stats


def assign_pending(engine: Engine, llm_config: LLMConfig, *, now: Optional[dt.datetime] = None,
                   batch_size: int = 20) -> dict[str, Any]:
    """分析周期末尾 / 10 分钟兜底 job 的入口(同步;异步调用方 ``asyncio.to_thread``)。

    返回统计:``scanned`` 新入池、``direct`` 机械直连、``singleton`` 单例、``llm_assigned`` 模型归并(含批内并组)、
    ``new_events`` 新建事件、``failed`` 退避篇数、``echo_mismatch`` 回显失配、``skipped`` 72h 兜底、``closed`` 懒关窗、
    ``recall`` 召回统计;另一次运行在途时直接返回 ``busy=True``。任何异常记日志后原样抛出,由调用方的 try/except 兜住。
    """
    stats = _new_stats()
    if not _ASSIGN_LOCK.acquire(blocking=False):
        stats["busy"] = True
        return stats
    try:
        return _assign_pending_locked(
            engine, llm_config, now=_as_utc(now), batch_size=max(1, int(batch_size)), stats=stats,
        )
    finally:
        _ASSIGN_LOCK.release()


__all__ = [
    "ASSIGNMENT_VERSION",
    "AssignmentDecision",
    "EventCandidate",
    "ParsedAssignment",
    "ParsedResponse",
    "PendingItem",
    "SourceMeta",
    "apply_assignments",
    "assign_pending",
    "canonical_url",
    "close_stale_events",
    "entity_codes_by_article",
    "translated_title",
    "load_source_meta",
    "parse_event_assign_response",
    "publisher_key",
    "recall_candidates",
    "recall_fts",
    "recall_tokens",
    "report_at_for",
    "scan_pending",
]
