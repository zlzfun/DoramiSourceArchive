"""订阅域问答检索 (src/services/reader_search.py) — 「LLM 计划检索 + FTS5」两段式。

v3.30 检索扶正波(方案 docs/rag-retirement-plan.md §2):取代向量 RAG 成为
scope=subscription 问答的主路径。管线:

  意图分流(闲聊/对话元问题 → 不检索纯对话;时效浏览型 → 直接时序窗口,不检索)
  → 查询规划(1 次轻量 LLM,JSON mode:中英关键词改写 + 日期窗 + use_brief + chat)
  → FTS5 召回(storage.fts.fts_search_ids,多组关键词取并集 ∩ 订阅域 ∩ 日期窗)
  → 选篇(1 次轻量 LLM:标题+来源+日期+引子,挑最相关的若干篇)
  → 全文注入(复用 reader_ai.build_numbered_context 编号截断拼接,[n] 即引用锚)

降级链(任何一级失败都不 5xx):规划失败 → 用户原词直接 FTS;FTS 不可用 → 标题
LIKE;零命中 → 订阅域时序窗口;选篇失败 → 取候选前 N 篇。「日报即索引」:跨期
盘点型问题(use_brief)在已订阅日报源时改检索日报正文——每日精选是现成压缩层。

两次 LLM 调用的计费归因并入 ask(不拆新 purpose),由调用方传入同一 usage_meta。
本模块只依赖 engine/LLM,不依赖 FastAPI;订阅域(source_ids,已含隐藏源排除)由
调用方解析后传入——与 reader_ai 的 D11 闭包注入形态一致,可独立单测。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import literal_column, or_
from sqlmodel import Session, select

from config import LLMConfig
from llm import prompts
from llm.client import ChatMessage, UsageMeta, chat_completion, parse_json_object
from models.db import ArticleRecord
from services.reader_ai import build_numbered_context, build_sources_payload
from services import user_sources as user_sources_service
from storage.fts import fts_search_ranked

logger = logging.getLogger(__name__)

# 日报特殊源(非抓取器):跨期盘点型问题的「日报即索引」检索目标。
BRIEF_SOURCE_ID = "dorami_daily_brief"

# FTS 召回候选上限(进 SQL 的排序截断)与送入选篇 prompt 的候选池上限。
CANDIDATE_LIMIT = 100
SELECT_POOL = 60
# 选篇上限(与提示词中的「最多选 8 篇」一致)。
SELECT_MAX = 8
# 候选行引子长度(标题之外帮 LLM 判断相关性的正文开头/摘要片段)。
_LEAD_CHARS = 160
# 注入上下文的截断预算(选中篇目全文,逐篇与整体)。
_CONTEXT_PER_ARTICLE = 2000
_CONTEXT_TOTAL = 14000
# rowid IN (...) 的防御性上限(SQLite 变量数限制;FTS 命中超此即截断,
# 靠日期倒序排序保住最近的候选)。
_MAX_FTS_IDS = 10000


def _exportable_source_ids(engine, source_ids: List[str]) -> List[str]:
    with Session(engine) as session:
        return user_sources_service.external_ai_allowed_source_ids(session, source_ids)


def _exportable_articles(engine, articles: List[ArticleRecord]) -> List[ArticleRecord]:
    allowed = set(_exportable_source_ids(
        engine, [str(article.source_id or "") for article in articles]
    ))
    return [article for article in articles if article.source_id in allowed]


# ==================== 查询规划 ====================

def _history_text(history: Optional[List[Any]]) -> str:
    """把多轮历史压成规划器可读的短文本(最近几条,单条截断)。

    追问型问题(「再展开讲讲第二点」)只有结合上文才能还原出可检索的完整问题;
    清洗复用 reader_ai._sanitize_history(role 白名单/截断),这里再压到规划
    prompt 的预算内——历史是给规划器理解指代用的,不是给它复述的。
    """
    from services.reader_ai import _sanitize_history

    turns = _sanitize_history(history)[-4:]
    lines: List[str] = []
    for turn in turns:
        speaker = "读者" if turn.role == "user" else "哆啦美"
        content = turn.content if len(turn.content) <= 300 else turn.content[:300] + "…"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


async def plan_query(
    question: str,
    llm_config: LLMConfig,
    usage_meta: Optional[UsageMeta] = None,
    *,
    history_text: str = "",
) -> Optional[Dict[str, Any]]:
    """把自然语言问题规划成检索计划;任何失败返回 None(降级为原词检索)。

    history_text 非空时随问题注入最近对话——追问/指代型问题须结合上文还原成
    独立问题再规划(v3.34,此前每轮只看当前问句,追问会规划出无意义关键词)。
    返回形状:{"keywords": [str], "date_gte": str|None, "date_lte": str|None,
    "temporal": bool, "use_brief": bool, "chat": bool}(字段已清洗校验)。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        raw = await chat_completion(
            messages=[
                ChatMessage(role="system", content=prompts.SEARCH_PLAN_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompts.build_search_plan_user_prompt(
                    question, today=today, history_text=history_text,
                )),
            ],
            config=llm_config,
            response_json=True,
            usage_meta=usage_meta,
        )
        data = parse_json_object(raw)
    except Exception as exc:  # noqa: BLE001 — 规划是增强,失败必须静默降级
        logger.warning("检索规划失败,降级为原词检索: %s", exc)
        return None

    keywords = [
        k.strip() for k in data.get("keywords") or []
        if isinstance(k, str) and len(k.strip()) >= 2
    ]

    def _date(key: str) -> Optional[str]:
        value = data.get(key)
        if isinstance(value, str) and len(value.strip()) >= 8:
            return value.strip()[:10]
        return None

    return {
        "keywords": keywords[:6],
        "date_gte": _date("date_gte"),
        "date_lte": _date("date_lte"),
        "temporal": bool(data.get("temporal")),
        "use_brief": bool(data.get("use_brief")),
        "chat": bool(data.get("chat")),
    }


