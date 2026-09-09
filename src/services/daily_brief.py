"""每日 AI 资讯日报编排 (src/services/daily_brief.py)

流程(v3.48 统一新闻价值评分波):
     collect_candidates(游标/名单/裁剪)
     → load_stored_scores + score_candidates(复用文章级分析的新闻价值分;缺分的候选
       就地调用**同一个评分函数**补评——喂同一套规范标签闭集,结果只用于本次生成、
       不写回分析表;无正文候选按标题走同一把尺子)
     → 软阈值(v3.49.1,issue #33):score ≥ min_score 进正选池;正选池不足 min_items 时,
       从近线带 [min_score−1.0, min_score) 按有效分补足正文条目;近线带其余条目在正文
       未填满 top_n 时以标题+链接填进附录;低于近线带的直接 pass(随游标跳过);过线但
       无正文的进附录
     → dedup_clusters(机械预聚类 + 同日同事件 LLM 聚类) → select_top(按分数+官方加成+多样性择优)
     → editorial_polish(只为入选条目逐篇写中文标题/要点/点评,分析摘要与评分理由作已知事实喂入)
     → cross_day_dedup(跨天查重,一次轻量 LLM) → render_brief_markdown(确定性渲染)
     → 写库(幂等 update)。

日报对文章级分析是**软依赖**:分析 worker 跑完了就直接复用(省钱),没跑完或总闸关着
就自己按同一把尺子补评——评分部分具备或完全不具备都能 work,两个子系统互不知晓。
日报自己不再打分(历史 MAP 逐篇打分随本波退役),全站只有一把新闻价值尺子:
llm/article_analysis_prompt.py。方案:docs/unified-news-scoring-plan.md。

v3.34 起 reduce 不再是整篇 LLM 长输出:markdown 由代码从结构化条目排版,
LLM 在汇编段只做「对照近几天日报条目标题判断 drop/接前报」的小 JSON 决策——
截断/漏条/复制篡改类静默劣化就此根除(2026-08 空正文事故的形态性风险消除)。

v3.35 权威机械层(生产实录:近 10 期日报头部名次官方源仅 1/30,官方并不迟到、
是排不上去):BriefCandidate 带 source_role(source_naming 后端角色镜像),
同事件代表权官方在分差门限内优先、select 排序官方 +0.5 有界加成、跨天查重
官方 drop 机械降级为 followup——三处全是确定性代码,不靠 LLM 自觉。
同波修同日重跑(合并而非覆盖,见 load_existing_brief_state/merge_same_day)、
跨天剔条回补(top_n+buffer 预选后裁回)、瞬时失败串行重试、候选两段式轻列取数。

三层去重：
  ① 确定性水位线游标 daily_brief_cursor（fetched_date），写库成功后才推进；
  ② dedup_clusters 同日同事件聚类合并；
  ③ cross_day_dedup 对照近期日报条目跨天去重（纯重复剔除/后续进展标注增量）。

运行记录走 AppSettingRecord（KV），不新建 ORM 表。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy import or_
from sqlmodel import Session, select

import config
from llm.client import (
    ChatMessage, LLMError, LLMNotConfigured, UsageMeta,
    chat_completion, client_session, parse_json_object,
)
from llm import prompts
from llm.article_analysis_prompt import ARTICLE_ANALYSIS_SCORING_VERSION

# 日报各阶段的 LLM 用量归属：手动触发归到触发它的 admin，定时调度无登录上下文则归 "system"。
USAGE_SYSTEM = "system"


def _usage_meta(purpose: str, username: Optional[str]) -> UsageMeta:
    return UsageMeta(purpose=purpose, username=(username or USAGE_SYSTEM))
from models.analysis_contracts import TaxonomyTagDTO
from models.content import DailyBriefContent
from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    SourceConfigRecord,
)
from services import credentials
from services.article_analysis import (
    AnalysisInput, analyze_article_with_llm, load_relevant_active_tags, validate_analysis_payload,
)
from services.source_naming import friendly_source_name, source_role

logger = logging.getLogger("dorami.daily_brief")


# ==========================================
# 生成进度（内存，仅供前端轮询；单进程有效，不持久化）
# ==========================================

_PROGRESS: Dict[str, Any] = {"phase": "idle", "message": "", "done": 0, "total": 0, "updated_at": 0.0}


def set_progress(phase: str, message: str = "", *, done: int = 0, total: int = 0) -> None:
    """更新当前生成阶段。phase ∈ idle/collecting/scoring/selecting/editing/reducing/persisting/done/empty/error。"""
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
# 入选门槛(v3.48):新闻价值分低于它的候选直接 pass——不进正选也不进附录,随游标跳过。
# 语义是「pass 掉边角料」的下限,入选仍靠排序与配额:淡日子日报自然变短而不是被灌水。
KEY_MIN_SCORE = "daily_brief_min_score"
DEFAULT_MIN_SCORE = 6.0
# 正文保底条数(v3.49.1,issue #33 F2/F3/F4):分数吸附在 .5 刻度且 5.5/6.5 双峰卡在 6.0 两侧,
# 同篇复评一成会跨线翻转,清淡日过线只剩 3 条——硬阈值在这种分布上不可靠。过线不足 min_items
# 时从近线带补足,近线带宽度固定 APPENDIX_BAND;0 = 不保底。
KEY_MIN_ITEMS = "daily_brief_min_items"
DEFAULT_MIN_ITEMS = 8
APPENDIX_BAND = 1.0
# 软阈值保底时陪跑进同事件聚类的近线稿下限(条):近线稿之间互为重复会坍缩,名额要留足
NEAR_BAND_PROBE_MIN = 12
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
    # 信息角色(source_naming.source_role):官方/媒体/个人/榜单,权威机械层的判定输入
    source_role: str = "media"


# ── 权威机械层(v3.35)──
# 「官方内容靠前」靠三处确定性代码,不靠 LLM 自觉(v3.33 空正文事故后的既定纪律):
# ① 同事件代表权:簇内官方在分差 REP_AUTHORITY_SCORE_GAP 内优先当代表——分差门限
#    防「官方一行推文」压过媒体的深度整理(代表决定条目正文的丰度);
# ② 同分排序:select_top 用 score + OFFICIAL_SCORE_BONUS 的**有界加成**排序——
#    整数分布下等价于「同分段官方置顶但绝不跨分数段」,重要性仍由 score 主导,
#    官方外围产品照沉(生产实测 76% 条目挤在 7/8 两档,平分段顺序此前由抓取顺序随机决定);
# ③ 跨天查重官方例外:见 cross_day_dedup。
AUTHORITY_RANK = {"official": 0, "leaderboard": 1, "media": 2, "personal": 3}
OFFICIAL_SCORE_BONUS = 0.5
REP_AUTHORITY_SCORE_GAP = 1.0


def _authority_rank(item: "ScoredItem") -> int:
    return AUTHORITY_RANK.get(item.candidate.source_role, AUTHORITY_RANK["media"])


def _effective_score(item: "ScoredItem") -> float:
    """排序用有效分:官方源加有界 bonus;item.score 本身(入库/导出值)不改。"""
    bonus = OFFICIAL_SCORE_BONUS if item.candidate.source_role == "official" else 0.0
    return item.score + bonus


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
    # 评分是否到手(分析表复用或就地补评成功);False 的条目只有标题与链接,降入附录
    score_ok: bool = True
    # 同事件去重合并后，被并入本条的其它来源链接（供 reduce 渲染多来源）
    extra_sources: List[str] = field(default_factory=list)
    # 跨天查重判定为「同一事件的后续进展」时的一句增量说明（渲染成「（接前报）」行）
    followup_note: str = ""
    # 分析给出的评分理由:编辑阶段的已知事实,不进 to_reduce_dict(不能被误当公共点评)
    score_reason: str = ""
    # 同事件归并时并入本条(代表)的其它条目文章 id:软阈值保底据此判断「簇里有没有过线稿」——
    # 近线官方稿当了过线媒体稿的代表时,簇仍算合格稿,不能被当成补足稿裁掉(codex 检视 P2)
    merged_ids: List[str] = field(default_factory=list)

    def to_reduce_dict(self) -> Dict[str, Any]:
        return {
            # id/source_id/source_role(v3.35 增量键):同日重跑合并时从 extensions.items
            # 重建条目所需;历史日报无这三键,合并侧按位对齐 included_article_ids 兜底。
            "id": self.candidate.id,
            "source_id": self.candidate.source_id,
            "source_role": self.candidate.source_role,
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
            "followup_note": self.followup_note,
        }


# 文章级分析的 content_genre → 日报 classification(分节用)。日报自己不再产分类,
# 体裁与分数一样来自分析结果,映射是确定性代码。
CLASSIFICATION_FROM_GENRE: Dict[str, str] = {
    "model_release": "模型发布",
    "product_update": "行业资讯",
    "open_source_update": "开源动态",
    "research_paper": "学术论文",
    "tutorial": "行业资讯",
    "opinion": "行业资讯",
    "industry_news": "行业资讯",
    "conference": "技术大会",
    "social_discussion": "社交动态",
    "aggregation": "资讯聚合",
    "security_incident": "行业资讯",
    "regulation": "行业资讯",
    "other": "资讯聚合",
}


def classification_from_genre(content_genre: str) -> str:
    """确定性映射 genre;未知/空值返回空串,渲染层回落 content_type 映射。"""

    return CLASSIFICATION_FROM_GENRE.get((content_genre or "").strip(), "")


def _analysis_summary_lines(summary: str) -> List[str]:
    """把文章级纯文本摘要收敛为 ``summary: list[str]`` 形状(editorial 失败时的要点兜底)。"""

    raw = (summary or "").strip()
    if not raw:
        return []
    lines = []
    for line in raw.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*+]|\d+[.)]|[•·])\s*", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return lines or [raw]


@dataclass(frozen=True)
class ArticleScore:
    """一篇文章可供日报消费的评分投影——来自分析表或就地补评,形状相同。

    topic_tags = topic/industry 规范标签显示名(首个作 realm 配额键);
    entity_tags = entity 标签名,其后补 entities 字段里的实体名(首个作 company)。
    """

    score: float
    summary: str
    genre: str
    topic_tags: Tuple[str, ...] = ()
    entity_tags: Tuple[str, ...] = ()
    # 一句「为什么重要/不重要」:只喂给编辑阶段作已知事实,绝不进 extensions.items
    score_reason: str = ""


def load_stored_scores(session: Session, article_ids: List[str]) -> Dict[str, ArticleScore]:
    """批量读取分析 worker 已产出的**当前尺子**评分(succeeded 且 scoring_version 为现行版本)。

    旧版本尺子的结果视同没有——版本键就是尺子的名字,混用两把尺子排序没有意义;
    这批候选会走就地补评。一次 IN 查询 + 一次标签 join,避免 N+1。
    """

    ids = list(dict.fromkeys(str(i) for i in article_ids if i))
    if not ids:
        return {}
    analyses = session.exec(
        select(ArticleAnalysisRecord)
        .where(ArticleAnalysisRecord.article_id.in_(ids))
        .where(ArticleAnalysisRecord.status == "succeeded")
        .where(ArticleAnalysisRecord.quality_score.is_not(None))
        .where(ArticleAnalysisRecord.scoring_version == ARTICLE_ANALYSIS_SCORING_VERSION)
    ).all()
    if not analyses:
        return {}

    analysis_ids = [row.article_id for row in analyses]
    tag_rows = session.exec(
        select(ArticleTagAssignmentRecord, CmsTagRecord)
        .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
        .where(ArticleTagAssignmentRecord.article_id.in_(analysis_ids))
        .where(CmsTagRecord.status == "active")
        .order_by(
            ArticleTagAssignmentRecord.article_id,
            ArticleTagAssignmentRecord.is_primary.desc(),
            ArticleTagAssignmentRecord.relevance.desc(),
            CmsTagRecord.id,
        )
    ).all()
    topics: Dict[str, List[str]] = {}
    entities: Dict[str, List[str]] = {}
    for assignment, tag in tag_rows:
        display = (tag.name_zh or tag.name_en or tag.code or "").strip()
        bucket = entities if tag.kind == "entity" else topics
        names = bucket.setdefault(assignment.article_id, [])
        if display and display not in names:
            names.append(display)

    out: Dict[str, ArticleScore] = {}
    for row in analyses:
        entity_names = list(entities.get(row.article_id, []))
        for name in _entity_names(row.entities_json):
            if name not in entity_names:
                entity_names.append(name)
        out[row.article_id] = ArticleScore(
            score=float(row.quality_score),
            summary=row.summary or "",
            genre=row.content_genre or "",
            topic_tags=tuple(topics.get(row.article_id, [])),
            entity_tags=tuple(entity_names),
            score_reason=row.score_reason or "",
        )
    return out


def _entity_names(entities_json: Optional[str]) -> List[str]:
    try:
        entries = json.loads(entities_json or "[]")
    except (TypeError, ValueError):
        return []
    names: List[str] = []
    for entry in entries if isinstance(entries, list) else []:
        name = str(entry.get("name") or "").strip() if isinstance(entry, dict) else ""
        if name and name not in names:
            names.append(name)
    return names


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


def daily_brief_min_items(session: Session) -> int:
    """正文保底条数(0～TOP_N_MAX);坏值回落默认;0 = 不保底。"""
    raw = get_setting(session, KEY_MIN_ITEMS, "")
    try:
        value = int(raw) if raw else DEFAULT_MIN_ITEMS
    except (TypeError, ValueError):
        value = DEFAULT_MIN_ITEMS
    return max(0, min(TOP_N_MAX, value))


def daily_brief_min_score(session: Session) -> float:
    """读取入选门槛(新闻价值分下限),非法/越界回落到默认并夹到 [0, 10]。"""
    raw = get_setting(session, KEY_MIN_SCORE, "")
    try:
        value = float(raw) if raw else DEFAULT_MIN_SCORE
    except ValueError:
        value = DEFAULT_MIN_SCORE
    return max(0.0, min(10.0, value))


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

    # 两段式取数(v3.35):先只取轻列做扫描/裁剪(游标重置或长停摆恢复时,旧实现会把
    # 游标后**全部行连正文**载入内存只为数 scanned_total),再按入选名单载全文。
    from services.user_sources import USER_SOURCE_PREFIX

    light_statement = (
        select(ArticleRecord.id, ArticleRecord.source_id, ArticleRecord.fetched_date)
        .where(ArticleRecord.fetched_date > effective_cursor)
        .where(ArticleRecord.source_id != DAILY_BRIEF_SOURCE_ID)  # 防自我递归
        # 用户自定源机械排除(v3.40):日报名单是手工 allowlist 本就不会勾用户源,
        # 此处是「全部来源」档(名单未设)下的双保险——私有源绝不进公共日报。
        .where(~ArticleRecord.source_id.startswith(USER_SOURCE_PREFIX, autoescape=True))
        .order_by(ArticleRecord.fetched_date.desc())
    )
    if source_ids:
        light_statement = light_statement.where(ArticleRecord.source_id.in_(list(source_ids)))
    light_rows = session.exec(light_statement).all()

    max_fetched_seen = cursor
    for _rid, _rsrc, fetched in light_rows:
        if fetched and fetched > max_fetched_seen:
            max_fetched_seen = fetched

    # per-source 裁剪 + 总量裁剪（light_rows 已按 fetched_date 倒序，保留较新）
    per_source_count: Dict[str, int] = {}
    chosen_ids: List[str] = []
    for rid, rsrc, _fetched in light_rows:
        count = per_source_count.get(rsrc, 0)
        if count >= per_source_cap:
            continue
        per_source_count[rsrc] = count + 1
        chosen_ids.append(rid)
        if len(chosen_ids) >= max_total:
            break

    candidates: List[BriefCandidate] = []
    if chosen_ids:
        full_rows = session.exec(
            select(ArticleRecord).where(ArticleRecord.id.in_(chosen_ids))
        ).all()
        by_id = {row.id: row for row in full_rows}
        chosen_source_ids = {row.source_id for row in full_rows if row.source_id}
        source_metadata = {
            source_id: (source_scope, provenance_tier)
            for source_id, source_scope, provenance_tier in session.exec(
                select(
                    SourceConfigRecord.source_id,
                    SourceConfigRecord.source_scope,
                    SourceConfigRecord.provenance_tier,
                ).where(SourceConfigRecord.source_id.in_(chosen_source_ids))
            ).all()
        }
        for rid in chosen_ids:
            row = by_id.get(rid)
            if row is None:
                continue
            role_metadata = source_metadata.get(row.source_id)
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
                    source_role=(
                        source_role(
                            row.source_id or "",
                            source_scope=role_metadata[0],
                            provenance_tier=role_metadata[1],
                        )
                        if role_metadata is not None
                        else source_role(row.source_id or "")
                    ),
                )
            )

    return candidates, (max_fetched_seen or effective_cursor), len(light_rows)


# ==========================================
# 阶段 2：Score（复用文章级分析;缺分就地补评——同一函数、同一把尺子）
# ==========================================

def _scored_item(candidate: BriefCandidate, score: ArticleScore) -> ScoredItem:
    """评分投影 → 日报条目。编辑字段(title_cn/要点/点评)留给 editorial_polish;
    这里先用分析产出机械填好选篇阶段需要的 classification/company/realm/hint。"""
    return ScoredItem(
        candidate=candidate,
        classification=classification_from_genre(score.genre),
        source=friendly_source_name(candidate.source_id),
        company=(score.entity_tags[0] if score.entity_tags else ""),
        realm=(score.topic_tags[0] if score.topic_tags else ""),
        summary=_analysis_summary_lines(score.summary),
        tags=list(dict.fromkeys(score.topic_tags + score.entity_tags)),
        score=score.score,
        score_ok=True,
        score_reason=score.score_reason,
    )


def _coerce_score(raw: Any) -> float:
    """存量 items 的 score → float(同日合并重建用),非法值取 0。"""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(10.0, value))


def _unscored_item(candidate: BriefCandidate) -> ScoredItem:
    return ScoredItem(
        candidate=candidate,
        title_cn=candidate.title,
        source=friendly_source_name(candidate.source_id),
        score=0.0,
        score_ok=False,
    )


async def _score_one(
    candidate: BriefCandidate, llm_config: config.LLMConfig,
    usage_meta: Optional[UsageMeta] = None, http_client=None,
    active_tags: Tuple[TaxonomyTagDTO, ...] = (),
) -> ScoredItem:
    """就地补评:直接调文章级分析的评分函数,结果只用于本次生成、不写回分析表(与 worker 松耦合)。

    v3.48 收口:补评喂 worker 同一套 `load_relevant_active_tags` 召回的规范标签闭集,
    topic/entity 与存储路径一样只取**规范标签名 + entities 名**,不再混入自由标签——
    否则 realm 配额键两条来路词汇不同(「大模型」vs「LLM」),`per_realm_cap` 按字符串计数失真。
    无正文候选同样走这里(body 为空、按标题评),过线者进附录、低于门槛 pass。
    """
    try:
        article_input = AnalysisInput(
            article_id=candidate.id,
            title=candidate.title,
            body=candidate.body,
            content_type=candidate.content_type,
            source_id=candidate.source_id,
            publish_date=candidate.publish_date,
            fetched_date=candidate.fetched_date,
            credentialed_source=False,  # 公共日报候选机械排除了用户私有源
            source_owner_or_domain=candidate.source_id,
            source_name=friendly_source_name(candidate.source_id),
            source_role=candidate.source_role,
        )
        raw = await analyze_article_with_llm(
            article_input, active_tags, llm_config, usage_meta=usage_meta, http_client=http_client,
        )
        result = validate_analysis_payload(raw, active_tags=active_tags).result
        name_by_code = {
            tag.code: (tag.name_zh or tag.name_en or tag.code).strip() for tag in active_tags
        }
        topics: List[str] = []
        entities: List[str] = []
        for assignment in result.tag_assignments:  # 已按 relevance 降序、首项为 primary
            name = name_by_code.get(assignment.code, "")
            bucket = entities if str(assignment.kind) == "entity" else topics
            if name and name not in bucket:
                bucket.append(name)
        for entity in result.entities:
            name = str(entity.get("name") or "").strip()
            if name and name not in entities:
                entities.append(name)
        return _scored_item(candidate, ArticleScore(
            score=float(result.quality_score),
            summary=result.summary,
            genre=str(result.content_genre),
            topic_tags=tuple(topics),
            entity_tags=tuple(entities),
            score_reason=result.score_reason or "",
        ))
    except (LLMError, Exception) as exc:  # noqa: BLE001 单篇失败降级，不中断整体
        logger.warning("日报补评单篇失败 (id=%s): %s", candidate.id, exc)
        return _unscored_item(candidate)


async def score_candidates(
    candidates: List[BriefCandidate],
    llm_config: config.LLMConfig,
    *,
    stored: Dict[str, ArticleScore],
    on_item_done=None,
    usage_username: Optional[str] = None,
    taxonomy_by_id: Optional[Dict[str, Tuple[TaxonomyTagDTO, ...]]] = None,
) -> List[ScoredItem]:
    """候选 → 带分条目。stored 里有的直接投影(零调用),没有的并发就地补评,
    整轮后失败者串行重试一次。无正文候选也进评分(按标题、同一把尺子),由调用方决定
    过线者进附录;taxonomy_by_id 是每篇补评要喂的规范标签闭集(缺省为空闭集)。
    on_item_done(done, total) 只统计补评篇数。"""
    if not candidates:
        return []
    tags_of = taxonomy_by_id or {}
    items: List[Optional[ScoredItem]] = [None] * len(candidates)
    pending: List[int] = []
    for i, candidate in enumerate(candidates):
        score = stored.get(candidate.id)
        if score is not None:
            items[i] = _scored_item(candidate, score)
        else:
            pending.append(i)
    if pending:
        total = len(pending)
        done = 0
        usage_meta = _usage_meta("article_analysis", usage_username)
        semaphore = asyncio.Semaphore(max(1, llm_config.map_concurrency))
        async with client_session(llm_config.for_aux()) as http_client:
            async def _guarded(i: int) -> ScoredItem:
                nonlocal done
                async with semaphore:
                    result = await _score_one(
                        candidates[i], llm_config, usage_meta, http_client,
                        active_tags=tags_of.get(candidates[i].id, ()),
                    )
                done += 1
                if on_item_done is not None:
                    on_item_done(done, total)
                return result

            results = await asyncio.gather(*[_guarded(i) for i in pending])
            for i, result in zip(pending, results):
                items[i] = result
            # 瞬时故障补救(v3.35 沿用):失败会降入附录且游标照推,整轮后串行重试一次
            failed = [i for i in pending if not items[i].score_ok]
            if failed:
                logger.info("日报补评:%d 条失败,串行重试一轮", len(failed))
                for i in failed:
                    retried = await _score_one(
                        candidates[i], llm_config, usage_meta, http_client,
                        active_tags=tags_of.get(candidates[i].id, ()),
                    )
                    if retried.score_ok:
                        items[i] = retried
    return [it for it in items if it is not None]


def score_histogram(items: List[ScoredItem]) -> Dict[str, int]:
    """1～10 整数档条数(评分到手的条目;10 分并入「10」档)——写进 last_run 供阈值校准。"""
    buckets = {str(b): 0 for b in range(1, 11)}
    for it in items:
        if not it.score_ok:
            continue
        buckets[str(min(10, max(1, int(it.score))))] += 1
    return buckets


# ==========================================
# 阶段 2.5：Dedup（同事件去重聚类，一次性 LLM 调用）
# ==========================================

def _pick_cluster_representative(items: List[ScoredItem], idxs: List[int]) -> int:
    """同事件簇的代表选择:官方在分差门限内优先,否则回归最高分。

    「官方内容靠前」的本义在同一事件内是**归属权**——读者应看到官方标题/链接/口径,
    媒体退来源行。但代表同时决定条目正文丰度:官方若只是一行推文而媒体有深度整理
    (分差 > REP_AUTHORITY_SCORE_GAP),仍由媒体当代表、官方链接进 extra_sources。
    多个官方并列时取分高者(官博自然赢过官推)。
    """
    best_score = max(items[i].score for i in idxs)
    officials = [i for i in idxs if items[i].candidate.source_role == "official"]
    if officials:
        rep_official = max(officials, key=lambda i: items[i].score)
        if items[rep_official].score >= best_score - REP_AUTHORITY_SCORE_GAP:
            return rep_official
    return max(idxs, key=lambda i: items[i].score)


_TITLE_NOISE_RE = re.compile(r"[\s\-—–_:：|,，.。!！?？'\"“”‘’()（）\[\]【】《》#]+")
_DIGIT_RUN_RE = re.compile(r"\d+")
PRECLUSTER_TITLE_RATIO = 0.75
# 归一化后短于此的标题不参与机械预聚类(「Qwen 更新」类短题共享前缀即高比率,太易误并)
PRECLUSTER_MIN_TITLE_CHARS = 8


def _normalized_title(title: str) -> str:
    return _TITLE_NOISE_RE.sub("", (title or "").strip().lower())


def _titles_look_same(a: str, b: str) -> bool:
    """归一化标题是否「明显同一事件」:一方包含另一方或 difflib 比率过线,且**数字串一致**
    (「GPT-5.5」vs「GPT-5.6」、「第 1 期」vs「第 2 期」只差数字却是不同事件,机械层绝不并)。"""
    if _DIGIT_RUN_RE.findall(a) != _DIGIT_RUN_RE.findall(b):
        return False
    return (a in b or b in a) or difflib.SequenceMatcher(None, a, b).ratio() >= PRECLUSTER_TITLE_RATIO


def _merge_into_representative(items: List[ScoredItem], idxs: List[int], dropped: set) -> None:
    """簇内非代表条目的链接并入代表的 extra_sources 并标记丢弃(LLM 簇与机械簇共用)。"""
    rep = _pick_cluster_representative(items, idxs)
    for i in idxs:
        if i == rep:
            continue
        for url in [items[i].candidate.source_url, *items[i].extra_sources]:
            if url and url not in items[rep].extra_sources and url != items[rep].candidate.source_url:
                items[rep].extra_sources.append(url)
        for merged_id in [items[i].candidate.id, *items[i].merged_ids]:
            if merged_id and merged_id not in items[rep].merged_ids:
                items[rep].merged_ids.append(merged_id)
        dropped.add(i)


def precluster_same_event(items: List[ScoredItem]) -> List[ScoredItem]:
    """同事件机械预聚类(v3.48 收口):分析结果带规范实体后,「同 company + 标题高相似」
    的明显重复不必再问 LLM——先机械并簇(代表选择与 LLM 簇同一规则),LLM 只处理剩余;
    LLM 失败时这一层就是兜底。判据刻意保守:company 非空且相同、归一化标题 difflib
    比率 ≥ PRECLUSTER_TITLE_RATIO(或一方完全包含另一方)且数字串一致、短标题不参与;
    跨语言(官方英文 vs 媒体中文)与措辞迥异的同事件仍交 LLM。"""
    if len(items) < 2:
        return items
    dropped: set = set()
    by_company: Dict[str, List[int]] = {}
    for i, it in enumerate(items):
        key = (it.company or "").strip().lower()
        if key:
            by_company.setdefault(key, []).append(i)
    for idxs in by_company.values():
        if len(idxs) < 2:
            continue
        titles = {i: _normalized_title(items[i].title_cn or items[i].candidate.title) for i in idxs}
        remaining = [i for i in idxs if len(titles[i]) >= PRECLUSTER_MIN_TITLE_CHARS]
        while remaining:
            seed = remaining.pop(0)
            group = [seed]
            rest: List[int] = []
            for j in remaining:
                (group if _titles_look_same(titles[seed], titles[j]) else rest).append(j)
            remaining = rest
            if len(group) >= 2:
                _merge_into_representative(items, sorted(group), dropped)
    if dropped:
        logger.info("日报预聚类：%d 条同厂商高相似标题机械合并", len(dropped))
    return [it for i, it in enumerate(items) if i not in dropped]


async def dedup_clusters(
    items: List[ScoredItem],
    llm_config: config.LLMConfig,
    usage_username: Optional[str] = None,
) -> List[ScoredItem]:
    """识别同一天里报道同一事件的重复条目，每组只保留 score 最高的代表，
    其余条目的 source_url 并入代表的 extra_sources。先跑机械预聚类(同厂商高相似标题),
    LLM 失败时降级为只保留机械层结果，不阻断主流程。"""
    items = precluster_same_event(items)
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
        _merge_into_representative(items, idxs, dropped)

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
    # 有效分 = score + 官方有界加成(v3.35):整数分高度压缩(生产实测 76% 挤在 7/8),
    # 平分段顺序此前由抓取顺序随机决定;+0.5 让同分段官方置顶、且绝不跨分数段。
    ranked = sorted(items, key=_effective_score, reverse=True)
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
    # 多样性配额只决定"哪些条目入选"；最终顺序统一按有效分（score+官方加成）降序，
    # 使日报 markdown 与导出 items（shendeng sort）都呈重要性排序、同分官方在前。
    selected.sort(key=_effective_score, reverse=True)
    return selected


# ==========================================
# 阶段 3.5：Editorial（只为入选条目逐篇写中文标题 / 要点 / 点评）
# ==========================================

async def _polish_one(
    item: ScoredItem, llm_config: config.LLMConfig,
    usage_meta: Optional[UsageMeta] = None, http_client=None,
) -> Tuple[ScoredItem, bool]:
    try:
        raw = await chat_completion(
            messages=[
                ChatMessage(role="system", content=prompts.EDITORIAL_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompts.build_editorial_user_prompt(
                    title=item.candidate.title, source_name=item.source, body=item.candidate.body,
                    analysis_summary="\n".join(item.summary), score_reason=item.score_reason,
                )),
            ],
            config=llm_config,
            response_json=True,
            usage_meta=usage_meta,
            http_client=http_client,
        )
        data = parse_json_object(raw)
        summary = [str(x) for x in (data.get("summary") or []) if x]
        tags = [str(t) for t in (data.get("tags") or []) if t]
        return replace(
            item,
            title_cn=str(data.get("title_cn") or item.candidate.title),
            source=str(data.get("source") or item.source),
            company=str(data.get("company") or item.company),
            realm=str(data.get("realm") or item.realm),
            summary=summary or item.summary,
            comment=str(data.get("comment") or ""),
            tags=tags or item.tags,
        ), True
    except (LLMError, Exception) as exc:  # noqa: BLE001 单篇失败保留分析摘要,不降附录
        logger.warning("日报编辑单篇失败 (id=%s): %s", item.candidate.id, exc)
        return item, False


async def editorial_polish(
    items: List[ScoredItem],
    llm_config: config.LLMConfig,
    *,
    on_item_done=None,
    usage_username: Optional[str] = None,
) -> List[ScoredItem]:
    """入选条目逐篇一次 LLM 调用写 title_cn/来源名/company/realm/要点/点评/常规标签。
    只跑十几篇(预选池),不再为上百篇候选付费。失败串行重试一次,仍失败者保留
    分析摘要作要点、点评留空——分数已到手,不因编辑失败降附录。"""
    if not items:
        return []
    total = len(items)
    done = 0
    usage_meta = _usage_meta("daily_brief_editorial", usage_username)
    edit_config = llm_config.for_aux()
    semaphore = asyncio.Semaphore(max(1, llm_config.map_concurrency))
    async with client_session(edit_config) as http_client:
        async def _guarded(it: ScoredItem) -> Tuple[ScoredItem, bool]:
            nonlocal done
            async with semaphore:
                result = await _polish_one(it, edit_config, usage_meta, http_client)
            done += 1
            if on_item_done is not None:
                on_item_done(done, total)
            return result

        results = await asyncio.gather(*[_guarded(it) for it in items])
        polished = [it for it, _ok in results]
        failed_idx = [i for i, (_it, ok) in enumerate(results) if not ok]
        if failed_idx:
            logger.info("日报编辑:%d 条失败,串行重试一轮", len(failed_idx))
            for i in failed_idx:
                retried, ok = await _polish_one(items[i], edit_config, usage_meta, http_client)
                if ok:
                    polished[i] = retried
    return polished


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
# 跨天查重对照物每条附带的要点长度上限
_RECENT_HINT_CHARS = 60


def fetch_recent_brief_items(
    session: Session, *, days: int = 3, exclude_date: str = ""
) -> List[Dict[str, Any]]:
    """近几天日报的条目标题清单（跨天查重的对照物）。

    优先读 extensions.items（结构化 title_cn），缺失时回退从正文提取「###」
    标题行；返回形如 [{"date": "YYYY-MM-DD", "titles": [...], "entries": [{"title","hint"}]}, ...]。
    entries(v3.48 收口)每条附要点首句(截 _RECENT_HINT_CHARS 字)作查重对照物,
    titles 保留供旧消费方。
    exclude_date(v3.35)：排除指定日期（传 report_date）——同日重跑时今天自己的
    日报曾混进对照物，增量条目先被「查重」剔光、再整篇覆盖，产出残报。
    """
    statement = (
        select(ArticleRecord)
        .where(ArticleRecord.source_id == DAILY_BRIEF_SOURCE_ID)
        .order_by(ArticleRecord.publish_date.desc())
        .limit(days)
    )
    if exclude_date:
        statement = statement.where(ArticleRecord.publish_date != exclude_date)
    out: List[Dict[str, Any]] = []
    for row in session.exec(statement).all():
        titles: List[str] = []
        hints: List[str] = []
        try:
            ext = json.loads(row.extensions_json or "{}")
            items = ext.get("items") if isinstance(ext, dict) else None
            for it in items or []:
                if isinstance(it, dict):
                    title = str(it.get("title_cn") or "").strip()
                    if title:
                        titles.append(title)
                        summary = it.get("summary")
                        first = ""
                        if isinstance(summary, list) and summary:
                            first = str(summary[0] or "")
                        elif isinstance(summary, str):
                            first = summary
                        hints.append(first.replace("**", "").strip()[:_RECENT_HINT_CHARS])
        except (ValueError, TypeError):
            pass
        if not titles and row.content:
            titles = [m.strip() for m in _BRIEF_HEADING_RE.findall(row.content) if m.strip()]
            hints = [""] * len(titles)
        if titles:
            titles = titles[:_RECENT_TITLES_PER_DAY]
            hints = (hints + [""] * len(titles))[:len(titles)]
            out.append({
                "date": (row.publish_date or "")[:10],
                "titles": titles,
                "entries": [{"title": t, "hint": h} for t, h in zip(titles, hints)],
            })
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

    官方例外(v3.35)：官方一手条目被判 drop 时降级为 followup 保留——近期日报的
    对照物只有标题，分不清前报是官方还是媒体转述；「媒体先转述、官方后发文」时
    删官方等于系统性压制一手信息。机械保证：官方条目最多被标「接前报」，绝不静默消失。
    """
    if not items or not recent_items:
        return items
    role_labels = {"official": "官方", "media": "媒体", "personal": "个人", "leaderboard": "榜单"}
    entries = [
        {
            "idx": i,
            "title": it.title_cn or it.candidate.title,
            "company": it.company,
            "role": role_labels.get(it.candidate.source_role, "媒体"),
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
    # 官方例外:drop 降级为 followup(机械保证,不依赖提示词被遵守)
    official_kept = {i for i in drops if items[i].candidate.source_role == "official"}
    if official_kept:
        drops -= official_kept
        for i in official_kept:
            if not items[i].followup_note:
                items[i].followup_note = "官方一手确认"
        logger.info("日报跨天查重：%d 条官方条目免删，改标「接前报」", len(official_kept))
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
    backfilled: int = 0,
) -> str:
    """把择优条目确定性渲染成日报 markdown（分节/条目格式忠实沿用原 reduce 契约）。

    selected 已按 score 降序（select_top 出口），分节内顺序即重要性顺序。
    backfilled > 0 时在报头如实注明「过线内容不足、按分数补足」(软阈值,v3.49.1)。
    """
    sections: Dict[str, List[ScoredItem]] = {}
    for item in selected:
        label = prompts.classification_label(item.classification, item.candidate.content_type)
        sections.setdefault(label, []).append(item)

    parts: List[str] = [
        f"# 🤖 哆啦美 AI 资讯日报 · {report_date}",
        "",
        f"> 共收录 {len(selected) + len(title_only)} 条资讯，涵盖 {len(sections)} 个分类",
    ]
    if backfilled > 0:
        parts.append(f"> 今日过线内容不足，按新闻价值分补足 {backfilled} 条")
    parts += ["", "---", ""]
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
# 同日重跑合并（v3.35）
# 旧行为是整篇覆盖:游标在首跑后已推进,二跑只有增量候选,早间条目整批消失、
# 且今天自己的日报曾混进跨天查重对照物把增量剔光——净效果是重跑产出残报。
# 现改为「已有当日日报 → 新旧条目合并 + 同事件聚类 + 重排」再覆盖写。
# ==========================================

def _scored_item_from_stored(entry: Dict[str, Any], article_id: str) -> ScoredItem:
    """extensions.items 的存量 dict → ScoredItem(合并重排/重渲染用)。

    v3.35 起 items 自带 id/source_id/source_role/followup_note;历史日报缺这些键,
    id 由调用方按位对齐 included_article_ids 兜底,role 现算。
    """
    sid = str(entry.get("source_id") or "")
    cand = BriefCandidate(
        id=article_id,
        title=str(entry.get("title_cn") or ""),
        source_id=sid,
        source_url=str(entry.get("source_url") or ""),
        content_type=str(entry.get("content_type") or ""),
        publish_date=str(entry.get("publish_date") or ""),
        fetched_date="",
        has_content=True,
        body="",
        source_role=str(entry.get("source_role") or "") or source_role(sid),
    )
    return ScoredItem(
        candidate=cand,
        title_cn=str(entry.get("title_cn") or ""),
        classification=str(entry.get("classification") or ""),
        source=str(entry.get("source") or ""),
        company=str(entry.get("company") or ""),
        realm=str(entry.get("realm") or ""),
        summary=[str(s) for s in (entry.get("summary") or []) if s],
        comment=str(entry.get("comment") or ""),
        tags=[str(t) for t in (entry.get("tags") or []) if t],
        score=_coerce_score(entry.get("score")),
        extra_sources=[str(u) for u in (entry.get("extra_sources") or []) if u],
        followup_note=str(entry.get("followup_note") or ""),
    )


def load_existing_brief_state(
    session: Session, report_date: str
) -> Optional[Tuple[List[ScoredItem], List[BriefCandidate]]]:
    """读当日已有日报,重建 (正选条目, 附录候选);无当日日报返回 None。"""
    row = session.get(ArticleRecord, f"daily_brief_{report_date}")
    if row is None:
        return None
    try:
        ext = json.loads(row.extensions_json or "{}")
    except (ValueError, TypeError):
        ext = {}
    raw_items = ext.get("items") if isinstance(ext, dict) else None
    raw_items = [e for e in (raw_items or []) if isinstance(e, dict)]
    included = [str(i) for i in (ext.get("included_article_ids") or []) if i]
    items: List[ScoredItem] = []
    for i, entry in enumerate(raw_items):
        article_id = str(entry.get("id") or "")
        if not article_id:
            # 历史日报无 id 键:items 与 included_article_ids 前段同源同序,按位兜底
            article_id = included[i] if i < len(included) else f"legacy_{report_date}_{i}"
        items.append(_scored_item_from_stored(entry, article_id))
    # 附录 = included_article_ids 去掉正选前段后的余段,回库取标题/链接
    title_only: List[BriefCandidate] = []
    for article_id in included[len(raw_items):]:
        art = session.get(ArticleRecord, article_id)
        if art is None:
            continue
        title_only.append(
            BriefCandidate(
                id=art.id, title=art.title or "", source_id=art.source_id or "",
                source_url=art.source_url or "", content_type=art.content_type or "",
                publish_date=art.publish_date or "", fetched_date=art.fetched_date or "",
                has_content=False, body="",
                source_role=source_role(art.source_id or ""),
            )
        )
    return items, title_only


async def merge_same_day(
    prior_items: List[ScoredItem],
    new_items: List[ScoredItem],
    prior_title_only: List[BriefCandidate],
    new_title_only: List[BriefCandidate],
    llm_config: config.LLMConfig,
    usage_username: Optional[str] = None,
) -> Tuple[List[ScoredItem], List[BriefCandidate]]:
    """当日已有日报时的增量合并:旧∪新(按文章 id 去重)→ 同事件聚类 → 有效分重排。

    合并结果**不裁 top_n**:早间已发布的条目是既成事实,为凑配置条数把它删掉
    比日报略长更伤(神灯流水线 08:55 已消费过早间 items)。
    """
    seen = {it.candidate.id for it in prior_items}
    combined = prior_items + [it for it in new_items if it.candidate.id not in seen]
    # 早间批与增量批可能各报了同一事件(不同文章 id),再跑一次同事件聚类合并
    combined = await dedup_clusters(combined, llm_config, usage_username=usage_username)
    combined.sort(key=_effective_score, reverse=True)
    selected_ids = {it.candidate.id for it in combined}
    seen_titles = {c.id for c in prior_title_only}
    title_only = prior_title_only + [
        c for c in new_title_only if c.id not in seen_titles and c.id not in selected_ids
    ]
    title_only = [c for c in title_only if c.id not in selected_ids]
    return combined, title_only


# ==========================================
# 主编排
# ==========================================

@dataclass(frozen=True)
class _RecallText:
    """load_relevant_active_tags 只读 .title/.content——候选已在内存,不必再查 ArticleRecord。"""

    title: str
    content: str


def _count_queued_analyses(session: Session, article_ids: List[str]) -> int:
    """这批文章里有多少条分析行正 pending/running(日报补评与 worker 撞车的观测读数)。"""
    ids = [i for i in article_ids if i]
    if not ids:
        return 0
    rows = session.exec(
        select(ArticleAnalysisRecord.article_id)
        .where(ArticleAnalysisRecord.article_id.in_(ids))
        .where(ArticleAnalysisRecord.status.in_(("pending", "running")))
    ).all()
    return len(rows)


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

    triggered_by：手动触发的 admin 用户名，用于 AI 用量归属（含就地补评的
    article_analysis 用量）；定时调度留空则归 "system"。
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

    # 4. 评分(复用分析 / 就地补评)→ 阈值 → 聚类 → 择优 → 编辑
    with Session(engine) as session:
        min_score = daily_brief_min_score(session)
        min_items = daily_brief_min_items(session)
        stored = load_stored_scores(session, [c.id for c in candidates if c.has_content])
        pending_ids = [c.id for c in candidates if c.id not in stored]
        # 补评喂 worker 同一套闭集(按标题正文词法召回的小子集),两条来路的标签词汇才一致
        taxonomy_by_id = {
            c.id: tuple(load_relevant_active_tags(session, _RecallText(c.title, c.body)))
            for c in candidates if c.id not in stored
        }
        # 撞车观测:补评的候选里有多少正在 worker 队列里(随后会再算一次)
        inline_pending = _count_queued_analyses(session, pending_ids)
    n_inline = len(pending_ids)
    set_progress("scoring", f"评分 0/{n_inline}（已有分析 {len(stored)} 篇）", done=0, total=n_inline)

    def _on_score_done(done: int, total: int) -> None:
        set_progress("scoring", f"评分 {done}/{total}（已有分析 {len(stored)} 篇）", done=done, total=total)
        if done == total or done % 5 == 0:
            logger.info("日报[%s]：就地补评 %d/%d", report_date, done, total)

    scored = await score_candidates(
        candidates, cfg, stored=stored, on_item_done=_on_score_done, usage_username=triggered_by,
        taxonomy_by_id=taxonomy_by_id,
    )
    # 评分没到手的条目只有标题与链接——降入「📎 其它收录」附录;低于门槛的直接 pass;
    # 无正文候选按标题走了同一把尺子:过线者也只能进附录(没有正文可写要点与点评)
    score_failed = [it.candidate for it in scored if not it.score_ok]
    usable = [it for it in scored if it.score_ok and it.candidate.has_content and it.score >= min_score]
    bodyless_passed = [
        it.candidate for it in scored
        if it.score_ok and not it.candidate.has_content and it.score >= min_score
    ]
    below_threshold = sum(1 for it in scored if it.score_ok and it.score < min_score)
    histogram = score_histogram(scored)
    logger.info(
        "日报[%s]：评分完成——复用分析 %d 篇、补评 %d 篇（其中 %d 篇正在 worker 队列）、失败 %d 篇；"
        "低于门槛 %.1f 的 %d 篇；分布 %s",
        report_date, len(stored), n_inline, inline_pending, len(score_failed),
        min_score, below_threshold, histogram,
    )
    # 同日已有日报先读出来:软阈值的「正文保底」与「附录补位」都要按**最终成品**(当日已有
    # 正文 ∪ 本批)算缺口——否则同日重跑时早间已满员的日报还会被增量批的近线稿补一轮,
    # 附录也会一轮轮长成近线稿的倾倒场(codex 检视 P2 ×2)。
    with Session(engine) as session:
        prior_state = load_existing_brief_state(session, report_date)
    prior_items: List[ScoredItem] = list(prior_state[0]) if prior_state is not None else []
    prior_title_only: List[BriefCandidate] = list(prior_state[1]) if prior_state is not None else []
    prior_ids = {it.candidate.id for it in prior_items}
    # 软阈值(v3.49.1):近线带 = 有正文、分在 [min_score−APPENDIX_BAND, min_score) 的条目,按有效分降序。
    # 保底缺口在同事件归并**之后**计算:过线稿若互为重复会坍缩,缺口要按归并后的合格簇数算;
    # 近线带最多取 min_items 条陪跑进同一次聚类(与过线稿同事件的自然并入代表,不重复出场)。
    # 补进正文的近线稿照常写要点与点评;带内其余条目留作附录补位(正文没填满 top_n 时以
    # 标题+链接填进「其它收录」)。低于近线带的仍直接 pass。
    near_band: List[ScoredItem] = []
    if min_score > 0:
        near_band = sorted(
            (it for it in scored
             if it.score_ok and it.candidate.has_content
             and min_score - APPENDIX_BAND <= it.score < min_score),
            key=_effective_score, reverse=True,
        )
    # 陪跑名额远大于缺口(缺口×3 且不少于 NEAR_BAND_PROBE_MIN):陪跑稿之间也可能互为重复,
    # 归并后仍要够填缺口;聚类是一次轻量结构化调用,多带十几行标题的代价可忽略。
    want = max(0, min_items - len(prior_items)) if min_items else 0
    probe = near_band[:max(want * 3, NEAR_BAND_PROBE_MIN)] if want else []
    qualified_ids = {it.candidate.id for it in usable}

    set_progress("selecting", "同事件去重与择优排序…")
    # 当日已有正文一起进聚类:同日重跑时增量批与早间批报同一事件(不同文章 id)只算一个簇,
    # 缺口不会被重复计数;早间条目本身不进增量管线(由同日合并阶段统一处理)。
    deduped_all = await dedup_clusters(prior_items + usable + probe, cfg, usage_username=triggered_by)

    def _cluster_ids(item: ScoredItem) -> set:
        return {item.candidate.id, *item.merged_ids}

    counted_ids = qualified_ids | prior_ids
    core = [it for it in deduped_all if _cluster_ids(it) & counted_ids]
    core_ids = {it.candidate.id for it in core}
    prior_core = sum(1 for it in core if it.candidate.id in prior_ids)
    near_survivors = sorted(
        (it for it in deduped_all if it.candidate.id not in core_ids),
        key=_effective_score, reverse=True,
    )
    deficit = max(0, min_items - len(core)) if min_items else 0
    # 多带一条备用稿:合格稿或补足稿被跨天查重剔掉时顶上,终选再按剩余缺口裁回。
    backfilled: List[ScoredItem] = near_survivors[:deficit + (1 if deficit else 0)]
    backfill_ids = {it.candidate.id for it in backfilled}
    deduped = [it for it in core + backfilled if it.candidate.id not in prior_ids]
    consumed_ids: set = set()
    for it in core + backfilled:
        consumed_ids |= _cluster_ids(it)
    near_band = [it for it in near_band if it.candidate.id not in consumed_ids]
    logger.info(
        "日报[%s]：去重后 %d 条合格簇（本批过线 %d、当日已有正文 %d）；正文保底 %d 条：缺口 %d、"
        "近线带带入 %d 条（含备用）、余 %d 条待附录补位",
        report_date, len(core), len(usable), len(prior_items), min_items, deficit,
        len(backfilled), len(near_band),
    )
    # 扩选池(v3.35):跨天查重会剔条,旧流程剔完不回补——热点连报日成品远少于 top_n。
    # 现按 top_n+buffer 预选,查重幸存者再裁回 top_n:回补条目天然也过了跨天检查。
    select_buffer = max(3, top_n // 3)
    preselected = select_top(deduped, top_n=top_n + select_buffer)
    title_only = bodyless_passed + score_failed
    logger.info("日报[%s]：预选 %d 条（目标 %d + 回补池 %d，仅标题 %d 条）",
                report_date, len(preselected), top_n, select_buffer, len(title_only))

    set_progress("editing", f"撰写点评 0/{len(preselected)}", done=0, total=len(preselected))

    def _on_edit_done(done: int, total: int) -> None:
        set_progress("editing", f"撰写点评 {done}/{total}", done=done, total=total)

    preselected = await editorial_polish(
        preselected, cfg, on_item_done=_on_edit_done, usage_username=triggered_by,
    )

    with Session(engine) as session:
        # exclude_date=report_date:同日重跑时今天自己的日报不进对照物(否则增量被剔光)
        recent_items = fetch_recent_brief_items(
            session, days=recent_brief_days, exclude_date=report_date,
        )

    set_progress("reducing", "跨天查重与汇编…")
    logger.info("日报[%s]：跨天查重（对照近期日报 %d 天条目）后确定性渲染", report_date, len(recent_items))
    survivors = await cross_day_dedup(preselected, recent_items, cfg, usage_username=triggered_by)
    # 保底终裁:补足稿只留到**剩余缺口**(备用稿在合格稿/补足稿被跨天查重剔掉时顶上);
    # 没用上的备用稿退回近线带作附录补位候选,被跨天查重剔掉的(近期已报过)不退回。
    survivor_ids = {it.candidate.id for it in survivors}
    crossday_dropped = {it.candidate.id for it in preselected} - survivor_ids
    qualified_survivors = [it for it in survivors if it.candidate.id not in backfill_ids]
    need = max(0, min_items - prior_core - len(qualified_survivors)) if min_items else 0
    backfill_survivors = sorted(
        (it for it in survivors if it.candidate.id in backfill_ids),
        key=_effective_score, reverse=True,
    )
    survivors = qualified_survivors + backfill_survivors[:need]
    kept_ids = {it.candidate.id for it in survivors}
    near_band = sorted(
        near_band + [
            it for it in backfilled
            if it.candidate.id not in kept_ids and it.candidate.id not in crossday_dropped
        ],
        key=_effective_score, reverse=True,
    )
    selected = sorted(survivors, key=_effective_score, reverse=True)[:top_n]
    if len(survivors) > len(selected):
        logger.info("日报[%s]：查重幸存 %d 条，按有效分裁回 %d 条", report_date, len(survivors), len(selected))
    # 附录补位(v3.49.1):正文没填满 top_n 时,近线带余下条目按分数以标题+链接填进附录,
    # 只填空出的槽位(扣掉当日已有正文)——忙日正文满员则一条不加,同日重跑也不会一轮轮
    # 把附录堆成近线条目的倾倒场。
    selected_ids = {it.candidate.id for it in selected}
    # 早间条目若已被本批新稿并成同一簇(新稿当代表),同日合并会把两行收成一行,容量只扣一次
    dedup_rep_ids = {it.candidate.id for it in deduped_all}
    prior_absorbed = sum(1 for pid in prior_ids if pid not in dedup_rep_ids)
    appendix_slots = max(
        0, top_n - len(selected) - (len(prior_items) - prior_absorbed) - len(prior_title_only)
    )
    near_miss_appendix = [
        it.candidate for it in near_band if it.candidate.id not in selected_ids
    ][:appendix_slots]
    if near_miss_appendix:
        title_only = title_only + near_miss_appendix
        logger.info("日报[%s]：正文 %d 条未满 %d，近线带 %d 条以标题补进附录",
                    report_date, len(selected), top_n, len(near_miss_appendix))
    backfilled_in_brief = sum(1 for it in selected if it.candidate.id in backfill_ids)

    # 同日重跑合并:当日已有日报 → 新旧条目合并重排,不再整篇覆盖丢早间条目
    if prior_state is not None:
        logger.info("日报[%s]：当日已有日报（%d 条），执行增量合并", report_date, len(prior_items))
        selected, title_only = await merge_same_day(
            prior_items, selected, prior_title_only, title_only, cfg, usage_username=triggered_by,
        )
    markdown = render_brief_markdown(
        selected, title_only, report_date=report_date, backfilled=backfilled_in_brief,
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
            # 候选裁剪观测(v3.34):扫描≫取用 说明 max_total/per_source_cap 在裁,
            # 被裁条目随游标永久跳过——涨不涨上限看这两个数。
            "candidates_scanned": scanned_total, "candidates_used": len(candidates),
            # 评分来源观测(v3.48):复用分析 vs 就地补评 vs 门槛 pass——补评常态化说明
            # worker 没跟上(或总闸没开),门槛 pass 过多说明阈值偏高;scored_inline_pending
            # 是补评里正在 worker 队列的篇数(随后会再算一次,cron 该往后挪);
            # score_histogram 是本次全部到手分数的整数档分布,阈值校准的依据。
            "scored_stored": len(stored), "scored_inline": n_inline,
            "scored_inline_pending": inline_pending,
            "below_threshold": below_threshold, "min_score": min_score,
            "score_histogram": histogram,
            # 软阈值观测(v3.49.1):正文保底补足条数 / 附录补位条数——两者常态非零说明
            # 门槛偏高或名单过窄;全为零说明保底从未介入。
            "min_items": min_items, "threshold_backfilled": backfilled_in_brief,
            "near_miss_appendix": len(near_miss_appendix),
        })

    # The synthetic brief is not produced by a collection job, so its successful
    # persistence is the readiness signal.  Wake only users who subscribe to it;
    # failures here must never roll back an already-published public brief.
    try:
        from services.personal_digest import notify_public_daily_brief_ready

        await asyncio.to_thread(
            notify_public_daily_brief_ready,
            engine,
            report_date=report_date,
        )
    except Exception as exc:  # noqa: BLE001 - personal fan-out is independent
        logger.warning("日报[%s]：触发个人早报 revision 失败，等待巡检恢复: %s", report_date, exc)

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
