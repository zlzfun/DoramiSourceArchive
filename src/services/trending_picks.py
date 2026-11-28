"""GitHub Trending 精读:第一个不是公共日报的生成流程(issue #72)。

取材 → 加工 → 产出:

- **取材**:最新一条 GitHub Trending 日榜记录(``github_trending_daily``,一天一条,``items_json`` 是结构化榜单)。
  不触发抓取。日榜记录的 id 与日期按 **UTC** 记(北京时间早上采到的是前一天那条),所以这里不按日期对,
  认的是「最新一条有没有被写过精读」。
- **加工**:辅助模型一次调用,从榜单里挑几个仓库,各写一句说明与一句「为什么值得看」。模型只返回结构化条目。
- **产出**:系统源 ``dorami_trending_picks`` 下的一条 ``trending_picks`` 条目,正文由代码按条目确定性渲染
  (与公共日报同一个思路:条目是枢纽,Markdown 是渲染)。一天一期,按北京时间的日期幂等。

不回流:这个源的条目不送入库分析(目录条目 ``analysis = false``),也不进公共日报候选、个人早报、榜单
(系统源名单排除,见 ``services.source_catalog.system_source_ids``)。

外壳(登记、开关、定时、手动触发、进度、上次结果)在 ``services.generation_flows``。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

import config
from llm import prompts
from llm.client import (
    ChatMessage,
    LLMError,
    LLMNotConfigured,
    UsageMeta,
    chat_completion,
    parse_json_object,
)
from models.content import TrendingPicksContent
from models.db import ArticleRecord
from services import daily_brief as kv   # KV 读写(get_setting / set_setting / *_json_setting)与模型配置解析在那个模块里
from services.generated_content import persist_generated_article

logger = logging.getLogger("dorami.trending_picks")

FLOW_ID = "trending_picks"
FLOW_NAME = "GitHub Trending 精读"
SOURCE_ID = "dorami_trending_picks"
CONTENT_TYPE = TrendingPicksContent.content_type
INPUT_SOURCE_ID = "github_trending_daily"
INPUT_SOURCE_NAME = "GitHub Trending 日榜"

DEFAULT_CRON = "30 9 * * *"     # 排在 08:30 的公共日报之后;日榜要另有采集任务每天在采
PHASES: Tuple[str, ...] = ("读榜", "挑选", "成稿")
PHASE_INDEX = {"reading": 0, "picking": 1, "writing": 2}

KEY_ENABLED = f"flow:{FLOW_ID}:enabled"
KEY_CRON = f"flow:{FLOW_ID}:cron"
KEY_CONFIG = f"flow:{FLOW_ID}:config"
KEY_LAST_RUN = f"flow:{FLOW_ID}:last_run"

PICK_COUNT_DEFAULT = 6
PICK_COUNT_MIN = 1
PICK_COUNT_MAX = 10
AI_ONLY_DEFAULT = True

USAGE_PURPOSE = "trending_picks"
USAGE_SYSTEM = "system"
_TEXT_LIMIT = 160           # 模型写的每一句的上限(提示词要的是 40 / 50 字,这里只挡跑飞的输出)
_USED_LOOKBACK = 60         # 查「这条日榜写没写过」时回看多少期

SKIP_NO_TRENDING = "还没有采到 GitHub Trending 日榜"
SKIP_NO_NEW_TRENDING = "没有新榜单"
SKIP_NO_AI_REPOS = "榜单里没有 AI 相关的仓库"


# ── 进度(内存,只供前端轮询;单进程有效,不持久化)────────────────────────────────

_PROGRESS: Dict[str, Any] = {"phase": "idle", "message": "", "done": 0, "total": 0, "updated_at": 0.0}


def set_progress(phase: str, message: str = "", *, done: int = 0, total: int = 0) -> None:
    """phase ∈ idle / reading / picking / writing / done / skipped / error。"""
    _PROGRESS.update({"phase": phase, "message": message, "done": done, "total": total, "updated_at": time.time()})


def get_progress() -> Dict[str, Any]:
    return dict(_PROGRESS)


# ── 配置 ──────────────────────────────────────────────────────────────────────


def is_enabled(session: Session) -> bool:
    """缺省关:升级后这个流程出现在「生成流程」页,管理员打开才开始跑。"""
    return kv.get_setting(session, KEY_ENABLED, "false").lower() == "true"


def cron(session: Session) -> str:
    return kv.get_setting(session, KEY_CRON, DEFAULT_CRON) or DEFAULT_CRON


def read_config(session: Session) -> Dict[str, Any]:
    raw = kv.get_json_setting(session, KEY_CONFIG, {})
    raw = raw if isinstance(raw, dict) else {}
    try:
        pick_count = int(raw.get("pick_count", PICK_COUNT_DEFAULT))
    except (TypeError, ValueError):
        pick_count = PICK_COUNT_DEFAULT
    return {
        "pick_count": max(PICK_COUNT_MIN, min(pick_count, PICK_COUNT_MAX)),
        "ai_only": bool(raw.get("ai_only", AI_ONLY_DEFAULT)),
    }


def validate_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """校验一份(可以只含部分键的)配置;不合法抛 ``ValueError``,合法返回规整后的字典。"""
    if not isinstance(patch, dict):
        raise ValueError("配置必须是对象")
    unknown = sorted(set(patch) - {"pick_count", "ai_only"})
    if unknown:
        raise ValueError(f"未知的配置项:{unknown}")
    clean: Dict[str, Any] = {}
    if "pick_count" in patch:
        value = patch["pick_count"]
        if isinstance(value, bool) or not isinstance(value, int) or not (PICK_COUNT_MIN <= value <= PICK_COUNT_MAX):
            raise ValueError(f"挑选个数需在 {PICK_COUNT_MIN}–{PICK_COUNT_MAX} 之间")
        clean["pick_count"] = value
    if "ai_only" in patch:
        if not isinstance(patch["ai_only"], bool):
            raise ValueError("「只看 AI 相关」必须是开或关")
        clean["ai_only"] = patch["ai_only"]
    return clean


def write_config(session: Session, patch: Dict[str, Any]) -> None:
    clean = validate_config(patch)
    if clean:
        kv.set_json_setting(session, KEY_CONFIG, {**read_config(session), **clean})


def last_run(session: Session) -> Optional[Dict[str, Any]]:
    value = kv.get_json_setting(session, KEY_LAST_RUN, None)
    return value if isinstance(value, dict) else None


# ── 取材:最新一条日榜 ─────────────────────────────────────────────────────────


def latest_ranking(session: Session) -> Optional[ArticleRecord]:
    return session.exec(
        select(ArticleRecord)
        .where(ArticleRecord.source_id == INPUT_SOURCE_ID)
        .order_by(ArticleRecord.publish_date.desc(), ArticleRecord.fetched_date.desc())
        .limit(1)
    ).first()


def _extensions(record: ArticleRecord) -> Dict[str, Any]:
    try:
        value = json.loads(record.extensions_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def ranking_items(record: ArticleRecord) -> List[Dict[str, Any]]:
    """日榜记录里的结构化榜单;读不出来返回空表。"""
    raw = _extensions(record).get("items_json")
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in (items or []) if isinstance(item, dict) and str(item.get("repo") or "").strip()]


def period_using(session: Session, ranking_id: str) -> Optional[ArticleRecord]:
    """已经用这条日榜写过的那一期(没有返回 None)。只回看最近若干期:一条日榜不会隔很久才被再用。"""
    rows = session.exec(
        select(ArticleRecord)
        .where(ArticleRecord.source_id == SOURCE_ID)
        .order_by(ArticleRecord.publish_date.desc())
        .limit(_USED_LOOKBACK)
    ).all()
    for row in rows:
        if _extensions(row).get("trending_article_id") == ranking_id:
            return row
    return None


def has_new_ranking(session: Session) -> bool:
    """有没有一条还没写过精读的日榜(泳道「取材」一格据此提示)。"""
    ranking = latest_ranking(session)
    return ranking is not None and period_using(session, ranking.id) is None


# ── 加工:挑选 ────────────────────────────────────────────────────────────────


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:_TEXT_LIMIT]


def validate_picks(
    payload: Dict[str, Any], items: List[Dict[str, Any]], *, pick_count: int, ai_only: bool
) -> List[Dict[str, Any]]:
    """把模型的回答对到榜单上:榜单里没有的仓库、缺说明的、重复的都丢掉;``ai_only`` 时只留 AI 一组。"""
    by_repo = {str(item["repo"]).strip().lower(): item for item in items}
    raw = payload.get("picks") if isinstance(payload, dict) else None
    picks: List[Dict[str, Any]] = []
    seen = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("repo") or "").strip().lower()
        item = by_repo.get(key)
        summary = _clean_text(entry.get("summary"))
        if item is None or key in seen or not summary:
            continue
        group = "ai" if str(entry.get("group") or "").strip().lower() == "ai" else "tool"
        if ai_only and group != "ai":
            continue
        seen.add(key)
        picks.append({
            "rank": item.get("rank"),
            "repo": item["repo"],
            "url": item.get("url") or f"https://github.com/{item['repo']}",
            "description": _clean_text(item.get("description")),
            "language": str(item.get("language") or ""),
            "stars": item.get("stars") or 0,
            "stars_today": str(item.get("stars_today") or ""),
            "group": group,
            "summary": summary,
            "why": _clean_text(entry.get("why")),
        })
        if len(picks) >= pick_count:
            break
    return picks


async def pick_repositories(
    items: List[Dict[str, Any]], *, pick_count: int, ai_only: bool,
    llm_config: config.LLMConfig, triggered_by: Optional[str],
) -> List[Dict[str, Any]]:
    """一次辅助档调用。``chat_completion`` 永不打印 api_key;测试经 monkeypatch 本模块的 ``chat_completion`` 打桩。"""
    system = prompts.TRENDING_PICKS_SYSTEM_PROMPT.format(
        pick_count=pick_count,
        scope_rule=prompts.TRENDING_PICKS_SCOPE_AI_ONLY if ai_only else prompts.TRENDING_PICKS_SCOPE_ANY,
    )
    board = [
        {key: item.get(key) for key in ("rank", "repo", "description", "language", "stars", "stars_today")}
        for item in items
    ]
    text = await chat_completion(
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=json.dumps(board, ensure_ascii=False)),
        ],
        config=llm_config.for_aux(),
        temperature=0.2,
        max_tokens=2000,
        response_json=True,
        usage_meta=UsageMeta(purpose=USAGE_PURPOSE, username=(triggered_by or USAGE_SYSTEM)),
    )
    if not (text or "").strip():
        raise LLMError("模型返回为空")
    return validate_picks(parse_json_object(text), items, pick_count=pick_count, ai_only=ai_only)


# ── 产出:确定性渲染 ───────────────────────────────────────────────────────────

_GROUP_TITLES = (("ai", "AI 相关"), ("tool", "开发工具与项目"))


def _heat(item: Dict[str, Any]) -> str:
    today = re.sub(r"[^\d,]", "", str(item.get("stars_today") or ""))
    try:
        stars = f"{int(item.get('stars') or 0):,}"
    except (TypeError, ValueError):
        stars = str(item.get("stars") or "")
    parts = [f"⭐ {stars}" + (f"(+{today} today)" if today else ""), str(item.get("language") or "")]
    return " · ".join(part for part in parts if part)


def render_markdown(picks: List[Dict[str, Any]], items: List[Dict[str, Any]], *, ranking_title: str) -> str:
    """条目 → 正文。分两组(AI 相关 / 开发工具与项目),各组内保持模型给的顺序;榜单其余的一行带过。"""
    blocks: List[str] = [f"> 选自 {ranking_title}(共 {len(items)} 个仓库)"]
    for group, title in _GROUP_TITLES:
        chosen = [pick for pick in picks if pick["group"] == group]
        if not chosen:
            continue
        blocks.append(f"### {title}")
        for pick in chosen:
            lines = [f"**[{pick['repo']}]({pick['url']})** — {_heat(pick)}", pick["summary"]]
            if pick["why"]:
                lines.append(f"**为什么值得看**:{pick['why']}")
            blocks.append("\n\n".join(lines))
    picked = {pick["repo"] for pick in picks}
    rest = [item for item in items if item["repo"] not in picked]
    if rest:
        blocks.append("### 榜单其余")
        blocks.append(" · ".join(
            f"[{item['repo']}]({item.get('url') or 'https://github.com/' + str(item['repo'])})" for item in rest
        ))
    return "\n\n".join(blocks)


# ── 生成 ──────────────────────────────────────────────────────────────────────

_LOCK = threading.Lock()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _record(session: Session, payload: Dict[str, Any]) -> None:
    kv.set_json_setting(session, KEY_LAST_RUN, payload)


def _skip(engine, *, started_at: str, report_date: str, trigger: str, reason: str) -> Dict[str, Any]:
    logger.info("精读[%s]：跳过（%s）", report_date, reason)
    set_progress("skipped", reason)
    with Session(engine) as session:
        _record(session, {
            "status": "skipped", "reason": reason, "started_at": started_at,
            "ended_at": datetime.now().isoformat(), "report_date": report_date,
            "article_id": None, "error_message": None,
        })
    return {"status": "skipped", "reason": reason, "report_date": report_date, "trigger": trigger}


async def generate(
    *,
    storage,
    trigger: str = "manual",
    triggered_by: Optional[str] = None,
    llm_config: Optional[config.LLMConfig] = None,
) -> Dict[str, Any]:
    """跑一次。成功 / 跳过都记进「上次结果」并返回;失败记下原因再把异常抛给调用方。"""
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("精读正在生成，请等待当前任务结束")
    started_at = datetime.now().isoformat()
    report_date = _today()
    try:
        return await _generate(
            storage=storage, trigger=trigger, triggered_by=triggered_by, llm_config=llm_config,
            started_at=started_at, report_date=report_date,
        )
    except Exception as exc:  # noqa: BLE001 记下失败再抛:泳道上要看得到「今天失败 · 原因」
        message = str(exc) or type(exc).__name__
        set_progress("error", message)
        try:
            with Session(storage.engine) as session:
                _record(session, {
                    "status": "failed", "started_at": started_at, "ended_at": datetime.now().isoformat(),
                    "report_date": report_date, "article_id": None, "error_message": message,
                })
        except Exception:  # noqa: BLE001 记不下来不该盖住原来的错误
            logger.warning("精读[%s]：失败原因没能记下", report_date, exc_info=True)
        raise
    finally:
        _LOCK.release()


async def _generate(
    *, storage, trigger: str, triggered_by: Optional[str], llm_config: Optional[config.LLMConfig],
    started_at: str, report_date: str,
) -> Dict[str, Any]:
    engine = storage.engine
    article_id = f"{FLOW_ID}_{report_date}"
    logger.info("精读[%s]：开始生成（trigger=%s）", report_date, trigger)
    set_progress("reading", "读取最新一期日榜")

    with Session(engine) as session:
        cfg = llm_config or kv.resolve_llm_config(session)
        settings = read_config(session)
        ranking = latest_ranking(session)
        if ranking is None:
            ranking_id = ranking_title = ""
            items: List[Dict[str, Any]] = []
            used_by = None
        else:
            ranking_id, ranking_title = ranking.id, ranking.title or INPUT_SOURCE_NAME
            items = ranking_items(ranking)
            used = period_using(session, ranking.id)
            used_by = used.id if used is not None else None

    if not ranking_id:
        return _skip(engine, started_at=started_at, report_date=report_date, trigger=trigger, reason=SKIP_NO_TRENDING)
    # 这条日榜写过了:定时一律跳过;手动只在「写的就是今天这一期」时重写(改了旋钮想重出一版),
    # 否则同一条榜单会落成两期。
    if used_by is not None and (trigger != "manual" or used_by != article_id):
        return _skip(engine, started_at=started_at, report_date=report_date, trigger=trigger, reason=SKIP_NO_NEW_TRENDING)
    if not items:
        raise RuntimeError("日榜记录里没有可用的条目")
    if not cfg.configured:
        raise LLMNotConfigured("LLM 未配置（需在设置中填写 base_url / api_key / model）")

    set_progress("picking", f"从 {len(items)} 个仓库里挑 {settings['pick_count']} 个")
    picks = await pick_repositories(
        items, pick_count=settings["pick_count"], ai_only=settings["ai_only"],
        llm_config=cfg, triggered_by=triggered_by,
    )
    if not picks:
        if settings["ai_only"]:
            return _skip(engine, started_at=started_at, report_date=report_date, trigger=trigger, reason=SKIP_NO_AI_REPOS)
        raise LLMError("模型没有挑出任何仓库")

    set_progress("writing", "写入")
    generated_at = datetime.now().isoformat()
    content_obj = TrendingPicksContent(
        id=article_id,
        title=f"{FLOW_NAME} · {report_date}",
        source_url="",
        publish_date=report_date,
        content=render_markdown(picks, items, ranking_title=ranking_title),
        has_content=True,
        report_date=report_date,
        items=picks,
        picks_count=len(picks),
        repo_count=len(items),
        trending_article_id=ranking_id,
        llm_model=cfg.for_aux().model,
        generated_at=generated_at,
    )
    content_obj.source_id = SOURCE_ID
    with Session(engine) as session:
        persist_generated_article(session, content_obj)
        kv._stage_setting(session, KEY_LAST_RUN, json.dumps({
            "status": "success", "started_at": started_at, "ended_at": datetime.now().isoformat(),
            "report_date": report_date, "article_id": article_id, "error_message": None,
            "picks_count": len(picks), "repo_count": len(items), "trending_article_id": ranking_id,
        }, ensure_ascii=False))
        session.commit()

    logger.info("精读[%s]：生成完成，选 %d 个 / 榜单 %d 个", report_date, len(picks), len(items))
    set_progress("done", f"完成 · 选 {len(picks)} 个")
    return {
        "status": "success", "report_date": report_date, "article_id": article_id,
        "picks_count": len(picks), "repo_count": len(items), "trigger": trigger,
    }
