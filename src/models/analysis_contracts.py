"""Shared contracts for article analysis, taxonomy, and personal digests.

The persisted tables deliberately store enum values as strings so SQLite remains
easy to inspect and migrate.  These enums and DTOs are the service-layer source of
truth used by WP-1/WP-2/WP-3; database CHECK constraints mirror their values.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StringEnum(str, Enum):
    """A JSON-friendly enum whose values are also valid database strings."""


class AnalysisStatus(StringEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


class TaggingStatus(StringEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class AnalysisOperation(StringEnum):
    FULL_ANALYSIS = "full_analysis"
    RETAG_ONLY = "retag_only"


class AnalysisAttemptStatus(StringEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


class TagKind(StringEnum):
    TOPIC = "topic"
    INDUSTRY = "industry"
    ENTITY = "entity"


class ContentGenre(StringEnum):
    MODEL_RELEASE = "model_release"
    PRODUCT_UPDATE = "product_update"
    OPEN_SOURCE_UPDATE = "open_source_update"
    RESEARCH_PAPER = "research_paper"
    TUTORIAL = "tutorial"
    OPINION = "opinion"
    INDUSTRY_NEWS = "industry_news"
    CONFERENCE = "conference"
    SOCIAL_DISCUSSION = "social_discussion"
    AGGREGATION = "aggregation"
    SECURITY_INCIDENT = "security_incident"
    REGULATION = "regulation"
    OTHER = "other"


class TagStatus(StringEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    MERGED = "merged"


class TagActivationMode(StringEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class TagAliasType(StringEnum):
    SYNONYM = "synonym"
    ABBREVIATION = "abbreviation"
    FORMER_NAME = "former_name"
    TRANSLATION = "translation"
    MISSPELLING = "misspelling"


class TagAssignmentSource(StringEnum):
    LLM = "llm"
    MANUAL = "manual"
    RULE = "rule"
    MIGRATION = "migration"


class TagCandidateStatus(StringEnum):
    CANDIDATE = "candidate"
    REVIEWING = "reviewing"
    ACTIVATED = "activated"
    MERGED = "merged"
    REJECTED = "rejected"


class TagEventAction(StringEnum):
    ACTIVATE = "activate"
    RENAME = "rename"
    MERGE = "merge"
    DEPRECATE = "deprecate"
    REJECT = "reject"
    CHANGE_FLAGS = "change_flags"
    DELETE_CANDIDATE = "delete_candidate"


class TaxonomyVersionStatus(StringEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class RetagJobStatus(StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InterestStance(StringEnum):
    """v3.56(issue #27)起只剩 follow:屏蔽一极整体退役,枚举保留供快照/契约字段取值。"""

    FOLLOW = "follow"


class InterestPriority(StringEnum):
    NORMAL = "normal"
    HIGH = "high"


class PersonalDigestStatus(StringEnum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class SelectionLane(StringEnum):
    INTEREST = "interest"
    QUALITY = "quality"
    # v3.50(issue #33 §2):「重大事件」通道——不看订阅范围的头条位,机械准入
    # (官方一手 ≥ 阈值,或多源印证),额外加在 target 之上、不进兴趣配额分母。
    BREAKING = "breaking"
    # issue #178 第三波:按内容价值的两个额外分区——「深度阅读」(论文 / 教程 / 观点 / 汇编)与
    # 「播客精选」(播客),接在新闻分区之后,不占新闻的 target 名额。
    READ = "read"
    LISTEN = "listen"


class DigestGenerationReason(StringEnum):
    SCHEDULED = "scheduled"
    FIRST_OPEN = "first_open"
    INTEREST_CHANGED = "interest_changed"
    SUBSCRIPTION_CHANGED = "subscription_changed"
    MANUAL_REBUILD = "manual_rebuild"
    DAILY_BRIEF_READY = "daily_brief_ready"
    RECOVERY = "recovery"


class SourceReadinessStatus(StringEnum):
    SUCCEEDED_WITH_ITEMS = "succeeded_with_items"
    SUCCEEDED_NO_UPDATE = "succeeded_no_update"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    NOT_DUE = "not_due"
    MISSED = "missed"


ANALYSIS_LEASE_SECONDS = 5 * 60
PERSONAL_DIGEST_TARGET_ITEMS = 10
PERSONAL_DIGEST_INTEREST_MAX_RATIO = 0.5
# 新闻价值尺子下(v3.48)博客/教程/观点落 4～6 分,7.0 会让博客订阅者的早报常态为空;
# 5.0 只挡边角与营销,兴趣/质量两通道同门槛。
PERSONAL_DIGEST_MIN_QUALITY_SCORE = 5.0
# v3.54(issue #33 §3 前置,「订阅 ∪ 兴趣」):兴趣半的候选池不再被订阅面截断——全站可见源里命中
# 兴趣标签的文章也能进兴趣半;订阅外没有「你明说信任」这层背书,门槛用公共日报的入选线 6.0,
# 且每源每期硬上限 2 条(HF Daily Papers 一类高产源一天几十篇,不设上限兴趣半会被一家占满)。
# 订阅内仍 5.0、每源软上限(不足时逐级放宽)。两枚都是管理面 KV 旋钮(见 personal_digest)。
PERSONAL_DIGEST_EXTERNAL_MIN_QUALITY_SCORE = 6.0
PERSONAL_DIGEST_EXTERNAL_PER_SOURCE_MAX = 2
PERSONAL_DIGEST_EXTERNAL_PER_SOURCE_MAX_LIMIT = 5
PERSONAL_DIGEST_WINDOW_HOURS = 36
PERSONAL_DIGEST_FALLBACK_WINDOW_HOURS = 72
PERSONAL_DIGEST_LATEST_FALLBACK_LIMIT = 5
# 「重大事件」通道(v3.50,issue #33 §2)。生产 7 天实证(699 篇,v6b 单分):≥9.0 官方
# 9 条只对应 2 个事件;Fable 5.1 的官博不在库、只以媒体形态 9.2 存在,故准入是
# 「官方一手 ≥ T」或「≥2 个不同来源 ≥ T−0.5 且至少一条 ≥ T」双路;24h 窗不回退;
# 同实体前 2 期上过头条即抑制(GPT-6 三天连续 ≥9.5 官方稿的实证)。
PERSONAL_DIGEST_BREAKING_MIN_SCORE = 9.0
PERSONAL_DIGEST_BREAKING_MAX_ITEMS = 2
PERSONAL_DIGEST_BREAKING_MAX_ITEMS_LIMIT = 5
PERSONAL_DIGEST_BREAKING_WINDOW_HOURS = 24
PERSONAL_DIGEST_BREAKING_CORROBORATION_SOURCES = 2
PERSONAL_DIGEST_BREAKING_CORROBORATION_SLACK = 0.5
PERSONAL_DIGEST_BREAKING_SUPPRESS_EDITIONS = 2
# 「深度阅读」「播客精选」(issue #178 第三波,方案 §5.1):按内容价值入选,额外于新闻的 target 之上。
# 门槛按黄金集分布暂定(扎实的论文 / 教程 / 一手观点多在 6.5–8 分),上线后按生产分布校准;
# 7 天窗口——深度内容不像新闻那样过期,36 小时常凑不出;每源 1 篇,防一个高产源占满;
# 读者近 7 期早报出现过的不再出现。门槛、名额、窗口是管理面 KV 旋钮(见 personal_digest)。
PERSONAL_DIGEST_READ_MIN_VALUE = 7.0
PERSONAL_DIGEST_READ_MAX_ITEMS = 3
PERSONAL_DIGEST_LISTEN_MIN_VALUE = 7.5
PERSONAL_DIGEST_LISTEN_MAX_ITEMS = 2
PERSONAL_DIGEST_VALUE_MAX_ITEMS_LIMIT = 5
PERSONAL_DIGEST_VALUE_WINDOW_DAYS = 7
PERSONAL_DIGEST_VALUE_WINDOW_DAYS_LIMIT = 14
PERSONAL_DIGEST_VALUE_PER_SOURCE_MAX = 1
PERSONAL_DIGEST_VALUE_SUPPRESS_EDITIONS = 7


class ContractModel(BaseModel):
    """Strict, immutable base for values crossing service boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class TaxonomyTagDTO(ContractModel):
    id: int
    code: str = Field(min_length=1)
    kind: TagKind
    name_zh: str = ""
    name_en: str = ""
    prompt_description: str = ""
    status: TagStatus = TagStatus.ACTIVE
    user_selectable: bool = False


class ArticleTagAssignmentDTO(ContractModel):
    code: str = Field(min_length=1)
    kind: TagKind
    relevance: float = Field(ge=0.0, le=1.0)
    is_primary: bool = False


class ArticleTagCandidateDTO(ContractModel):
    label: str = Field(min_length=1)
    proposed_kind: TagKind
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""


class ArticleAnalysisResultDTO(ContractModel):
    quality_score: float = Field(ge=1.0, le=10.0)
    score_reason: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    content_genre: ContentGenre
    primary_tag_code: Optional[str] = None
    tag_assignments: tuple[ArticleTagAssignmentDTO, ...] = Field(default_factory=tuple)
    tag_candidates: tuple[ArticleTagCandidateDTO, ...] = Field(default_factory=tuple)
    content_features: tuple[str, ...] = Field(default_factory=tuple)
    entities: tuple[dict[str, object], ...] = Field(default_factory=tuple)
    # 内容价值(issue #178 第二波):文章提示词 v7 起产出;播客与旧版结果为空。
    content_kind: Optional[str] = None
    content_value: Optional[float] = Field(default=None, ge=1.0, le=10.0)
    content_value_reason: str = ""


class DigestArticleCandidateDTO(ContractModel):
    """User-independent analyzed article input consumed by digest selection."""

    article_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_url: str = ""
    publish_date: str
    fetched_date: str
    quality_score: float = Field(ge=1.0, le=10.0)
    score_reason: str
    content_genre: ContentGenre
    tag_codes: tuple[str, ...] = Field(default_factory=tuple)
    primary_tag_code: Optional[str] = None
    # issue #121 事件层:所属事件 ID(event_members.event_id);None = 未分配,去重回落 URL / 标题键。
    event_id: Optional[int] = None
    # v3.50 重大事件通道用:来源角色(source_naming.source_role)与内容形态;
    # 订阅域选篇不读这两项,默认值保持既有 DTO 构造不变。
    source_role: str = "media"
    content_shape: str = "article"
    # v3.54「订阅 ∪ 兴趣」:是否在读者的订阅面内。订阅外候选只能走兴趣通道、门槛与每源上限
    # 另算;默认 True 保持既有构造与订阅域语义不变。
    subscribed: bool = True
    # 兴趣命中判据用的标签码(主标签或相关度过线的指派,与阅读器兴趣透镜同一尺);None = 回退到
    # tag_codes 全集(旧调用方/纯策略测试)。
    interest_tag_codes: Optional[tuple[str, ...]] = None
    # issue #178 第三波:条目的内容类型(content_classification.classify_item 推导)与当前有效的
    # 内容价值(文章只认当前尺子版本;播客 = 现有播客分的投影)。「深度阅读」「播客精选」两个分区据此入选,
    # 新闻通道不读这两项。
    content_kind: Optional[str] = None
    content_value: Optional[float] = Field(default=None, ge=1.0, le=10.0)


class UserInterestDTO(ContractModel):
    tag_code: str = Field(min_length=1)
    stance: InterestStance = InterestStance.FOLLOW
    priority: InterestPriority = InterestPriority.NORMAL


class DigestSelectionDTO(ContractModel):
    article_id: str = Field(min_length=1)
    lane: SelectionLane
    matched_interest_codes: tuple[str, ...] = Field(default_factory=tuple)
    selection_reason: str
    coverage_adjustments: tuple[str, ...] = Field(default_factory=tuple)
    # 仅 lane=breaking 填写:准入依据 official|corroborated、同事件来源数、
    # 事件实体码(跨天抑制的对照物,随条目 ranking_features 落库)。
    breaking_basis: Optional[str] = None
    breaking_source_count: int = 0
    event_entity_codes: tuple[str, ...] = Field(default_factory=tuple)
