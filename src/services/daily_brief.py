"""每日 AI 资讯日报编排 (src/services/daily_brief.py)

流程：预处理(collect_candidates) → map_summarize(每篇 LLM 概括+打分)
     → select_top(按分数+多样性择优) → reduce_to_markdown(汇总) → 写库(幂等 update)。

双层去重：
  ① 确定性水位线游标 daily_brief_cursor（fetched_date），写库成功后才推进；
  ② reduce 阶段注入近期日报正文，LLM 处理同一事件的语义/重复。

运行记录走 AppSettingRecord（KV），不新建 ORM 表。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

import config
from llm.client import ChatMessage, LLMError, LLMNotConfigured, chat_completion, parse_json_object
from llm import prompts
from models.content import DailyBriefContent
from models.db import AppSettingRecord, ArticleRecord

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
KEY_LAST_RUN = "daily_brief_last_run"
KEY_LLM_BASE_URL = "llm_base_url"
KEY_LLM_MODEL = "llm_model"
KEY_LLM_TEMPERATURE = "llm_temperature"
KEY_LLM_MAX_TOKENS = "llm_max_tokens"
KEY_LLM_API_KEY = "llm_api_key"


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


# ==========================================
# LLM 配置合并（ini 默认 ∪ KV 运行期覆盖）
# ==========================================

def resolve_llm_config(session: Session) -> config.LLMConfig:
    """合并 ini/env 默认配置与 KV 运行期覆盖，产出有效 LLMConfig。"""
    base = config.settings.llm

    def _str(key: str, fallback: str) -> str:
        val = get_setting(session, key, "")
        return val if val else fallback

    def _float(key: str, fallback: float) -> float:
        val = get_setting(session, key, "")
        try:
            return float(val) if val else fallback
        except ValueError:
            return fallback

    def _int(key: str, fallback: int) -> int:
        val = get_setting(session, key, "")
        try:
            return int(val) if val else fallback
        except ValueError:
            return fallback

    return config.LLMConfig(
        base_url=_str(KEY_LLM_BASE_URL, base.base_url),
        api_key=_str(KEY_LLM_API_KEY, base.api_key),
        model=_str(KEY_LLM_MODEL, base.model),
        timeout_seconds=base.timeout_seconds,
        temperature=_float(KEY_LLM_TEMPERATURE, base.temperature),
        max_tokens=_int(KEY_LLM_MAX_TOKENS, base.max_tokens),
        map_concurrency=base.map_concurrency,
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
) -> Tuple[List[BriefCandidate], str]:
    """取游标之后新入库的文章作为候选。

    返回 (candidates, max_fetched_seen)。max_fetched_seen 是裁剪前扫描到的最大
    fetched_date，用于推进游标（避免下次重复处理已看过但被裁剪的条目）。
    游标为空（首次或手动重置）时不设时间地板，按 fetched_date 倒序取最新
    max_total 篇重做——成本由 max_total 上限兜住，不会全库进 LLM。
    """
    # 空游标 → "" ，fetched_date > "" 命中全部，靠下方倒序 + max_total 截断取最新批
    effective_cursor = cursor or ""

    statement = (
        select(ArticleRecord)
        .where(ArticleRecord.fetched_date > effective_cursor)
        .where(ArticleRecord.source_id != DAILY_BRIEF_SOURCE_ID)  # 防自我递归
        .order_by(ArticleRecord.fetched_date.desc())
    )
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

    return candidates, (max_fetched_seen or effective_cursor)


# ==========================================
# 阶段 2：Map（每篇 LLM 概括 + 打分）
# ==========================================

async def _summarize_one(candidate: BriefCandidate, llm_config: config.LLMConfig) -> ScoredItem:
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
) -> List[ScoredItem]:
    """对有正文的候选并发 LLM 概括。无正文候选不进 map（reduce 单列附录）。
    on_item_done(done, total) 每完成一篇回调一次，供上层上报进度。"""
    with_body = [c for c in candidates if c.has_content]
    if not with_body:
        return []
    total = len(with_body)
    done = 0
    semaphore = asyncio.Semaphore(max(1, llm_config.map_concurrency))

    async def _guarded(c: BriefCandidate) -> ScoredItem:
        nonlocal done
        async with semaphore:
            result = await _summarize_one(c, llm_config)
        done += 1
        if on_item_done is not None:
            on_item_done(done, total)
        return result

    return await asyncio.gather(*[_guarded(c) for c in with_body])


# ==========================================
# 阶段 3：Select（按分数 + 来源/领域多样性择优）
# ==========================================

def select_top(
    items: List[ScoredItem],
    *,
    top_n: int = 30,
    per_source_cap: int = 5,
    per_realm_cap: int = 8,
) -> List[ScoredItem]:
    ranked = sorted(items, key=lambda it: it.score, reverse=True)
    selected: List[ScoredItem] = []
    overflow: List[ScoredItem] = []
    source_count: Dict[str, int] = {}
    realm_count: Dict[str, int] = {}
    for item in ranked:
        if len(selected) >= top_n:
            break
        src = item.candidate.source_id
        realm = item.realm or "未分类"
        if source_count.get(src, 0) >= per_source_cap or realm_count.get(realm, 0) >= per_realm_cap:
            overflow.append(item)
            continue
        selected.append(item)
        source_count[src] = source_count.get(src, 0) + 1
        realm_count[realm] = realm_count.get(realm, 0) + 1
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
# 阶段 4：Reduce（汇总成 Markdown）
# ==========================================

def fetch_recent_briefs(session: Session, *, days: int = 3) -> List[str]:
    statement = (
        select(ArticleRecord)
        .where(ArticleRecord.source_id == DAILY_BRIEF_SOURCE_ID)
        .order_by(ArticleRecord.publish_date.desc())
        .limit(days)
    )
    return [row.content for row in session.exec(statement).all() if row.content]


async def reduce_to_markdown(
    selected: List[ScoredItem],
    title_only: List[BriefCandidate],
    recent_briefs: List[str],
    *,
    report_date: str,
    llm_config: config.LLMConfig,
) -> str:
    selected_dicts = [it.to_reduce_dict() for it in selected]
    title_only_dicts = [
        {"title": c.title, "source_url": c.source_url, "source": c.source_id,
         "publish_date": c.publish_date, "content_type": c.content_type}
        for c in title_only
    ]
    user_prompt = prompts.build_reduce_user_prompt(
        report_date=report_date,
        selected_items=selected_dicts,
        title_only_items=title_only_dicts,
        recent_briefs=recent_briefs,
    )
    system_prompt = prompts.REDUCE_SYSTEM_PROMPT.replace("{report_date}", report_date)
    return await chat_completion(
        messages=[
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ],
        config=llm_config,
        # reduce 输出较长，放宽 token 上限
        max_tokens=max(llm_config.max_tokens, 4096),
    )


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
    dry_run: bool = False,
    max_total: int = 120,
    per_source_cap: int = 15,
    top_n: Optional[int] = None,
    recent_brief_days: int = 3,
) -> Dict[str, Any]:
    """生成日报主流程。storage 为 DatabaseStorage 实例（提供 .engine 与 save/get/update）。"""
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
        candidates, max_fetched_seen = collect_candidates(
            session, cursor=cursor_before, max_total=max_total, per_source_cap=per_source_cap
        )
    n_body = sum(1 for c in candidates if c.has_content)
    logger.info("日报[%s]：取到候选 %d 篇（有正文 %d）", report_date, len(candidates), n_body)

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
                })
        return result

    # 4. map → select → reduce
    set_progress("mapping", f"概括打分 0/{n_body}", done=0, total=n_body)

    def _on_map_done(done: int, total: int) -> None:
        set_progress("mapping", f"概括打分 {done}/{total}", done=done, total=total)
        if done == total or done % 5 == 0:
            logger.info("日报[%s]：Map 概括打分 %d/%d", report_date, done, total)

    scored = await map_summarize(candidates, cfg, on_item_done=_on_map_done)

    set_progress("selecting", "按重要性择优排序…")
    selected = select_top(scored, top_n=top_n)
    title_only = [c for c in candidates if not c.has_content]
    logger.info("日报[%s]：择优 %d 条（+ 仅标题 %d 条）", report_date, len(selected), len(title_only))

    with Session(engine) as session:
        recent_briefs = fetch_recent_briefs(session, days=recent_brief_days)

    set_progress("reducing", "汇编日报正文…")
    logger.info("日报[%s]：开始汇编（reduce），注入近期日报 %d 篇", report_date, len(recent_briefs))
    markdown = await reduce_to_markdown(
        selected, title_only, recent_briefs, report_date=report_date, llm_config=cfg
    )

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
        "is_vectorized": False,
    })