# ==================== 候选召回 ====================

def fetch_candidates(
    engine,
    *,
    keywords: List[str],
    source_ids: List[str],
    date_gte: Optional[str] = None,
    date_lte: Optional[str] = None,
    limit: int = CANDIDATE_LIMIT,
) -> List[ArticleRecord]:
    """按关键词组并集召回,限定检索域与日期窗,**相关性(bm25)优先**排序截断。

    v3.34 检索质量修复:此前召回只按发布日期倒序截断,FTS 的 bm25 分数被整个
    丢弃——宽关键词高命中时真正对题但稍旧的文章会被日期序挤出候选池。现行为:

    - trigram 可用的关键词走 FTS(fts_search_ranked),逐 rowid 取各关键词里
      最好(最小)的 bm25 rank;
    - 短于 trigram 下限(3 字符)的关键词(常见:两字中文实体名如「豆包」)与
      FTS 整体不可用时的关键词,走标题 LIKE **补召回**——并入同一候选池,
      无 rank 的命中排在 FTS 命中之后、按日期倒序;
    - 排序:rank 升序(越小越相关)为主序,发布日期倒序为次序;两阶段取数——
      先取轻量列(id/rowid/日期)排序截断,再按序装载完整记录,避免为排序把
      全量正文载入内存。
    keywords 为空或无候选时返回 []。
    """
    source_ids = _exportable_source_ids(engine, source_ids)
    if not keywords or not source_ids:
        return []
    with Session(engine) as session:
        ranks: Dict[int, float] = {}
        like_keywords: List[str] = []
        for keyword in keywords:
            hits = fts_search_ranked(session, keyword)
            if hits is None:
                # 不可用(FTS 缺失)或过短(trigram 命不中)——进 LIKE 补召回分支。
                like_keywords.append(keyword)
                continue
            for rowid, rank in hits.items():
                if rowid not in ranks or rank < ranks[rowid]:
                    ranks[rowid] = rank

        rowid_col = literal_column("articles.rowid")
        conditions = []
        if ranks:
            top_ids = sorted(ranks, key=ranks.get)[:_MAX_FTS_IDS]
            conditions.append(rowid_col.in_(top_ids))
        if like_keywords:
            conditions.append(
                or_(*[ArticleRecord.title.contains(keyword) for keyword in like_keywords])
            )
        if not conditions:
            return []

        query = (
            select(ArticleRecord.id, rowid_col.label("rid"), ArticleRecord.publish_date)
            .where(
                ArticleRecord.source_id.in_(source_ids),
                ArticleRecord.has_content == True,  # noqa: E712
                or_(*conditions),
            )
        )
        if date_gte:
            query = query.where(ArticleRecord.publish_date >= date_gte)
        if date_lte:
            # 上限含当日(publish_date 可能带时间部分,补日末哨兵)。
            query = query.where(ArticleRecord.publish_date <= f"{date_lte}~")
        rows = list(session.exec(query).all())
        # 稳定排序两趟:先日期倒序(次序),再 rank 升序(主序);LIKE 命中无 rank,
        # 以 +inf 落在全部 FTS 命中之后、组内保持日期倒序。
        rows.sort(key=lambda r: (r[2] or ""), reverse=True)
        rows.sort(key=lambda r: ranks.get(r[1], float("inf")))
        ordered_ids = [r[0] for r in rows[:limit]]
        if not ordered_ids:
            return []
        by_id = {
            rec.id: rec
            for rec in session.exec(
                select(ArticleRecord).where(ArticleRecord.id.in_(ordered_ids))
            ).all()
        }
        return [by_id[i] for i in ordered_ids if i in by_id]


