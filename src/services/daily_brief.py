"""每日 AI 资讯日报编排 (src/services/daily_brief.py)

流程：预处理(collect_candidates) → map_summarize(每篇 LLM 概括+打分)
     → dedup_clusters(同日同事件聚类) → select_top(按分数+多样性择优)
     → cross_day_dedup(跨天查重,一次轻量 LLM) → render_brief_markdown(确定性渲染)
     → 写库(幂等 update)。

v3.34 起 reduce 不再是整篇 LLM 长输出:markdown 由代码从结构化条目排版,
LLM 在汇编段只做「对照近几天日报条目标题判断 drop/接前报」的小 JSON 决策——
截断/漏条/复制篡改类静默劣化就此根除(2026-08 空正文事故的形态性风险消除)。

v3.35 权威机械层(生产实录:近 10 期日报头部名次官方源仅 1/30,官方并不迟到、
是排不上去):BriefCandidate 带 source_role(source_naming 后端角色镜像),
同事件代表权官方在分差门限内优先、select 排序官方 +0.5 有界加成、跨天查重
官方 drop 机械降级为 followup——三处全是确定性代码,不靠 LLM 自觉。
同波修同日重跑(合并而非覆盖,见 load_existing_brief_state/merge_same_day)、
跨天剔条回补(top_n+buffer 预选后裁回)、map 瞬时失败串行重试、候选两段式轻列取数。

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

# 日报各阶段的 LLM 用量归属：手动触发归到触发它的 admin，定时调度无登录上下文则归 "system"。
USAGE_SYSTEM = "system"


def _usage_meta(purpose: str, username: Optional[str]) -> UsageMeta:
    return UsageMeta(purpose=purpose, username=(username or USAGE_SYSTEM))
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
from services.source_naming import source_role

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
# 公共日报读取文章级分析的独立发布开关。默认关闭；关闭时 MAP、门槛、渲染和
# extensions.items 均继续走 legacy 路径。shadow 对比写入另一个内部 KV，不参与输出。
KEY_ANALYSIS_ADAPTER_ENABLED = "public_digest_analysis_adapter_enabled"
KEY_ANALYSIS_SHADOW_METRICS = "daily_brief_analysis_shadow_metrics"
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
    map_ok: bool = True
    # 同事件去重合并后，被并入本条的其它来源链接（供 reduce 渲染多来源）
    extra_sources: List[str] = field(default_factory=list)
    # 跨天查重判定为「同一事件的后续进展」时的一句增量说明（渲染成「（接前报）」行）
    followup_note: str = ""

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


@dataclass(frozen=True)
class PersistedAnalysisCompat:
    """一篇文章可供公共日报兼容 adapter 消费的只读投影。"""

    article_id: str
    quality_score: float
    summary: str
    content_genre: str
    canonical_tags: Tuple[str, ...] = ()


# 新 analysis 的 content_genre → legacy 公共日报 classification。映射只改变字段
# 形状，不改变公共日报的候选范围、择优配额或门槛（公共日报目前没有 7 分硬门槛）。
CONTENT_GENRE_TO_LEGACY_CLASSIFICATION: Dict[str, str] = {
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


def content_genre_to_legacy_classification(content_genre: str) -> str:
    """确定性映射新 genre；未知/空值返回空串，让调用方保留 legacy 分类。"""

    return CONTENT_GENRE_TO_LEGACY_CLASSIFICATION.get((content_genre or "").strip(), "")


def _analysis_summary_lines(summary: str) -> List[str]:
    """把文章级纯文本摘要收敛为 legacy ``summary: list[str]`` 形状。"""

    raw = (summary or "").strip()
    if not raw:
        return []
    lines = []
    for line in raw.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*+]|\d+[.)]|[•·])\s*", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return lines or [raw]


def load_persisted_analysis_compat(
    session: Session, article_ids: List[str]
) -> Dict[str, PersistedAnalysisCompat]:
    """批量读取成功 analysis 与 active canonical tags，避免公共日报 N+1。"""

    ids = list(dict.fromkeys(str(i) for i in article_ids if i))
    if not ids:
        return {}
    analyses = session.exec(
        select(ArticleAnalysisRecord)
        .where(ArticleAnalysisRecord.article_id.in_(ids))
        .where(
            or_(
                ArticleAnalysisRecord.status == "succeeded",
                ArticleAnalysisRecord.analyzed_at.is_not(None),
            )
        )
        .where(ArticleAnalysisRecord.quality_score.is_not(None))
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
    tags_by_article: Dict[str, List[str]] = {}
    for assignment, tag in tag_rows:
        display = (tag.name_zh or tag.name_en or tag.code or "").strip()
        if display and display not in tags_by_article.setdefault(assignment.article_id, []):
            tags_by_article[assignment.article_id].append(display)

    return {
        row.article_id: PersistedAnalysisCompat(
            article_id=row.article_id,
            quality_score=float(row.quality_score),
            summary=row.summary or "",
            content_genre=row.content_genre or "",
            canonical_tags=tuple(tags_by_article.get(row.article_id, [])),
        )
        for row in analyses
    }


def build_analysis_shadow_metrics(
    legacy_items: List[ScoredItem],
    persisted: Dict[str, PersistedAnalysisCompat],
) -> Dict[str, Any]:
    """汇总同批新旧评分、摘要和分类差异；不改变任何条目。"""

    comparable = [it for it in legacy_items if it.candidate.id in persisted]
    score_deltas = [
        abs(it.score - persisted[it.candidate.id].quality_score)
        for it in comparable
    ]
    classification_matches = 0
    summary_matches = 0
    summary_similarities: List[float] = []
    for item in comparable:
        new = persisted[item.candidate.id]
        mapped = content_genre_to_legacy_classification(new.content_genre)
        if mapped and mapped == (item.classification or "").strip():
            classification_matches += 1
        legacy_summary = " ".join(item.summary).strip()
        analysis_summary = " ".join(_analysis_summary_lines(new.summary)).strip()
        if legacy_summary and analysis_summary and legacy_summary == analysis_summary:
            summary_matches += 1
        if legacy_summary and analysis_summary:
            summary_similarities.append(
                difflib.SequenceMatcher(None, legacy_summary, analysis_summary).ratio()
            )
    count = len(comparable)
    return {
        "legacy_count": len(legacy_items),
        "persisted_count": len(persisted),
        "comparable_count": count,
        "score_mean_abs_delta": (
            round(sum(score_deltas) / count, 4) if count else None
        ),
        "score_max_abs_delta": round(max(score_deltas), 4) if score_deltas else None,
        "score_within_1_count": sum(delta <= 1.0 for delta in score_deltas),
        "classification_match_count": classification_matches,
        "summary_exact_match_count": summary_matches,
        "summary_mean_similarity": (
            round(sum(summary_similarities) / len(summary_similarities), 4)
            if summary_similarities else None
        ),
    }


def apply_persisted_analysis_adapter(
    legacy_items: List[ScoredItem],
    persisted: Dict[str, PersistedAnalysisCompat],
) -> List[ScoredItem]:
    """覆盖可复用字段，同时保留 legacy title/source/company/realm/comment。

    ``score_reason`` 不在投影中，因而绝不可能被误当成公共日报 ``comment``。
    没有成功 analysis 的文章逐对象原样返回，允许渐进覆盖。
    """

    adapted: List[ScoredItem] = []
    for item in legacy_items:
        analysis = persisted.get(item.candidate.id)
        if analysis is None:
            adapted.append(item)
            continue
        summary = _analysis_summary_lines(analysis.summary) or item.summary
        classification = (
            content_genre_to_legacy_classification(analysis.content_genre)
            or item.classification
        )
        adapted.append(
            replace(
                item,
                classification=classification,
                summary=summary,
                tags=list(analysis.canonical_tags) or item.tags,
                score=analysis.quality_score,
            )
        )
    return adapted


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

        results = await asyncio.gather(*[_guarded(c) for c in with_body])
        # 瞬时故障补救(v3.35):map 失败会降入附录且游标照推——LLM 端点中段抖动几分钟,
        # 那批文章就永久定格成裸标题。整轮结束后对失败者**串行**重试一次(避开并发压力,
        # 端点恢复即救回),仍失败才认作真失败。
        failed_idx = [i for i, r in enumerate(results) if not r.map_ok]
        if failed_idx:
            logger.info("日报 map:%d 条失败,串行重试一轮", len(failed_idx))
            for i in failed_idx:
                retried = await _summarize_one(with_body[i], map_config, usage_meta, http_client)
                if retried.map_ok:
                    results[i] = retried
        return results


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
        rep = _pick_cluster_representative(items, idxs)
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


def fetch_recent_brief_items(
    session: Session, *, days: int = 3, exclude_date: str = ""
) -> List[Dict[str, Any]]:
    """近几天日报的条目标题清单（跨天查重的对照物）。

    优先读 extensions.items（结构化 title_cn），缺失时回退从正文提取「###」
    标题行；返回形如 [{"date": "YYYY-MM-DD", "titles": [...]}, ...]。
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

    # 文章级分析先 shadow 对比、再由独立开关决定是否覆盖兼容字段。legacy MAP 始终
    # 保留：它继续提供 title_cn/source/company/realm/comment，尤其不能拿 score_reason
    # 冒充既有点评文案。任何读取/映射异常都降级回完整 legacy 路径。
    analysis_adapter_enabled = False
    shadow_metrics: Dict[str, Any] = {}
    try:
        with Session(engine) as session:
            analysis_adapter_enabled = (
                get_setting(session, KEY_ANALYSIS_ADAPTER_ENABLED, "false").strip().casefold()
                in {"1", "true", "yes", "on"}
            )
            persisted_analysis = load_persisted_analysis_compat(
                session, [it.candidate.id for it in scored]
            )
        shadow_metrics = build_analysis_shadow_metrics(scored, persisted_analysis)
        shadow_metrics.update({
            "report_date": report_date,
            "adapter_enabled": analysis_adapter_enabled,
            "measured_at": datetime.now().isoformat(),
        })
        if analysis_adapter_enabled:
            scored = apply_persisted_analysis_adapter(scored, persisted_analysis)
        logger.info(
            "日报[%s]：analysis shadow 可比 %d/%d，adapter=%s",
            report_date,
            shadow_metrics["comparable_count"],
            shadow_metrics["legacy_count"],
            analysis_adapter_enabled,
        )
    except Exception as exc:  # noqa: BLE001 adapter 失败不能阻断公共日报
        logger.warning("日报 analysis shadow/adapter 读取失败，回退 legacy: %s", exc)
        shadow_metrics = {
            "report_date": report_date,
            "adapter_enabled": analysis_adapter_enabled,
            "error": type(exc).__name__,
            "measured_at": datetime.now().isoformat(),
        }

    set_progress("selecting", "同事件去重与择优排序…")
    deduped = await dedup_clusters(scored, cfg, usage_username=triggered_by)
    logger.info("日报[%s]：去重后 %d 条（map 前 %d）", report_date, len(deduped), len(scored))
    # map 失败的条目没有总结/点评且 score 是占位值——不参与择优（曾以默认 3 分
    # 混入正选,渲染出无总结无点评的残条目),降入「📎 其它收录」附录保标题与链接。
    map_failed = [it.candidate for it in deduped if not it.map_ok]
    usable = [it for it in deduped if it.map_ok]
    if map_failed:
        logger.info("日报[%s]：map 失败 %d 条降入「其它收录」附录", report_date, len(map_failed))
    # 扩选池(v3.35):跨天查重会剔条,旧流程剔完不回补——热点连报日成品远少于 top_n。
    # 现按 top_n+buffer 预选,查重幸存者再裁回 top_n:回补条目天然也过了跨天检查。
    select_buffer = max(3, top_n // 3)
    preselected = select_top(usable, top_n=top_n + select_buffer)
    title_only = [c for c in candidates if not c.has_content] + map_failed
    logger.info("日报[%s]：预选 %d 条（目标 %d + 回补池 %d，仅标题 %d 条）",
                report_date, len(preselected), top_n, select_buffer, len(title_only))

    with Session(engine) as session:
        # exclude_date=report_date:同日重跑时今天自己的日报不进对照物(否则增量被剔光)
        recent_items = fetch_recent_brief_items(
            session, days=recent_brief_days, exclude_date=report_date,
        )

    set_progress("reducing", "跨天查重与汇编…")
    logger.info("日报[%s]：跨天查重（对照近期日报 %d 天条目）后确定性渲染", report_date, len(recent_items))
    survivors = await cross_day_dedup(preselected, recent_items, cfg, usage_username=triggered_by)
    selected = sorted(survivors, key=_effective_score, reverse=True)[:top_n]
    if len(survivors) > len(selected):
        logger.info("日报[%s]：查重幸存 %d 条，按有效分裁回 %d 条", report_date, len(survivors), len(selected))

    # 同日重跑合并:当日已有日报 → 新旧条目合并重排,不再整篇覆盖丢早间条目
    with Session(engine) as session:
        prior_state = load_existing_brief_state(session, report_date)
    if prior_state is not None:
        prior_items, prior_title_only = prior_state
        logger.info("日报[%s]：当日已有日报（%d 条），执行增量合并", report_date, len(prior_items))
        selected, title_only = await merge_same_day(
            prior_items, selected, prior_title_only, title_only, cfg, usage_username=triggered_by,
        )
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

    # shadow 是旁路观测，写指标失败绝不能把已成功发布的公共日报翻成失败。
    if shadow_metrics:
        try:
            with Session(engine) as session:
                set_json_setting(session, KEY_ANALYSIS_SHADOW_METRICS, shadow_metrics)
        except Exception as exc:  # noqa: BLE001 观测失败不阻断主流程
            logger.warning("日报 analysis shadow 指标写入失败，忽略: %s", exc)

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
