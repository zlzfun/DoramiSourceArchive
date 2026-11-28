"""生成流程的登记表与外壳(issue #72)。

**流程**:平台自己定时把站内已有的内容加工成一份新内容——公共日报、GitHub Trending 精读。
一个流程是一段登记在代码里的 Python,产出写进一个系统源(``system.toml`` 的 ``[[system]]``)。

这里只统一外面那一层:登记、开关、定时、手动触发、进度、上次结果、往期。**内部各写各的**
(``services.daily_brief`` / ``services.trending_picks``),不抽共用的管道——两份流程的取材、打分、选篇
正是它们不一样的地方。加一个流程 = 写一个模块 + 在 ``system.toml`` 加一条系统源 + 在 ``FLOWS`` 里登记,
与加一个抓取器是同一种纪律(代码即记录)。

外壳对流程的要求:

- 一组**有序的阶段**(``phases``,建议不超过 6 个)。它是内部过程的粗粒度投影,不是步骤图:日报的评分与点评
  是逐条并行、论文走单独的名额,都在阶段里面。以后有并行或分支的流程,往阶段上加字段,已有流程不用改;
- 运行时上报「当前阶段 + 阶段内的完成数 / 总数」(``progress``);
- 跑一次的结果是 成功 / 跳过 / 失败 之一(``last_run``);跳过不算失败。

公共日报是薄适配:配置键(``daily_brief_*``)、调度里的 job id(``daily_brief``)、``/api/daily-brief/*``
都原样保留(神灯流水线与 skill 模板在用)。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlmodel import Session, select

from models.db import ArticleRecord
from services import daily_brief, trending_picks

STATUS_SUCCESS = "success"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

RECENT_DAYS = 7
PERIOD_PAGE_SIZE_MAX = 50


@dataclass(frozen=True)
class Knob:
    """一个旋钮的声明;管理面据此画一行参数。``type`` 是 ``number`` 或 ``toggle``。"""

    field: str
    label: str
    type: str
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None

    def describe(self, value: Any) -> Dict[str, Any]:
        return {
            "field": self.field, "label": self.label, "type": self.type, "default": self.default,
            "min": self.min, "max": self.max, "step": self.step, "value": value,
        }


@dataclass(frozen=True)
class Flow:
    id: str                 # 也是调度里的 job id 与后台任务类型
    name: str
    source_id: str          # 产出写进哪个系统源
    phases: Tuple[str, ...]
    default_cron: str
    knobs: Tuple[Knob, ...]
    is_enabled: Callable[[Session], bool]
    cron: Callable[[Session], str]
    read_config: Callable[[Session], Dict[str, Any]]
    # (session, *, enabled, cron, config) → 校验并写入,只动给了的;不合法抛 ValueError
    update: Callable[..., None]
    input_summary: Callable[[Session], Dict[str, str]]          # 「取材」一格:{"label", "sub"}
    generate: Callable[..., Awaitable[Dict[str, Any]]]          # (storage=, trigger=, triggered_by=)
    progress: Callable[[], Dict[str, Any]]                      # 规整后的进度,见 _progress()
    last_run: Callable[[Session], Optional[Dict[str, Any]]]     # 规整后的上次结果,见 _last_run()
    period_summary: Callable[[Dict[str, Any]], Tuple[str, str]]  # 一期的扩展字段 → (一句概况, 往期行上的文字)
    # 「配置」里「取材」那一行:{"type": "source_scope", "field": …}(全部来源 / 自定名单)
    # 或 {"type": "source_link", "source_id": …, "text": …}(固定取自一个源,给个去源管理的入口)
    input_field: Dict[str, Any]
    # 旋钮之后的行(日报的增量游标:{"type": "cursor", "field": "cursor", …})
    extra_fields: Tuple[Dict[str, Any], ...] = ()
    scope_note: str = ""            # 「取材与去重口径」:收着的一段说明,没有就不出现
    delete_note: str = ""           # 删一期时确认框里多说的一句
    # 需要管理员留意的事(只在异常时有内容,正常是空的)
    notices: Callable[[Session], List[str]] = lambda session: []


def _progress(state: str, *, phase_index: Optional[int], message: str = "", done: int = 0, total: int = 0) -> Dict[str, Any]:
    """``state``:idle / running / done / skipped / error;``phase_index`` 只在 running 时有意义。"""
    return {"state": state, "phase_index": phase_index, "message": message, "done": int(done or 0), "total": int(total or 0)}


def _last_run(status: str, *, reason: str = "", ended_at: str = "", report_date: str = "") -> Dict[str, Any]:
    return {"status": status, "reason": reason or "", "ended_at": ended_at or "", "report_date": report_date or ""}


def _validate_cron(expr: str) -> str:
    cleaned = str(expr or "").strip()
    if len(cleaned.split()) != 5:
        raise ValueError("cron 表达式必须是 5 段，例如：30 8 * * *")
    return cleaned


# ── 公共日报:薄适配 ──────────────────────────────────────────────────────────

_BRIEF_PHASE_INDEX = {"collecting": 0, "scoring": 1, "selecting": 2, "editing": 3, "reducing": 4, "persisting": 4}
_BRIEF_CONFIG_KEYS = ("top_n", "min_score", "min_items", "paper_min_content_value", "source_ids", "cursor")


def _brief_read_config(session: Session) -> Dict[str, Any]:
    return {
        "top_n": daily_brief.daily_brief_top_n(session),
        "min_score": daily_brief.daily_brief_min_score(session),
        "min_items": daily_brief.daily_brief_min_items(session),
        "paper_min_content_value": daily_brief.daily_brief_paper_min_content_value(session),
        # None = 全部来源;非空名单 = 候选只取名单内的源
        "source_ids": daily_brief.read_source_scope(session),
        "cursor": daily_brief.read_cursor(session),
    }


def _number(value: Any, label: str, *, integer: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (integer and int(value) != value):
        raise ValueError(f"{label}必须是{'整数' if integer else '数字'}")
    return int(value) if integer else float(value)


def _brief_update(session: Session, *, enabled: Optional[bool] = None, cron: Optional[str] = None,
                  config: Optional[Dict[str, Any]] = None) -> None:
    config = config or {}
    if not isinstance(config, dict):
        raise ValueError("配置必须是对象")
    unknown = sorted(set(config) - set(_BRIEF_CONFIG_KEYS))
    if unknown:
        raise ValueError(f"未知的配置项:{unknown}")
    fields: Dict[str, Any] = {}
    if "top_n" in config:
        fields["top_n"] = _number(config["top_n"], "精选条数", integer=True)
    if "min_items" in config:
        fields["min_items"] = _number(config["min_items"], "正文保底条数", integer=True)
    if "min_score" in config:
        fields["min_score"] = _number(config["min_score"], "入选门槛", integer=False)
    if "paper_min_content_value" in config:
        fields["paper_min_content_value"] = _number(config["paper_min_content_value"], "论文门槛", integer=False)
    if "source_ids" in config:
        value = config["source_ids"]
        if value is not None and not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
            raise ValueError("来源名单必须是源 id 的数组")
        fields["source_ids"] = value or []      # None / [] = 回到全部来源
    if "cursor" in config:
        if not isinstance(config["cursor"], str):
            raise ValueError("增量游标必须是字符串")
        fields["cursor"] = config["cursor"]
    daily_brief.apply_config_update(session, enabled=enabled, cron=cron, **fields)


def _brief_input_summary(session: Session) -> Dict[str, str]:
    scope = daily_brief.read_source_scope(session)
    if scope is None:
        return {"label": "全部来源", "sub": ""}
    return {"label": "自定名单", "sub": f"{len(scope)} 个来源"}


def _brief_progress() -> Dict[str, Any]:
    raw = daily_brief.get_progress()
    phase = raw.get("phase")
    if phase in _BRIEF_PHASE_INDEX:
        return _progress("running", phase_index=_BRIEF_PHASE_INDEX[phase], message=raw.get("message") or "",
                         done=raw.get("done"), total=raw.get("total"))
    state = {"done": "done", "empty": "skipped", "error": "error"}.get(phase, "idle")
    return _progress(state, phase_index=None, message=raw.get("message") or "")


def _brief_last_run(session: Session) -> Optional[Dict[str, Any]]:
    raw = daily_brief.get_json_setting(session, daily_brief.KEY_LAST_RUN, None)
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    common = {"ended_at": raw.get("ended_at") or "", "report_date": raw.get("report_date") or ""}
    if status == "empty":
        return _last_run(STATUS_SKIPPED, reason="暂无新增内容", **common)
    if status == "failed":
        return _last_run(STATUS_FAILED, reason=str(raw.get("error_message") or ""), **common)
    return _last_run(STATUS_SUCCESS, **common)


def _brief_period_summary(extensions: Dict[str, Any]) -> Tuple[str, str]:
    count = extensions.get("articles_count")
    short = f"收录 {count} 条" if count is not None else ""
    recommendations = extensions.get("content_recommendations")
    recommended = f"推荐 {len(recommendations)} 条" if isinstance(recommendations, list) and recommendations else ""
    model = str(extensions.get("llm_model") or "")
    return short, " · ".join(part for part in (short, recommended, model) if part)


def _brief_notices(session: Session) -> List[str]:
    """评分来源的健康提示(v3.48 收口,原来在前端算):日报对入库分析是软依赖,补评常态化不报错,
    但意味着分析总闸没开或 worker 没追平(成本翻倍、读者面没有分数)。只在异常时有,正常是空的。"""
    raw = daily_brief.get_json_setting(session, daily_brief.KEY_LAST_RUN, None)
    if not isinstance(raw, dict) or raw.get("status") != "success":
        return []

    def count(key: str) -> int:
        try:
            return int(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    stored, inline, queued = count("scored_stored"), count("scored_inline"), count("scored_inline_pending")
    total = stored + inline
    if not total or inline / total < 0.5:
        return []
    if stored == 0:
        head = f"最近一次 {inline} 篇候选全部由日报就地补评（复用分析 0 篇）：入库分析总闸可能未开启，或 worker 尚未追平"
    else:
        head = f"最近一次补评占比 {int(inline / total * 100 + 0.5)}%（复用 {stored} / 补评 {inline}）"
    tail = f"；其中 {queued} 篇当时仍在分析队列，建议把 cron 排在 worker 追平之后" if queued > 0 else ""
    return [head + tail]


_BRIEF_SCOPE_NOTE = (
    "新闻候选取自上次成功生成之后新入库的文章；同日同事件合并，并对照近期日报查重。"
    "深度阅读与播客另从近 7 天已有评分的内容中推荐：深读至少 7.0 分、最多 3 篇；播客需全文终评至少 7.5 分、最多 2 集；"
    "各分区每源最多 1 条。推荐不自动生成导读音频。生成在后台执行，可离开本页。"
)


async def _brief_generate(*, storage, trigger: str, triggered_by: Optional[str]) -> Dict[str, Any]:
    """经外壳手动触发的日报:失败时把进度与上次结果都标成失败(定时那条路由 ``execute_daily_brief_job`` 自己记)。"""
    try:
        return await daily_brief.generate_daily_brief(storage=storage, trigger=trigger, triggered_by=triggered_by)
    except Exception as exc:  # noqa: BLE001 记下再抛
        message = str(exc) or type(exc).__name__
        daily_brief.set_progress("error", f"生成失败: {message}")
        try:
            with Session(storage.engine) as session:
                daily_brief.set_json_setting(session, daily_brief.KEY_LAST_RUN, {
                    "status": "failed", "ended_at": dt.datetime.now().isoformat(), "error_message": message,
                })
        except Exception:  # noqa: BLE001 记不下来不该盖住原来的错误
            pass
        raise


DAILY_BRIEF = Flow(
    id="daily_brief",
    name="哆啦美·AI资讯日报",
    source_id=daily_brief.DAILY_BRIEF_SOURCE_ID,
    phases=("收集", "评分", "精选", "点评", "成稿"),
    default_cron=daily_brief.DEFAULT_DAILY_BRIEF_CRON,
    knobs=(
        Knob("top_n", "每期条数", "number", daily_brief.DEFAULT_TOP_N, daily_brief.TOP_N_MIN, daily_brief.TOP_N_MAX, 1),
        Knob("min_score", "入选门槛", "number", daily_brief.DEFAULT_MIN_SCORE, 0, 10, 0.5),
        Knob("min_items", "正文保底条数", "number", daily_brief.DEFAULT_MIN_ITEMS, 0, daily_brief.TOP_N_MAX, 1),
        Knob("paper_min_content_value", "论文门槛", "number", daily_brief.DEFAULT_PAPER_MIN_CONTENT_VALUE, 0, 10, 0.5),
    ),
    is_enabled=daily_brief.daily_brief_enabled,
    cron=daily_brief.daily_brief_cron,
    read_config=_brief_read_config,
    update=_brief_update,
    input_summary=_brief_input_summary,
    generate=_brief_generate,
    progress=_brief_progress,
    last_run=_brief_last_run,
    period_summary=_brief_period_summary,
    input_field={"type": "source_scope", "field": "source_ids", "label": "取材"},
    extra_fields=({
        "type": "cursor", "field": "cursor", "label": "增量游标",
        "reset_confirm": "重置增量游标后,下次生成会从近期归档(最多 120 篇最新内容)重做,用于重做 / 补生成。确认重置?",
    },),
    scope_note=_BRIEF_SCOPE_NOTE,
    delete_note="若删除的是最新一期,增量游标会自动回退到生成它之前,便于重新生成。",
    notices=_brief_notices,
)


# ── GitHub Trending 精读 ──────────────────────────────────────────────────────


def _picks_update(session: Session, *, enabled: Optional[bool] = None, cron: Optional[str] = None,
                  config: Optional[Dict[str, Any]] = None) -> None:
    # 先全部校验,再写:不会写到一半
    cleaned_cron = _validate_cron(cron) if cron is not None else None
    cleaned_config = trending_picks.validate_config(config) if config is not None else {}
    if enabled is not None:
        daily_brief.set_setting(session, trending_picks.KEY_ENABLED, "true" if enabled else "false")
    if cleaned_cron is not None:
        daily_brief.set_setting(session, trending_picks.KEY_CRON, cleaned_cron)
    if cleaned_config:
        trending_picks.write_config(session, cleaned_config)


def _picks_input_summary(session: Session) -> Dict[str, str]:
    ranking = trending_picks.latest_ranking(session)
    stamp = (ranking.fetched_date or "") if ranking is not None else ""
    # 最新一条日榜的入库时刻(MM-DD HH:MM);一条都没有时留空
    sub = f"{stamp[5:10]} {stamp[11:16]}" if len(stamp) >= 16 else ""
    return {"label": trending_picks.INPUT_SOURCE_NAME, "sub": sub}


def _picks_progress() -> Dict[str, Any]:
    raw = trending_picks.get_progress()
    phase = raw.get("phase")
    if phase in trending_picks.PHASE_INDEX:
        return _progress("running", phase_index=trending_picks.PHASE_INDEX[phase], message=raw.get("message") or "",
                         done=raw.get("done"), total=raw.get("total"))
    state = phase if phase in ("done", "skipped", "error") else "idle"
    return _progress(state, phase_index=None, message=raw.get("message") or "")


def _picks_last_run(session: Session) -> Optional[Dict[str, Any]]:
    raw = trending_picks.last_run(session)
    if raw is None:
        return None
    common = {"ended_at": raw.get("ended_at") or "", "report_date": raw.get("report_date") or ""}
    status = raw.get("status")
    if status == "skipped":
        return _last_run(STATUS_SKIPPED, reason=str(raw.get("reason") or ""), **common)
    if status == "failed":
        return _last_run(STATUS_FAILED, reason=str(raw.get("error_message") or ""), **common)
    return _last_run(STATUS_SUCCESS, **common)


def _picks_period_summary(extensions: Dict[str, Any]) -> Tuple[str, str]:
    picks, repos = extensions.get("picks_count"), extensions.get("repo_count")
    if picks is None:
        return "", ""
    text = f"选 {picks} 个" + (f" · 榜单 {repos} 个" if repos is not None else "")
    return text, text


TRENDING_PICKS = Flow(
    id=trending_picks.FLOW_ID,
    name=trending_picks.FLOW_NAME,
    source_id=trending_picks.SOURCE_ID,
    phases=trending_picks.PHASES,
    default_cron=trending_picks.DEFAULT_CRON,
    knobs=(
        Knob("pick_count", "挑选个数", "number", trending_picks.PICK_COUNT_DEFAULT,
             trending_picks.PICK_COUNT_MIN, trending_picks.PICK_COUNT_MAX, 1),
        Knob("ai_only", "只看 AI 相关", "toggle", trending_picks.AI_ONLY_DEFAULT),
    ),
    is_enabled=trending_picks.is_enabled,
    cron=trending_picks.cron,
    read_config=trending_picks.read_config,
    update=_picks_update,
    input_summary=_picks_input_summary,
    generate=trending_picks.generate,
    progress=_picks_progress,
    last_run=_picks_last_run,
    period_summary=_picks_period_summary,
    input_field={
        "type": "source_link", "label": "取材",
        "source_id": trending_picks.INPUT_SOURCE_ID, "text": trending_picks.INPUT_SOURCE_NAME,
    },
)


# ── 登记表 ────────────────────────────────────────────────────────────────────

FLOWS: Tuple[Flow, ...] = (DAILY_BRIEF, TRENDING_PICKS)


def all_flows() -> Tuple[Flow, ...]:
    return FLOWS


def get_flow(flow_id: str) -> Optional[Flow]:
    return next((flow for flow in FLOWS if flow.id == flow_id), None)


# ── 外壳:一个流程现在的样子、它的往期 ─────────────────────────────────────────


def _extensions(record: ArticleRecord) -> Dict[str, Any]:
    try:
        value = json.loads(record.extensions_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _period(flow: Flow, record: ArticleRecord) -> Dict[str, Any]:
    short, row = flow.period_summary(_extensions(record))
    return {
        "article_id": record.id,
        "date": (record.publish_date or "")[:10],
        "generated_at": record.fetched_date or "",
        "summary": short,
        "meta": row,
    }


def _periods_query(flow: Flow):
    return select(ArticleRecord).where(ArticleRecord.source_id == flow.source_id)


def list_periods(session: Session, flow: Flow, *, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
    """往期(新的在前),一页一页取;不带正文——正文在展开某一期时另取。"""
    page = max(int(page or 1), 1)
    page_size = max(1, min(int(page_size or 10), PERIOD_PAGE_SIZE_MAX))
    total = session.exec(
        select(func.count()).select_from(ArticleRecord).where(ArticleRecord.source_id == flow.source_id)
    ).one()
    rows = session.exec(
        _periods_query(flow)
        .order_by(ArticleRecord.publish_date.desc(), ArticleRecord.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": int(total or 0), "page": page, "page_size": page_size,
            "items": [_period(flow, row) for row in rows]}


def _form(flow: Flow, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    def with_value(field: Dict[str, Any]) -> Dict[str, Any]:
        return {**field, "value": config.get(field["field"])} if field.get("field") else dict(field)

    return [
        with_value(flow.input_field),
        {"type": "cron", "label": "定时"},
        *(knob.describe(config.get(knob.field, knob.default)) for knob in flow.knobs),
        *(with_value(field) for field in flow.extra_fields),
    ]


def describe(session: Session, flow: Flow, *, next_run_at: Optional[str] = None,
             today: Optional[dt.date] = None) -> Dict[str, Any]:
    """泳道上一行要的全部:取材的一句话、阶段与进度、今天 / 最近一期、近 7 日、定时、旋钮与取值。"""
    today = today or dt.datetime.now().date()
    config = flow.read_config(session)
    last = flow.last_run(session)
    progress = flow.progress()

    start = (today - dt.timedelta(days=RECENT_DAYS - 1)).isoformat()
    dated = session.exec(
        select(func.substr(ArticleRecord.publish_date, 1, 10))
        .where(ArticleRecord.source_id == flow.source_id, func.substr(ArticleRecord.publish_date, 1, 10) >= start)
    ).all()
    produced = {value for value in dated if value}
    recent_days: List[Dict[str, Any]] = []
    for offset in range(RECENT_DAYS - 1, -1, -1):
        day = (today - dt.timedelta(days=offset)).isoformat()
        recent_days.append({"date": day, "produced": day in produced})

    listing = list_periods(session, flow, page=1, page_size=1)
    latest = listing["items"][0] if listing["items"] else None

    summary = dict(flow.input_summary(session))
    # 上一次是跳过的(没有可取的素材):「取材」一格把原因提出来
    summary["warn"] = last["reason"] if last and last["status"] == STATUS_SKIPPED else ""

    return {
        "id": flow.id,
        "name": flow.name,
        "source_id": flow.source_id,
        "phases": list(flow.phases),
        "input": summary,
        "enabled": flow.is_enabled(session),
        "cron": flow.cron(session),
        "default_cron": flow.default_cron,
        "next_run_at": next_run_at,
        "running": progress["state"] == "running",
        "progress": progress,
        "last_run": last,
        "latest": latest,
        "recent_days": recent_days,
        "total": listing["total"],
        "knobs": [knob.describe(config.get(knob.field, knob.default)) for knob in flow.knobs],
        "config": config,
        # 抽屉里「配置」框从上到下的各行:取材 → 定时 → 旋钮 → 其余
        "form": _form(flow, config),
        "scope_note": flow.scope_note,
        "delete_note": flow.delete_note,
        "notices": flow.notices(session),
    }