def fetch_recent_window(
    engine, source_ids: List[str], *, limit: int = CANDIDATE_LIMIT
) -> List[ArticleRecord]:
    """时序窗口档:订阅域内按抓取时间倒序的最近若干篇有正文文章。"""
    source_ids = _exportable_source_ids(engine, source_ids)
    if not source_ids:
        return []
    with Session(engine) as session:
        query = (
            select(ArticleRecord)
            .where(
                ArticleRecord.source_id.in_(source_ids),
                ArticleRecord.has_content == True,  # noqa: E712
            )
            .order_by(ArticleRecord.fetched_date.desc())
            .limit(limit)
        )
        return list(session.exec(query).all())


# ==================== 选篇 ====================

def _lead(record: ArticleRecord) -> str:
    """候选行引子:正文开头压成单行截断(帮 LLM 判断相关性,非展示文案)。"""
    body = " ".join((record.content or "").split())
    return body[:_LEAD_CHARS]


def _candidate_line(idx: int, record: ArticleRecord) -> str:
    date = (record.publish_date or "")[:10]
    return f"[{idx}] {record.title or '(无标题)'} | {record.source_id} | {date}\n    {_lead(record)}"


async def select_articles(
    question: str,
    candidates: List[ArticleRecord],
    llm_config: LLMConfig,
    usage_meta: Optional[UsageMeta] = None,
) -> List[ArticleRecord]:
    """让 LLM 从候选中挑最相关的若干篇;候选不多时跳过调用,失败时取前 N 篇。

    LLM 明确判定「无一相关」(selected=[])时如实返回空列表——此时问答会基于
    空资料诚实回答「无法确定」,好过硬塞不相关的内容。
    """
    if not candidates:
        return []
    if len(candidates) <= SELECT_MAX:
        return candidates
    pool = candidates[:SELECT_POOL]
    lines = [_candidate_line(i, record) for i, record in enumerate(pool)]
    try:
        raw = await chat_completion(
            messages=[
                ChatMessage(role="system", content=prompts.SEARCH_SELECT_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompts.build_search_select_user_prompt(
                    question, lines, today=datetime.now().strftime("%Y-%m-%d"),
                )),
            ],
            config=llm_config,
            response_json=True,
            usage_meta=usage_meta,
        )
        data = parse_json_object(raw)
        selected = data.get("selected")
        if not isinstance(selected, list):
            raise ValueError("selected 不是数组")
        picked: List[ArticleRecord] = []
        for idx in selected:
            if isinstance(idx, int) and 0 <= idx < len(pool):
                record = pool[idx]
                if record not in picked:
                    picked.append(record)
            if len(picked) >= SELECT_MAX:
                break
        return picked
    except Exception as exc:  # noqa: BLE001 — 选篇是精排增强,失败退回截断
        logger.warning("检索选篇失败,退回候选前 %s 篇: %s", SELECT_MAX, exc)
        return candidates[:SELECT_MAX]


# ==================== 编排 ====================

def _window_text(date_gte: Optional[str], date_lte: Optional[str]) -> str:
    if date_gte and date_lte:
        return f"{date_gte} 至 {date_lte}"
    if date_gte:
        return f"{date_gte} 之后"
    return f"{date_lte} 之前"

