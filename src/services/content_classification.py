"""内容分类词表与条目「内容类型 / 事件类型」推导(issue #178 第一波)。

全站分类的单一事实来源。一个维度只回答一个问题:

- **呈现形态**(源级 ``content_shape``:article / social / podcast)——用哪种界面呈现;
- **来源角色**(``source_naming.source_role``)——谁在说话;
- **内容类型 kind**(条目级,本模块)——这是什么:资讯 / 论文 / 教程 / 观点 / 版本更新 / 项目 / 汇编;
- **事件类型 event_type**(条目级,本模块)——发生了哪类事,只对事件类内容(资讯 / 版本更新 / 项目)有意义;
- **数据结构** ``content_type``——决定 ``extensions_json`` 的字段形状,feed / 订阅 / 归档同步契约依赖它,
  **不再当作分类对读者展示**(台账里的中文名也改为数据结构名,见 ``CONTENT_TYPE_LABELS``)。

内容类型与事件类型在读取时推导:「来源先验 → 数据结构 → 链接形状 → 模型给的类型 → 体裁」的确定性纯函数。
输入全部已入库并随归档同步传到接收节点,两端用同一份代码算出同一结果。第一波只用规则与体裁;第二波
(内容价值)起分析提示词直接给出内容类型(``article_analyses.content_kind``),排在规则之后、体裁之前。

第二波还在这里定义**双评分的读取规则**:新闻价值(``quality_score``)与内容价值(``content_value``,
独立版本号)各自缺失即缺席,读者面显示哪个作主分由 ``primary_dimension`` 决定(``score_projection``)。

本模块刻意不在顶层导入抓取器注册表或 ORM:``digest_selection`` 等纯策略模块也从这里取词表,
注册表与 ORM 只在需要来源规则 / SQL 的函数内部惰性导入。
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from llm.article_analysis_prompt import CONTENT_VALUE_SCORING_VERSION


# ── 内容类型 ──────────────────────────────────────────────────────────────────
# 顺序即读者面筛选条的呈现顺序。「资讯」是默认值,读者面不显示类型签、也不进筛选条。
ITEM_KIND_LABELS: dict[str, str] = {
    "news": "资讯",
    "paper": "论文",
    "tutorial": "教程",
    "opinion": "观点",
    "release": "版本更新",
    "project": "项目",
    "digest": "汇编",
}
ITEM_KINDS: tuple[str, ...] = tuple(ITEM_KIND_LABELS)
DEFAULT_ITEM_KIND = "news"
# 读者面筛选条可选的类型(资讯是默认值,不单列)。
FILTERABLE_ITEM_KINDS: tuple[str, ...] = tuple(k for k in ITEM_KINDS if k != DEFAULT_ITEM_KIND)
# 版本更新在列表里用紧凑行(取代原「动态」容器的短条目扫读形)。
COMPACT_ITEM_KINDS: frozenset[str] = frozenset({"release"})
# 事件类型只对这几类内容有意义;论文 / 教程 / 观点 / 汇编不是对某件事的报道。
EVENT_BEARING_KINDS: frozenset[str] = frozenset({"news", "release", "project"})

# ── 事件类型 ──────────────────────────────────────────────────────────────────
EVENT_TYPE_LABELS: dict[str, str] = {
    "model_release": "模型发布",
    "product_update": "产品更新",
    "open_source": "开源发布",
    "research": "研究成果",
    "industry": "行业动态",
    "policy": "政策监管",
    "security": "安全事件",
}
EVENT_TYPES: tuple[str, ...] = tuple(EVENT_TYPE_LABELS)

# ── 体裁(article_analyses.content_genre,分析提示词的闭集)──────────────────────
# 第一波它仍是分析的输出;读者面改看内容类型 + 事件类型,体裁中文名只作兜底展示。
GENRE_LABELS: dict[str, str] = {
    "model_release": "模型发布",
    "product_update": "产品更新",
    "open_source_update": "开源动态",
    "research_paper": "学术论文",
    "tutorial": "教程",
    "opinion": "观点",
    "industry_news": "行业资讯",
    "conference": "技术大会",
    "social_discussion": "社交讨论",
    "aggregation": "资讯聚合",
    "security_incident": "安全事件",
    "regulation": "监管政策",
    "other": "其他",
}

# 体裁 → (内容类型, 事件类型)。体裁把「这是什么」和「发生了哪类事」压在一个单选里,这里拆开。
# research_paper 映射到「资讯 + 研究成果」:分析看到的是一篇讲研究的内容,它不一定是论文原文;
# 论文原文由来源先验(HF Daily Papers)与链接形状(arXiv / OpenReview)判定,优先于体裁。
GENRE_KIND_EVENT: dict[str, tuple[str, Optional[str]]] = {
    "model_release": ("news", "model_release"),
    "product_update": ("news", "product_update"),
    "open_source_update": ("news", "open_source"),
    "research_paper": ("news", "research"),
    "tutorial": ("tutorial", None),
    "opinion": ("opinion", None),
    "industry_news": ("news", "industry"),
    "conference": ("news", "industry"),
    "social_discussion": ("news", None),
    "aggregation": ("digest", None),
    "security_incident": ("news", "security"),
    "regulation": ("news", "policy"),
    "other": ("news", None),
}

# ── 数据结构 content_type 的中文名(管理面台账 / 详情用)────────────────────────
# 刻意写成数据结构名而不是内容分类:rss_article / web_article 只说明「怎么抓进来的」,
# 此前叫「资讯文章」,导致 HF Daily Papers 的论文和各家 changelog 在台账里都成了「资讯文章」。
CONTENT_TYPE_LABELS: dict[str, str] = {
    "rss_article": "RSS 条目",
    "web_article": "网页条目",
    "wechat_article": "公众号文章",
    "arxiv": "arXiv 论文",
    "github_release": "GitHub Release",
    "github_repository": "GitHub 仓库",
    "github_trending": "GitHub 趋势",
    "hf_model": "HF 模型卡",
    "huggingface_model": "HF 模型卡",
    "podcast": "播客",
    "podcast_episode": "播客单集",
    "social_post": "社交帖",
    "daily_brief": "日报",
    "tech_conference": "技术会议",
    "ai_company_blog": "厂商博客",
    "ai_community": "AI 社区",
    "ai_tools": "AI 工具",
    "webhook_trigger": "工作流",
}

# ── 分区映射(从各消费方收拢到这里,取值保持不变)──────────────────────────────
# 个人早报板块:按体裁分节(issue #74 固定板块序见 digest_selection.SECTION_ORDER)。
PERSONAL_DIGEST_GENRE_SECTIONS: dict[str, str] = {
    "model_release": "模型发布",
    "open_source_update": "开源动态",
    "research_paper": "学术论文",
    "conference": "技术大会",
    "social_discussion": "社交动态",
    "product_update": "行业资讯",
    "industry_news": "行业资讯",
    "security_incident": "行业资讯",
    "regulation": "行业资讯",
    "tutorial": "工程实践",
    "opinion": "观点洞察",
    "aggregation": "资讯聚合",
    "other": "其它",
}

# 公共日报分类(``extensions.items[].classification``)。**这是对外契约**:内网神灯流水线
# 把它原样写进 classification 与 type 两个字段(scripts/export_shendeng_daily_news.py)。
# 因此它与个人早报板块不一致(教程 / 观点在这里归「行业资讯」)是有意保留的:改字符串属于
# 对外变更,需要先与神灯侧确认。
DAILY_BRIEF_GENRE_CLASSIFICATIONS: dict[str, str] = {
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

# ── 判定规则 ──────────────────────────────────────────────────────────────────
# 数据结构的默认类型:对没有来源先验的源兜底(配置源、历史 / 导入源,以及未设 default_kind 的注册源;
# 有来源先验的源一律以 default_kind 为准)。
CONTENT_TYPE_DEFAULT_KINDS: dict[str, str] = {
    "github_release": "release",
    "github_repository": "project",
    "github_trending": "project",
    "hf_model": "project",
    "huggingface_model": "project",
    "arxiv": "paper",
    "daily_brief": "digest",
}
# 不经抓取器注册表的特殊源。
SPECIAL_SOURCE_KINDS: dict[str, str] = {
    "dorami_daily_brief": "digest",
}
# 链接形状判定论文原文:锚定在 URL 开头的「协议 + 主机 + 路径」前缀,查询串 / 片段里引用的
# 论文地址、相似域名(notarxiv.org)都不算。社交帖 / 播客 / 日报的 source_url 指向自身,不套用。
_PAPER_URL_HOST_PATHS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("arxiv.org", "www.arxiv.org", "export.arxiv.org"), ("/abs/", "/pdf/")),
    (("openreview.net", "www.openreview.net"), ("/forum", "/pdf")),
    (("huggingface.co", "www.huggingface.co", "hf.co"), ("/papers/",)),
)
PAPER_URL_PREFIXES: tuple[str, ...] = tuple(
    f"{scheme}://{host}{path}"
    for hosts, paths in _PAPER_URL_HOST_PATHS
    for host in hosts
    for path in paths
    for scheme in ("http", "https")
)
# SQLite LIKE 只对 ASCII 字母不区分大小写;Python 侧用同样的「仅 ASCII 小写化」,两边逐字等价。
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
URL_RULE_EXCLUDED_CONTENT_TYPES: frozenset[str] = frozenset({
    "social_post", "podcast_episode", "daily_brief",
})

KIND_BASIS_SOURCE = "source"
KIND_BASIS_CONTENT_TYPE = "content_type"
KIND_BASIS_URL = "url"
KIND_BASIS_LLM = "llm"
KIND_BASIS_GENRE = "genre"
KIND_BASIS_UNKNOWN = "unknown"

# ── 双评分与主分(issue #178 第二波)──────────────────────────────────────────────
# 新闻价值 = 这件事有多重要;内容价值 = 值不值得花时间读完 / 听完。每条最多两个分,缺一个就只显示另一个。
SCORE_DIMENSION_LABELS: dict[str, str] = {"news": "新闻价值", "content": "内容价值"}
# 这几类内容默认以内容价值为主分;资讯 / 版本更新 / 项目以新闻价值为主。
CONTENT_PRIMARY_KINDS: frozenset[str] = frozenset({"paper", "tutorial", "opinion", "digest"})
# 新闻价值达到这条线时一律以新闻价值为主(重大事件优先)。与重大事件通道的 9.0 分开设置;先做常量。
PRIMARY_NEWS_OVERRIDE = 8.5


@dataclass(frozen=True)
class ItemClassification:
    """一条内容的推导结果。``kind`` 为 None 表示尚无依据(通常是分析还没完成)。"""

    kind: Optional[str]
    kind_basis: str
    event_type: Optional[str]

    @property
    def kind_label(self) -> str:
        return ITEM_KIND_LABELS.get(self.kind or "", "")

    @property
    def event_label(self) -> str:
        return EVENT_TYPE_LABELS.get(self.event_type or "", "")

    def as_projection(self) -> dict[str, Optional[str]]:
        """序列化给读者面 / 管理面的字段(列表、详情、收藏共用)。"""
        return {
            "kind": self.kind,
            "kind_label": self.kind_label or None,
            "kind_basis": self.kind_basis,
            "event_type": self.event_type,
            "event_label": self.event_label or None,
        }


def item_kind_label(kind: Optional[str]) -> str:
    return ITEM_KIND_LABELS.get(kind or "", "")


def event_type_label(event_type: Optional[str]) -> str:
    return EVENT_TYPE_LABELS.get(event_type or "", "")


def genre_label(genre: Optional[str]) -> str:
    return GENRE_LABELS.get(genre or "", "")


def content_type_label(content_type: Optional[str]) -> str:
    value = content_type or ""
    return CONTENT_TYPE_LABELS.get(value, value)


def personal_digest_section_for_genre(genre: Optional[str]) -> str:
    return PERSONAL_DIGEST_GENRE_SECTIONS.get(genre or "other", PERSONAL_DIGEST_GENRE_SECTIONS["other"])


def daily_brief_classification_for_genre(genre: Optional[str]) -> str:
    """体裁 → 公共日报分类(神灯契约);未知 / 空值返回空串,渲染层回落 content_type 映射。"""
    return DAILY_BRIEF_GENRE_CLASSIFICATIONS.get((genre or "").strip(), "")


def source_default_kinds() -> dict[str, str]:
    """source_id → 来源先验内容类型(抓取器 ``default_kind`` + 特殊源)。

    只读注册表类属性;注册表在导入期扫描完毕后不再变化。SourceConfig 源(播客、自定 RSS、
    配置化网页 / X 源)没有来源先验,由数据结构、链接形状与体裁判定。
    """
    from fetchers.registry import fetcher_registry

    result: dict[str, str] = {}
    for source_id, fetcher_class in fetcher_registry._fetchers.items():
        kind = str(getattr(fetcher_class, "default_kind", "") or "").strip().lower()
        if kind in ITEM_KIND_LABELS:
            result[source_id] = kind
    result.update(SPECIAL_SOURCE_KINDS)
    return result


def source_kind_prior(
    source_id: Optional[str],
    content_type: Optional[str],
    source_kinds: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """一个源的默认内容类型(发现页源卡、源目录用):来源先验,否则数据结构兜底,否则无。"""
    kinds = source_kinds if source_kinds is not None else source_default_kinds()
    return kinds.get(source_id or "") or CONTENT_TYPE_DEFAULT_KINDS.get(content_type or "") or None


def is_paper_url(source_url: Optional[str]) -> bool:
    return (source_url or "").translate(_ASCII_LOWER).startswith(PAPER_URL_PREFIXES)


def classify_item(
    *,
    source_id: Optional[str],
    source_url: Optional[str],
    content_type: Optional[str],
    content_genre: Optional[str],
    source_kinds: Optional[Mapping[str, str]] = None,
    content_kind: Optional[str] = None,
) -> ItemClassification:
    """推导一条内容的内容类型与事件类型。

    判定序:来源先验 → 数据结构(仅没有来源先验的源)→ 链接形状(论文原文)→ 模型给的类型
    (``content_kind``,第二波起分析产出)→ 体裁 → 未知。规则能判的不看模型。
    ``source_kinds`` 可由调用方批量预取后传入,避免逐条读注册表。
    """
    kinds = source_kinds if source_kinds is not None else source_default_kinds()
    sid = source_id or ""
    ctype = content_type or ""
    genre = (content_genre or "").strip()
    genre_kind, genre_event = GENRE_KIND_EVENT.get(genre, (None, None))
    llm_kind = (content_kind or "").strip()

    if sid in kinds:
        kind, basis = kinds[sid], KIND_BASIS_SOURCE
    elif ctype in CONTENT_TYPE_DEFAULT_KINDS:
        kind, basis = CONTENT_TYPE_DEFAULT_KINDS[ctype], KIND_BASIS_CONTENT_TYPE
    elif ctype not in URL_RULE_EXCLUDED_CONTENT_TYPES and is_paper_url(source_url):
        kind, basis = "paper", KIND_BASIS_URL
    elif llm_kind in ITEM_KIND_LABELS:
        kind, basis = llm_kind, KIND_BASIS_LLM
    elif genre_kind is not None:
        kind, basis = genre_kind, KIND_BASIS_GENRE
    else:
        kind, basis = None, KIND_BASIS_UNKNOWN

    event_type = genre_event if kind in EVENT_BEARING_KINDS else None
    return ItemClassification(kind=kind, kind_basis=basis, event_type=event_type)


RULE_KIND_BASES: frozenset[str] = frozenset({KIND_BASIS_SOURCE, KIND_BASIS_CONTENT_TYPE, KIND_BASIS_URL})


def rule_kind(
    *,
    source_id: Optional[str],
    source_url: Optional[str],
    content_type: Optional[str],
    source_kinds: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """只凭规则(来源先验 / 数据结构 / 链接形状)能判出的内容类型;判不了返回 None。

    分析输入的 ``kind_hint``(第二波)用它:规则能判时喂给模型并以规则为准。
    """
    result = classify_item(
        source_id=source_id,
        source_url=source_url,
        content_type=content_type,
        content_genre=None,
        source_kinds=source_kinds,
    )
    return result.kind if result.kind_basis in RULE_KIND_BASES else None


def parse_kind_filter(value: Optional[str]) -> list[str]:
    """``?kind=paper,tutorial`` → 类型列表(保持词表顺序、去重);含任何未知值即抛 ``ValueError``。"""
    requested = {part.strip().lower() for part in str(value or "").split(",") if part.strip()}
    unknown = sorted(requested - set(ITEM_KINDS))
    if unknown:
        raise ValueError(f"kind 取值无效: {', '.join(unknown)}")
    return [kind for kind in ITEM_KINDS if kind in requested]


def kind_expr(source_kinds: Optional[Mapping[str, str]] = None):
    """``classify_item`` 的 SQL 镜像:按同一判定序给出条目内容类型的 CASE 表达式(无依据时为 NULL)。

    WHEN 的先后就是判定序(来源先验 → 数据结构 → 链接形状 → 模型给的类型 → 体裁),与 Python 推导共用
    同一组规则表;``tests/test_content_classification.py`` 用由规则表生成的穷举组合逐条对拍两者。
    模型类型与体裁取自 ``article_analyses`` 的 ``content_kind`` / ``content_genre``(一篇一行,与列表
    序列化读取的是同一行,不额外看分析状态)。
    筛选用 ``kind_condition``,分面按它 GROUP BY 一次聚合(codex R1 #8)。
    """
    from sqlalchemy import and_, case, func, or_, select

    from models.db import ArticleAnalysisRecord, ArticleRecord

    rules = dict(source_kinds if source_kinds is not None else source_default_kinds())
    whens = []
    for kind in ITEM_KINDS:
        sources = sorted(sid for sid, value in rules.items() if value == kind)
        if sources:
            whens.append((ArticleRecord.source_id.in_(sources), kind))
    for kind in ITEM_KINDS:
        types = sorted(ctype for ctype, value in CONTENT_TYPE_DEFAULT_KINDS.items() if value == kind)
        if types:
            whens.append((ArticleRecord.content_type.in_(types), kind))
    url_value = func.coalesce(ArticleRecord.source_url, "")
    whens.append((
        and_(
            ArticleRecord.content_type.notin_(sorted(URL_RULE_EXCLUDED_CONTENT_TYPES)),
            or_(*[url_value.like(f"{prefix}%") for prefix in PAPER_URL_PREFIXES]),
        ),
        "paper",
    ))
    llm_kind = (
        select(ArticleAnalysisRecord.content_kind)
        .where(ArticleAnalysisRecord.article_id == ArticleRecord.id)
        .scalar_subquery()
    )
    whens.append((llm_kind.in_(list(ITEM_KINDS)), llm_kind))
    genre = (
        select(ArticleAnalysisRecord.content_genre)
        .where(ArticleAnalysisRecord.article_id == ArticleRecord.id)
        .scalar_subquery()
    )
    for kind in ITEM_KINDS:
        genres = sorted(g for g, (value, _event) in GENRE_KIND_EVENT.items() if value == kind)
        if genres:
            whens.append((genre.in_(genres), kind))
    return case(*whens, else_=None)


def kind_condition(kinds: Iterable[str], source_kinds: Optional[Mapping[str, str]] = None):
    """条目的内容类型属于 ``kinds`` 之一(``kind_expr`` 的 IN;空集合恒假)。"""
    from sqlalchemy import false

    wanted = [kind for kind in ITEM_KINDS if kind in set(kinds)]
    if not wanted:
        return false()
    return kind_expr(source_kinds).in_(wanted)


def primary_dimension(
    kind: Optional[str],
    news_value: Optional[float],
    content_value: Optional[float],
) -> Optional[str]:
    """读者面每条显示哪个分作主分:``"news"`` / ``"content"``,两个都没有时 None。

    只有一个分时显示它;新闻价值 ≥ ``PRIMARY_NEWS_OVERRIDE`` 时一律以新闻价值为主;否则论文 / 教程 /
    观点 / 汇编以内容价值为主,其余(含未判定)以新闻价值为主。
    """
    has_news, has_content = news_value is not None, content_value is not None
    if not has_news and not has_content:
        return None
    if has_news != has_content:
        return "news" if has_news else "content"
    if float(news_value) >= PRIMARY_NEWS_OVERRIDE:
        return "news"
    return "content" if kind in CONTENT_PRIMARY_KINDS else "news"


def current_content_value(analysis: Any) -> tuple[Optional[float], str]:
    """一行分析当前有效的内容价值与依据;尺子版本不是当前版本即视为没有(缺失即缺席)。"""
    if analysis is None:
        return None, ""
    value = getattr(analysis, "content_value", None)
    if value is None or getattr(analysis, "content_value_version", "") != CONTENT_VALUE_SCORING_VERSION:
        return None, ""
    return float(value), str(getattr(analysis, "content_value_reason", "") or "").strip()


def score_projection(*, content_type: Optional[str], kind: Optional[str], analysis: Any) -> dict[str, Any]:
    """双评分的读者面投影(列表、详情、收藏共用)。

    播客(issue #178 第二波拍板甲):现有分数用的是播客尺子(值不值得听),投影为**内容价值**,播客暂无
    新闻价值;播客提示词拆成两个分随 #97 播客黄金集放第三波。``quality_score`` / ``score_reason`` 原样
    保留给旧调用方,这里只新增字段。
    """
    quality = getattr(analysis, "quality_score", None) if analysis is not None else None
    reason = str(getattr(analysis, "score_reason", "") or "").strip() if analysis is not None else ""
    if (content_type or "").strip() == "podcast_episode":
        news_value, news_reason = None, ""
        content_value, content_reason = (float(quality), reason) if quality is not None else (None, "")
    else:
        news_value, news_reason = (float(quality), reason) if quality is not None else (None, "")
        content_value, content_reason = current_content_value(analysis)
    dimension = primary_dimension(kind, news_value, content_value)
    primary_score = {"news": news_value, "content": content_value}.get(dimension or "")
    return {
        "news_value": news_value,
        "news_value_reason": news_reason or None,
        "content_value": content_value,
        "content_value_reason": content_reason or None,
        "primary_dimension": dimension,
        "primary_score": primary_score,
    }
