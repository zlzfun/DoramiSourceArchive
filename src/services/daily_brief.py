"""每日 AI 资讯日报编排 (src/services/daily_brief.py)

流程：预处理(collect_candidates) → map_summarize(每篇 LLM 概括+打分)
     → dedup_clusters(同日同事件聚类) → select_top(按分数+多样性择优)
     → cross_day_dedup(跨天查重,一次轻量 LLM) → render_brief_markdown(确定性渲染)
     → 写库(幂等 update)。

v3.34 起 reduce 不再是整篇 LLM 长输出:markdown 由代码从结构化条目排版,
LLM 在汇编段只做「对照近几天日报条目标题判断 drop/接前报」的小 JSON 决策——
截断/漏条/复制篡改类静默劣化就此根除(2026-08 空正文事故的形态性风险消除)。

三层去重：
  ① 确定性水位线游标 daily_brief_cursor（fetched_date），写库成功后才推进；
  ② dedup_clusters 同日同事件聚类合并；
  ③ cross_day_dedup 对照近期日报条目跨天去重（纯重复剔除/后续进展标注增量）。

运行记录走 AppSettingRecord（KV），不新建 ORM 表。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlmodel import Session, select

import config
from llm.client import (
    ChatMessage, LLMError, LLMNotConfigured, UsageMeta,
    chat_completion, client_session, parse_json_object,
)
from llm import prompts

# 日报各阶段的 LLM 用量归属：手动触发归到触发它的 admin，定时调度无登录上下文则归 "system"。
USAGE_SYSTEM = "system"


def _usage_meta(purpose: str, username: Optional[str]) -> UsageMeta:
    return UsageMeta(purpose=purpose, username=(username or USAGE_SYSTEM))
from models.content import DailyBriefContent
from models.db import AppSettingRecord, ArticleRecord
from services import credentials

logger = logging.getLogger("dorami.daily_brief")


# ==========================================
# 生成进度（内存，仅供前端轮询；单进程有效，不持久化）
# ==========================================

_PROGRESS: Dict[str, Any] = {"phase": "idle", "message": "", "done": 0, "total": 0, "updated_at": 0.0}


def set_progress(phase: str, message: str = "", *, done: int = 0, total: int = 0) -> None:
    """更新当前生成阶段。phase ∈ idle/collecting/mapping/selecting/reducing/persisting/done/empty/error。"""
    _PROGRESS.update({
        "phase": phase, "message": message, "done": done, "total": total, "updated_at": time.time(),
    })


def get_progress() -> Dict[str, Any]:
    return dict(_PROGRESS)

# --- 常量 ---
DAILY_BRIEF_SOURCE_ID = "dorami_daily_brief"
DAILY_BRIEF_CONTENT_TYPE = "daily_brief"
DEFAULT_DAILY_BRIEF_CRON = "30 8 * * *"  # 排在 07:10 全量采集之后
DEFAULT_TOP_N = 12        # 日报精选条数默认值
TOP_N_MIN = 1
TOP_N_MAX = 50

# AppSettingRecord 键
KEY_CURSOR = "daily_brief_cursor"
KEY_ENABLED = "daily_brief_enabled"
KEY_CRON = "daily_brief_cron"
KEY_TOP_N = "daily_brief_top_n"
KEY_SOURCE_IDS = "daily_brief_source_ids"
KEY_LAST_RUN = "daily_brief_last_run"
# LLM 配置的 KV key 沿用 services/credentials 注册表(与历史存量一致,零迁移)。
KEY_LLM_BASE_URL = credentials.LLM_NAMESPACE.field_by_name("base_url").kv_key
KEY_LLM_MODEL = credentials.LLM_NAMESPACE.field_by_name("model").kv_key
KEY_LLM_TEMPERATURE = credentials.LLM_NAMESPACE.field_by_name("temperature").kv_key
KEY_LLM_MAX_TOKENS = credentials.LLM_NAMESPACE.field_by_name("max_tokens").kv_key
KEY_LLM_API_KEY = credentials.LLM_NAMESPACE.field_by_name("api_key").kv_key


# ==========================================
# 数据结构
# ==========================================

@dataclass
class BriefCandidate:
    id: str
    title: str
    source_id: str
    source_url: str
    content_type: str
    publish_date: str
    fetched_date: str
    has_content: bool
    body: str


@dataclass
class ScoredItem:
    candidate: BriefCandidate
    title_cn: str = ""
    classification: str = ""
    source: str = ""
    company: str = ""
    realm: str = ""
    summary: List[str] = field(default_factory=list)
    comment: str = ""
    tags: List[str] = field(default_factory=list)
    score: float = 0.0
    map_ok: bool = True
    # 同事件去重合并后，被并入本条的其它来源链接（供 reduce 渲染多来源）
    extra_sources: List[str] = field(default_factory=list)
    # 跨天查重判定为「同一事件的后续进展」时的一句增量说明（渲染成「（接前报）」行）
    followup_note: str = ""

    def to_reduce_dict(self) -> Dict[str, Any]:
        return {
            "title_cn": self.title_cn or self.candidate.title,
            "source_url": self.candidate.source_url,
            "source": self.source,
            "publish_date": self.candidate.publish_date,
            "content_type": self.candidate.content_type,
            "classification": self.classification,
            "company": self.company,
            "realm": self.realm,
            "summary": self.summary,
            "comment": self.comment,
            "tags": self.tags,
            "score": self.score,
            "extra_sources": self.extra_sources,
        }


# ==========================================
# KV 读写 helper
# ==========================================

def get_setting(session: Session, key: str, default: str = "") -> str:
    record = session.get(AppSettingRecord, key)
    return record.value if record is not None else default


def set_setting(session: Session, key: str, value: str) -> None:
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()


def get_json_setting(session: Session, key: str, default: Any = None) -> Any:
    raw = get_setting(session, key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def set_json_setting(session: Session, key: str, value: Any) -> None:
    set_setting(session, key, json.dumps(value, ensure_ascii=False))


def daily_brief_enabled(session: Session) -> bool:
    return get_setting(session, KEY_ENABLED, "false").lower() == "true"


def daily_brief_cron(session: Session) -> str:
    return get_setting(session, KEY_CRON, DEFAULT_DAILY_BRIEF_CRON) or DEFAULT_DAILY_BRIEF_CRON


def daily_brief_top_n(session: Session) -> int:
    """读取精选条数配置，越界则夹到 [TOP_N_MIN, TOP_N_MAX]。"""
    raw = get_setting(session, KEY_TOP_N, "")
    try:
        value = int(raw) if raw else DEFAULT_TOP_N
    except ValueError:
        value = DEFAULT_TOP_N
    return max(TOP_N_MIN, min(TOP_N_MAX, value))


def read_source_scope(session: Session) -> Optional[List[str]]:
    """日报候选的源范围名单(手工维护,用户拍板 2026-07-17):

    - None = 未配置 → 全部源(向后兼容既有行为);
    - 非空名单 → 候选只取名单内的源。新增源(含未来的 X 动态类导入源)默认
      **不进**日报,由 admin 在日报配置页显式勾入——不做形态/tier 规则过滤,
      高噪即时源的取舍交给名单 + map 阶段 LLM 打分。
    - 空名单视同 None(防呆:空名单必然产出空日报,基本是误操作)。
    """
    raw = get_setting(session, KEY_SOURCE_IDS, "")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list):
        return None
    ids = sorted({str(v).strip() for v in value if str(v).strip()})
    return ids or None


def write_source_scope(session: Session, source_ids: Optional[List[str]]) -> None:
    """写日报源范围名单;空/None → 清空配置(回到全部源)。"""
    ids = sorted({str(v).strip() for v in (source_ids or []) if str(v).strip()})
    set_setting(session, KEY_SOURCE_IDS, json.dumps(ids, ensure_ascii=False) if ids else "")


# ==========================================
# LLM 配置合并（ini 默认 ∪ KV 运行期覆盖）
# ==========================================

def resolve_llm_config(session: Session) -> config.LLMConfig:
    """合并 ini/env 默认配置与 KV 运行期覆盖，产出有效 LLMConfig。"""
    base = config.settings.llm
    values = credentials.resolve_values(session, credentials.LLM_NAMESPACE, base)
    return config.LLMConfig(
        timeout_seconds=base.timeout_seconds,
        map_concurrency=base.map_concurrency,
        **values,
    )


# ==========================================
# 阶段 1：预处理
# ==========================================

def read_cursor(session: Session) -> str:
    return get_setting(session, KEY_CURSOR, "")


def collect_candidates(
    session: Session,
    *,
    cursor: str,
    max_total: int = 120,
    per_source_cap: int = 15,
    source_ids: Optional[List[str]] = None,
) -> Tuple[List[BriefCandidate], str, int]:
    """取游标之后新入库的文章作为候选。

    返回 (candidates, max_fetched_seen, scanned_total)。max_fetched_seen 是裁剪前
    扫描到的最大 fetched_date，用于推进游标（避免下次重复处理已看过但被裁剪的
    条目）。scanned_total 是裁剪前的扫描总数——per_source_cap/max_total 裁掉的
    条目会随游标永久跳过，扫描/取用两个读数写进日志与 last_run，裁剪不再静默。
    游标为空（首次或手动重置）时不设时间地板，按 fetched_date 倒序取最新
    max_total 篇重做——成本由 max_total 上限兜住，不会全库进 LLM。

    source_ids 非空时只扫描名单内的源(read_source_scope 的手工名单):范围外
    文章不进扫描、也不推进游标——之后把某源加入名单,其游标后的积压会一次性
    进入候选(由 per_source_cap/max_total 兜住),新纳入源立刻有内容,符合预期。
    """
    # 空游标 → "" ，fetched_date > "" 命中全部，靠下方倒序 + max_total 截断取最新批
    effective_cursor = cursor or ""

    statement = (
        select(ArticleRecord)
        .where(ArticleRecord.fetched_date > effective_cursor)
        .where(ArticleRecord.source_id != DAILY_BRIEF_SOURCE_ID)  # 防自我递归
        .order_by(ArticleRecord.fetched_date.desc())
    )
    if source_ids:
        statement = statement.where(ArticleRecord.source_id.in_(list(source_ids)))
    rows = session.exec(statement).all()

    max_fetched_seen = cursor
    for row in rows:
        if row.fetched_date and row.fetched_date > max_fetched_seen:
            max_fetched_seen = row.fetched_date

    # 去重 + per-source 裁剪 + 总量裁剪（rows 已按 fetched_date 倒序，保留较新）
    seen_ids: set[str] = set()
    per_source_count: Dict[str, int] = {}
    candidates: List[BriefCandidate] = []
    for row in rows:
        if row.id in seen_ids:
            continue
        seen_ids.add(row.id)
        count = per_source_count.get(row.source_id, 0)
        if count >= per_source_cap:
            continue
        per_source_count[row.source_id] = count + 1
        candidates.append(
            BriefCandidate(
                id=row.id,
                title=row.title or "",
                source_id=row.source_id or "",
                source_url=row.source_url or "",
                content_type=row.content_type or "",
                publish_date=row.publish_date or "",
                fetched_date=row.fetched_date or "",
                has_content=bool(row.has_content and row.content),
                body=row.content or "",
            )
        )
        if len(candidates) >= max_total:
            break

    return candidates, (max_fetched_seen or effective_cursor), len(rows)


# ==========================================
# 阶段 2：Map（每篇 LLM 概括 + 打分）
# ==========================================

async def _summarize_one(
    candidate: BriefCandidate, llm_config: config.LLMConfig,
    usage_meta: Optional[UsageMeta] = None, http_client=None,
) -> ScoredItem:
    try:
        user_prompt = prompts.build_map_user_prompt(
            title=candidate.title,
            source_name=candidate.source_id,
            body=candidate.body,
        )
        raw = await chat_completion(
            messages=[
                ChatMessage(role="system", content=prompts.MAP_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            config=llm_config,
            response_json=True,
            usage_meta=usage_meta,
            http_client=http_client,
        )
        data = parse_json_object(raw)
        return ScoredItem(
            candidate=candidate,
            title_cn=str(data.get("title_cn") or candidate.title),
            classification=str(data.get("classification") or ""),
            source=str(data.get("source") or candidate.source_id),
            company=str(data.get("company") or ""),
            realm=str(data.get("realm") or ""),
            summary=[str(s) for s in (data.get("summary") or []) if s],
            comment=str(data.get("comment") or ""),
            tags=[str(t) for t in (data.get("tags") or []) if t],
            score=_coerce_score(data.get("score")),
            map_ok=True,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001 单篇失败降级，不中断整体
        logger.warning("日报 map 单篇失败 (id=%s): %s", candidate.id, exc)
        return ScoredItem(
            candidate=candidate,
            title_cn=candidate.title,
            source=candidate.source_id,
            summary=[],
            score=3.0,
            map_ok=False,
        )


def _coerce_score(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 3.0
    return max(0.0, min(10.0, value))


async def map_summarize(
    candidates: List[BriefCandidate],
    llm_config: config.LLMConfig,
    *,
    on_item_done=None,
    usage_username: Optional[str] = None,
) -> List[ScoredItem]:
    """对有正文的候选并发 LLM 概括。无正文候选不进 map（reduce 单列附录）。
    on_item_done(done, total) 每完成一篇回调一次，供上层上报进度。
    走辅助轻模型档（未配置 aux_model 时即主模型），整个 map 段共享一个连接池。"""
    with_body = [c for c in candidates if c.has_content]
    if not with_body:
        return []
    total = len(with_body)
    done = 0
    usage_meta = _usage_meta("daily_brief_map", usage_username)
    map_config = llm_config.for_aux()
    semaphore = asyncio.Semaphore(max(1, llm_config.map_concurrency))

    async with client_session(map_config) as http_client:
        async def _guarded(c: BriefCandidate) -> ScoredItem:
            nonlocal done
            async with semaphore:
                result = await _summarize_one(c, map_config, usage_meta, http_client)
            done += 1
            if on_item_done is not None:
                on_item_done(done, total)
            return result

        return await asyncio.gather(*[_guarded(c) for c in with_body])


# ==========================================
# 阶段 2.5：Dedup（同事件去重聚类，一次性 LLM 调用）
# ==========================================

async def dedup_clusters(
    items: List[ScoredItem],
    llm_config: config.LLMConfig,
    usage_username: Optional[str] = None,
) -> List[ScoredItem]:
    """识别同一天里报道同一事件的重复条目，每组只保留 score 最高的代表，
    其余条目的 source_url 并入代表的 extra_sources。LLM 失败时降级为不聚类
    （返回原列表），不阻断主流程。"""
    if len(items) < 2:
        return items
    entries = [
        {
            "idx": i,
            "title": it.title_cn or it.candidate.title,
            "company": it.company,
            "hint": (it.summary[0] if it.summary else ""),
        }
        for i, it in enumerate(items)
    ]
    try:
        raw = await chat_completion(
            messages=[
                ChatMessage(role="system", content=prompts.DEDUP_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompts.build_dedup_user_prompt(entries)),
            ],
            config=llm_config.for_aux(),  # 聚类是轻量结构化判断,走辅助档
            response_json=True,
            usage_meta=_usage_meta("daily_brief_dedup", usage_username),
        )
        data = parse_json_object(raw)
        clusters = data.get("clusters") or []
    except (LLMError, Exception) as exc:  # noqa: BLE001 去重失败降级，不中断整体
        logger.warning("日报去重聚类失败，降级为不聚类: %s", exc)
        return items

    n = len(items)
    dropped: set[int] = set()
    for group in clusters:
        # 规整为去重后的合法 idx 列表
        idxs = sorted({int(g) for g in group if isinstance(g, (int, float)) and 0 <= int(g) < n})
        if len(idxs) < 2:
            continue
        # 组内已被其它组消化掉的代表不再重复处理
        idxs = [i for i in idxs if i not in dropped]
        if len(idxs) < 2:
            continue
        rep = max(idxs, key=lambda i: items[i].score)
        for i in idxs:
            if i == rep:
                continue
            url = items[i].candidate.source_url
            if url and url not in items[rep].extra_sources and url != items[rep].candidate.source_url:
                items[rep].extra_sources.append(url)
            dropped.add(i)

    if dropped:
        logger.info("日报去重：%d 条同事件重复合并到代表条目", len(dropped))
    return [it for i, it in enumerate(items) if i not in dropped]


# ==========================================
# 阶段 3：Select（按分数 + 来源/领域多样性择优）
# ==========================================

# 论文类 classification（占比受 paper_cap 限制，避免论文淹没行业资讯）
PAPER_CLASSIFICATION = "学术论文"


def _is_paper(item: ScoredItem) -> bool:
    return item.classification == PAPER_CLASSIFICATION or item.candidate.content_type == "arxiv"


def select_top(
    items: List[ScoredItem],
    *,
    top_n: int = 30,
    per_source_cap: int = 5,
    per_realm_cap: int = 8,
    paper_cap: int = 3,
) -> List[ScoredItem]:
    ranked = sorted(items, key=lambda it: it.score, reverse=True)
    selected: List[ScoredItem] = []
    overflow: List[ScoredItem] = []
    source_count: Dict[str, int] = {}
    realm_count: Dict[str, int] = {}
    paper_count = 0
    for item in ranked:
        if len(selected) >= top_n:
            break
        src = item.candidate.source_id
        realm = item.realm or "未分类"
        # 论文配额：超额的论文丢进 overflow（仅在凑不满时才回补），压低论文占比
        if _is_paper(item) and paper_count >= paper_cap:
            overflow.append(item)
            continue
        if source_count.get(src, 0) >= per_source_cap or realm_count.get(realm, 0) >= per_realm_cap:
            overflow.append(item)
            continue
        selected.append(item)
        source_count[src] = source_count.get(src, 0) + 1
        realm_count[realm] = realm_count.get(realm, 0) + 1
        if _is_paper(item):
            paper_count += 1
    # 多样性配额导致不足时，用 overflow 中分数最高者补满
    if len(selected) < top_n:
        for item in overflow:
            if len(selected) >= top_n:
                break
            selected.append(item)
    # 多样性配额只决定"哪些条目入选"；最终顺序统一按重要性（score）降序，
    # 使日报 markdown 与导出 items（shendeng sort）都呈重要性排序。
    selected.sort(key=lambda it: it.score, reverse=True)
    return selected


# ==========================================
# 阶段 4：汇编（v3.34 确定性渲染 + 跨天查重）
# reduce 不再是整篇 LLM 长输出：markdown 由代码从结构化条目排版；
# LLM 只做「对照近期日报条目标题判断 drop/接前报」的小 JSON 决策。
# ==========================================

# 内容里提取「### [标题](url)」/「### 标题」标题行的回退正则（extensions.items 缺失时用）
_BRIEF_HEADING_RE = re.compile(r"^###\s+\[?([^\]\n]+?)\]?(?:\(|$)", re.M)
# 跨天查重单天注入的条目标题上限（近几天日报每天几十条，控 prompt 预算）
_RECENT_TITLES_PER_DAY = 40
# 「（接前报）」增量注的长度上限
_FOLLOWUP_NOTE_CHARS = 60


def fetch_recent_brief_items(session: Session, *, days: int = 3) -> List[Dict[str, Any]]:
    """近几天日报的条目标题清单（跨天查重的对照物）。

    优先读 extensions.items（结构化 title_cn），缺失时回退从正文提取「###」
    标题行；返回形如 [{"date": "YYYY-MM-DD", "titles": [...]}, ...]。
    """
    statement = (
        select(ArticleRecord)
        .where(ArticleRecord.source_id == DAILY_BRIEF_SOURCE_ID)
        .order_by(ArticleRecord.publish_date.desc())
        .limit(days)
    )
    out: List[Dict[str, Any]] = []
    for row in session.exec(statement).all():
        titles: List[str] = []
        try:
            ext = json.loads(row.extensions_json or "{}")
            items = ext.get("items") if isinstance(ext, dict) else None
            for it in items or []:
                if isinstance(it, dict):
                    title = str(it.get("title_cn") or "").strip()
                    if title:
                        titles.append(title)
        except (ValueError, TypeError):
            pass
        if not titles and row.content:
            titles = [m.strip() for m in _BRIEF_HEADING_RE.findall(row.content) if m.strip()]
        if titles:
            out.append({"date": (row.publish_date or "")[:10], "titles": titles[:_RECENT_TITLES_PER_DAY]})
    return out


async def cross_day_dedup(
    items: List[ScoredItem],
    recent_items: List[Dict[str, Any]],
    llm_config: config.LLMConfig,
    usage_username: Optional[str] = None,
) -> List[ScoredItem]:
    """对照近几天日报条目做跨天查重：纯重复剔除、后续进展标注一句增量注。

    LLM 失败/输出异常降级为不查重（原列表返回）；判定要求丢弃全部条目时
    视为误判忽略 drop（安全阀）。计费归入 daily_brief_reduce（沿用原 reduce
    的用途口径），走辅助轻模型档。
    """
    if not items or not recent_items:
        return items
    entries = [
        {
            "idx": i,
            "title": it.title_cn or it.candidate.title,
            "company": it.company,
            "hint": (it.summary[0] if it.summary else ""),
        }
        for i, it in enumerate(items)
    ]
    try:
        raw = await chat_completion(
            messages=[
                ChatMessage(role="system", content=prompts.CROSS_DAY_DEDUP_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompts.build_cross_day_dedup_user_prompt(entries, recent_items)),
            ],
            config=llm_config.for_aux(),
            response_json=True,
            usage_meta=_usage_meta("daily_brief_reduce", usage_username),
        )
        data = parse_json_object(raw)
    except (LLMError, Exception) as exc:  # noqa: BLE001 查重失败降级，不中断汇编
        logger.warning("日报跨天查重失败，降级为不查重: %s", exc)
        return items

    n = len(items)
    drops = {
        int(i) for i in (data.get("drop") or [])
        if isinstance(i, (int, float)) and 0 <= int(i) < n
    }
    if drops and len(drops) >= n:
        logger.warning("日报跨天查重要求丢弃全部 %d 条，疑似误判，忽略 drop", n)
        drops = set()
    for entry in data.get("followups") or []:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("idx")
        note = str(entry.get("note") or "").strip()
        if isinstance(idx, (int, float)) and 0 <= int(idx) < n and note and int(idx) not in drops:
            items[int(idx)].followup_note = note[:_FOLLOWUP_NOTE_CHARS]
    if drops:
        logger.info("日报跨天查重：剔除 %d 条与近期日报重复的条目", len(drops))
    return [it for i, it in enumerate(items) if i not in drops]


def _entry_markdown(item: ScoredItem) -> str:
    """单条目的日报 markdown（格式与 REDUCE_SYSTEM_PROMPT 的风格契约一致）。"""
    data = item.to_reduce_dict()
    title = str(data["title_cn"] or "").strip() or "（无标题）"
    url = (data["source_url"] or "").strip()
    heading = f"### [{title}]({url})" if url else f"### {title}"
    source_name = (data["source"] or "").strip() or item.candidate.source_id or "未知来源"
    date = (data["publish_date"] or "")[:10]
    source_line = f"**来源**: {source_name}" + (f" · {date}" if date else "")
    for extra in data["extra_sources"]:
        netloc = urlparse(extra).netloc or "另见"
        source_line += f" · [{netloc}]({extra})"
    lines = [heading, source_line]
    if item.followup_note:
        lines.append(f"*（接前报）{item.followup_note}*")
    if data["summary"]:
        lines.append("核心总结：")
        lines.extend(f"- {s}" for s in data["summary"])
    comment = str(data["comment"] or "").strip()
    if comment:
        lines.append(f"> 💡 点评：{comment}")
    return "\n".join(lines)


def render_brief_markdown(
    selected: List[ScoredItem],
    title_only: List[BriefCandidate],
    *,
    report_date: str,
) -> str:
    """把择优条目确定性渲染成日报 markdown（分节/条目格式忠实沿用原 reduce 契约）。

    selected 已按 score 降序（select_top 出口），分节内顺序即重要性顺序。
    """
    sections: Dict[str, List[ScoredItem]] = {}
    for item in selected:
        label = prompts.classification_label(item.classification, item.candidate.content_type)
        sections.setdefault(label, []).append(item)

    parts: List[str] = [
        f"# 🤖 哆啦美 AI 资讯日报 · {report_date}",
        "",
        f"> 共收录 {len(selected) + len(title_only)} 条资讯，涵盖 {len(sections)} 个分类",
        "",
        "---",
        "",
    ]
    for label in prompts.section_label_order():
        items = sections.get(label)
        if not items:
            continue
        parts.append(f"## {label}（{len(items)} 篇）")
        parts.append("")
        for item in items:
            parts.append(_entry_markdown(item))
            parts.append("")
        parts.append("---")
        parts.append("")
    if title_only:
        parts.append("## 📎 其它收录")
        parts.append("")
        for c in title_only:
            title = (c.title or "").strip() or "（无标题）"
            parts.append(f"- [{title}]({c.source_url})" if c.source_url else f"- {title}")
        parts.append("")
        parts.append("---")
        parts.append("")
    parts.append("*由哆啦美·归档中枢生成*")
    return "\n".join(parts)


# ==========================================
# 主编排
# ==========================================

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _record_last_run(session: Session, payload: Dict[str, Any]) -> None:
    set_json_setting(session, KEY_LAST_RUN, payload)


async def generate_daily_brief(
    *,
    storage,
    llm_config: Optional[config.LLMConfig] = None,
    report_date: Optional[str] = None,
    trigger: str = "manual",
    triggered_by: Optional[str] = None,
    dry_run: bool = False,
    max_total: int = 120,
    per_source_cap: int = 15,
    top_n: Optional[int] = None,
    recent_brief_days: int = 3,
) -> Dict[str, Any]:
    """生成日报主流程。storage 为 DatabaseStorage 实例（提供 .engine 与 save/get/update）。

    triggered_by：手动触发的 admin 用户名，用于 AI 用量归属；定时调度留空则归 "system"。
    """
    report_date = report_date or _today()
    started_at = datetime.now().isoformat()
    engine = storage.engine
    logger.info("日报[%s]：开始生成（trigger=%s, dry_run=%s）", report_date, trigger, dry_run)
    set_progress("collecting", "正在筛选候选内容…")

    # 1. 解析配置
    with Session(engine) as session:
        cfg = llm_config or resolve_llm_config(session)
    if not cfg.configured:
        set_progress("error", "LLM 未配置")
        raise LLMNotConfigured("LLM 未配置（需在设置中填写 base_url / api_key / model）")

    # 2. 取候选（top_n 未显式指定时读配置）
    with Session(engine) as session:
        if top_n is None:
            top_n = daily_brief_top_n(session)
        cursor_before = read_cursor(session)
        source_scope = read_source_scope(session)
        candidates, max_fetched_seen, scanned_total = collect_candidates(
            session, cursor=cursor_before, max_total=max_total,
            per_source_cap=per_source_cap, source_ids=source_scope,
        )
    n_body = sum(1 for c in candidates if c.has_content)
    logger.info(
        "日报[%s]：扫描 %d 篇 → 取用候选 %d 篇（有正文 %d，per_source_cap/max_total 裁剪 %d——被裁条目随游标跳过）",
        report_date, scanned_total, len(candidates), n_body, scanned_total - len(candidates),
    )

    # 3. 空日报：不写库、不推进游标
    if not candidates:
        logger.info("日报[%s]：无新增候选，跳过生成", report_date)
        set_progress("empty", "暂无新增内容")
        result = {
            "status": "empty",
            "report_date": report_date,
            "articles_count": 0,
            "trigger": trigger,
        }
        if not dry_run:
            with Session(engine) as session:
                _record_last_run(session, {
                    "status": "empty", "started_at": started_at,
                    "ended_at": datetime.now().isoformat(), "report_date": report_date,
                    "article_id": None, "articles_count": 0, "error_message": None,
                    "candidates_scanned": scanned_total, "candidates_used": 0,
                })
        return result

    # 4. map → select → reduce
    set_progress("mapping", f"概括打分 0/{n_body}", done=0, total=n_body)

    def _on_map_done(done: int, total: int) -> None:
        set_progress("mapping", f"概括打分 {done}/{total}", done=done, total=total)
        if done == total or done % 5 == 0:
            logger.info("日报[%s]：Map 概括打分 %d/%d", report_date, done, total)

    scored = await map_summarize(candidates, cfg, on_item_done=_on_map_done, usage_username=triggered_by)

    set_progress("selecting", "同事件去重与择优排序…")
    deduped = await dedup_clusters(scored, cfg, usage_username=triggered_by)
    logger.info("日报[%s]：去重后 %d 条（map 前 %d）", report_date, len(deduped), len(scored))
    # map 失败的条目没有总结/点评且 score 是占位值——不参与择优（曾以默认 3 分
    # 混入正选,渲染出无总结无点评的残条目),降入「📎 其它收录」附录保标题与链接。
    map_failed = [it.candidate for it in deduped if not it.map_ok]
    usable = [it for it in deduped if it.map_ok]
    if map_failed:
        logger.info("日报[%s]：map 失败 %d 条降入「其它收录」附录", report_date, len(map_failed))
    selected = select_top(usable, top_n=top_n)
    title_only = [c for c in candidates if not c.has_content] + map_failed
    logger.info("日报[%s]：择优 %d 条（+ 仅标题 %d 条）", report_date, len(selected), len(title_only))

    with Session(engine) as session:
        recent_items = fetch_recent_brief_items(session, days=recent_brief_days)

    set_progress("reducing", "跨天查重与汇编…")
    logger.info("日报[%s]：跨天查重（对照近期日报 %d 天条目）后确定性渲染", report_date, len(recent_items))
    selected = await cross_day_dedup(selected, recent_items, cfg, usage_username=triggered_by)
    markdown = render_brief_markdown(selected, title_only, report_date=report_date)

    if dry_run:
        set_progress("done", "预览生成完成")
        return {
            "status": "dry_run",
            "report_date": report_date,
            "articles_count": len(selected) + len(title_only),
            "markdown": markdown,
        }

    set_progress("persisting", "写入与分发…")
    # 5. 组装内容
    included_ids = [it.candidate.id for it in selected] + [c.id for c in title_only]
    categories = {it.classification or it.candidate.content_type for it in selected}
    article_id = f"daily_brief_{report_date}"
    generated_at = datetime.now().isoformat()
    content_obj = DailyBriefContent(
        id=article_id,
        title=f"哆啦美 AI 资讯日报 · {report_date}",
        source_url="",
        publish_date=report_date,
        content=markdown,
        has_content=True,
        report_date=report_date,
        articles_count=len(selected) + len(title_only),
        categories_count=len(categories),
        included_article_ids=included_ids,
        items=[it.to_reduce_dict() for it in selected],
        cursor_before=cursor_before,
        cursor_after=max_fetched_seen,
        llm_model=cfg.model,
        generated_at=generated_at,
    )
    content_obj.source_id = DAILY_BRIEF_SOURCE_ID

    # 6. 写库（幂等：已存在则 update 覆盖，否则 save）
    await _persist_brief(storage, content_obj)

    # 7. 写库成功后推进游标
    with Session(engine) as session:
        set_setting(session, KEY_CURSOR, max_fetched_seen)
        _record_last_run(session, {
            "status": "success", "started_at": started_at,
            "ended_at": datetime.now().isoformat(), "report_date": report_date,
            "article_id": article_id, "articles_count": content_obj.articles_count,
            "error_message": None,
            # 候选裁剪观测(v3.34):扫描≫取用 说明 max_total/per_source_cap 在裁,
            # 被裁条目随游标永久跳过——涨不涨上限看这两个数。
            "candidates_scanned": scanned_total, "candidates_used": len(candidates),
        })

    logger.info("日报[%s]：生成完成，收录 %d 条", report_date, content_obj.articles_count)
    set_progress("done", f"完成 · 收录 {content_obj.articles_count} 条")

    return {
        "status": "success",
        "report_date": report_date,
        "article_id": article_id,
        "articles_count": content_obj.articles_count,
        "categories_count": content_obj.categories_count,
        "trigger": trigger,
    }


async def _persist_brief(storage, content_obj: DailyBriefContent) -> None:
    """写日报。db_storage.save() 不覆盖已有 has_content 记录，故同日重跑走 update。"""
    from models.content import serialize_to_metadata

    # 落库前断言正文非空:空正文若放行,save() 的 summary 兜底会把空串写成 NULL
    # 且 has_content=True 照写,阅读器呈现「暂无正文」而运行记录是 success——
    # 2026-08 生产事故的静默半边。宁可失败留游标,下轮带着候选重来。
    if not (content_obj.content or "").strip():
        raise RuntimeError("日报正文为空,拒绝写库(疑似 LLM 输出被思考/截断耗尽,检查 max_tokens 与思考模式)")

    existing = await storage.get(content_obj.id)
    if existing is None:
        ok = await storage.save(content_obj)
        if not ok:
            raise RuntimeError(f"日报写库失败 (id={content_obj.id})")
        return
    metadata = serialize_to_metadata(content_obj)
    await storage.update(content_obj.id, {
        "title": content_obj.title,
        "content_type": DAILY_BRIEF_CONTENT_TYPE,
        "source_id": DAILY_BRIEF_SOURCE_ID,
        "publish_date": content_obj.publish_date,
        "fetched_date": content_obj.fetched_date,
        "has_content": True,
        "content": content_obj.content,
        "extensions_json": json.dumps(metadata.get("extensions", {}), ensure_ascii=False),
    })