def _notify(progress: Optional[Any], stage: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """阶段进度上报(v3.32 阶段化等待态):回调由调用方注入,失败绝不阻断检索。"""
    if progress is None:
        return
    try:
        progress(stage, detail or {})
    except Exception:  # noqa: BLE001 — 进度是观测面,不是主流程
        pass


async def subscription_context(
    question: str,
    *,
    engine,
    source_ids: List[str],
    llm_config: LLMConfig,
    usage_meta: Optional[UsageMeta] = None,
    progress: Optional[Any] = None,
    corpus_label: str = "读者订阅内容",
    history: Optional[List[Any]] = None,
) -> tuple:
    """检索圈定型问答(scope=subscription/all)的上下文组装,返回 ``(context, sources)``。

    source_ids 为调用方解析好的检索域(订阅域或全库可见源,均已排除隐藏源)——
    本管线自身范围无关,名字保留 subscription_ 前缀是因为订阅域是它的主场景。
    空域返回空上下文,由问答提示词如实说明资料不足。
    progress(stage, detail) 可选:plan → search → select 三阶段上报(答案阶段由调用方报)。
    corpus_label:检索说明里对语料域的称呼——subscription 档是「读者订阅内容」,
    all 档应传「哆啦美收录内容」;措辞跟着检索域走,否则 all 档(如 IM 代答渠道)
    的降级说明会把全库语料说成提问者的订阅(v3.33.2)。
    history:多轮对话历史(与作答侧同一份),供规划器还原追问/指代(v3.34)。
    规划与选篇是轻量结构化调用,走辅助轻模型档(llm_config.for_aux(),未配置
    aux_model 时即主模型)。
    """
    source_ids = _exportable_source_ids(engine, source_ids)
    if not source_ids:
        return "", []
    aux_config = llm_config.for_aux()

    _notify(progress, "plan")
    plan = await plan_query(question, aux_config, usage_meta, history_text=_history_text(history))
    if plan is not None and plan.get("chat"):
        # 闲聊/对话元问题(v3.32 二轮返修):与资讯内容无关,不检索不注入,纯对话作答
        # ——等待态由此从「理解问题」直接跳到「组织回答」,不画未发生的检索阶段。
        return "", []
    _notify(progress, "search", {
        "keywords": len(plan["keywords"]) if plan else 0,
        "temporal": bool(plan and plan["temporal"]),
    })

    candidates: List[ArticleRecord] = []
    # 检索说明(v3.32 时效诚实性):降级链一旦改写了检索语义(放宽日期窗/丢掉主题),
    # 把这个事实**机械注入**上下文头部——「资料早于问题范围要如实说明」若只靠作答
    # 模型自觉对照日期,弱模型会照样把旧闻包装成新进展(实测)。
    notice = ""
    if plan is None:
        # 规划失败:用户原词直接检索(FTS 内部自带 LIKE 回退)。
        candidates = fetch_candidates(
            engine, keywords=[question.strip()], source_ids=source_ids
        )
    elif not plan["temporal"] and plan["keywords"]:
        # 「日报即索引」:跨期盘点型且已订阅日报源时,改检索日报正文。
        search_source_ids = source_ids
        if plan["use_brief"] and BRIEF_SOURCE_ID in set(source_ids):
            search_source_ids = [BRIEF_SOURCE_ID]
        candidates = fetch_candidates(
            engine,
            keywords=plan["keywords"],
            source_ids=search_source_ids,
            date_gte=plan["date_gte"],
            date_lte=plan["date_lte"],
        )
        if not candidates and (plan["date_gte"] or plan["date_lte"]):
            # 窗口内零命中 ≠ 库里没有该主题:先放宽日期窗保主题,好过直接丢主题看最新。
            candidates = fetch_candidates(
                engine, keywords=plan["keywords"], source_ids=search_source_ids
            )
            if candidates:
                notice = (
                    f"(检索说明:读者要求的时间范围({_window_text(plan['date_gte'], plan['date_lte'])})"
                    "内没有命中的文章;以下是同一主题在库中可得的资料,发布日期见各条标注。"
                    "回答时请先如实说明这一点,再基于这些资料作答——不要把它们说成该时间范围内的新进展。)"
                )

    # 时效浏览型 / 零命中兜底:订阅域时序窗口。
    if not candidates:
        candidates = fetch_recent_window(engine, source_ids)
        if candidates and not (plan and plan["temporal"]):
            notice = (
                f"(检索说明:没有检索到与问题直接相关的文章;以下是{corpus_label}里最新的一批条目,"
                "发布日期见各条标注。若其中没有能回答问题的内容,请如实说明,不要强行关联。)"
            )
    if not candidates:
        return "", []

    # Recheck after retrieval so a source reclassified during query planning
    # cannot leak its lead text into the selection prompt.
    candidates = _exportable_articles(engine, candidates)
    if not candidates:
        return "", []

    _notify(progress, "select", {"candidates": len(candidates)})
    chosen = await select_articles(question, candidates, aux_config, usage_meta)
    # Recheck once more before constructing the answer context.
    chosen = _exportable_articles(engine, chosen)
    # 上下文与 sources 必须同源同序(编号 [n] 即引用锚,见 reader_ai 核心层)。
    # 单篇预算按选中篇数摊分(下限保底):选中少时长文能带出更多正文,
    # 不再固定每篇 2000 字符只喂开头(v3.34)。
    per_article = max(_CONTEXT_PER_ARTICLE, _CONTEXT_TOTAL // max(1, len(chosen)))
    context, included = build_numbered_context(
        chosen, per_article_chars=per_article, total_chars=_CONTEXT_TOTAL
    )
    if notice:
        context = f"{notice}\n\n{context}" if context else notice
    return context, build_sources_payload(included)
