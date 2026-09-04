from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import CheckConstraint, Column, Index, String, UniqueConstraint, text


class ArticleRecord(SQLModel, table=True):
    """关系型数据库表结构：用于 CMS 后端管理系统"""
    __tablename__ = "articles"
    # fetched_date 是读者 feed 主排序、媒体热点图、未读水位的高频过滤/排序键，
    # 除单列索引外再建 (source_id, fetched_date) 复合索引——单源按时间倒序取条目
    # （阅读器源栏、feed 交付、热点图逐源统计）走复合索引即可命中，免二次排序。
    __table_args__ = (
        Index("ix_articles_source_id_fetched_date", "source_id", "fetched_date"),
    )

    id: str = Field(primary_key=True, description="唯一序号")
    title: str = Field(index=True, description="文章标题")

    # 【架构重构】: 将原先模糊的 source_type 拆分为双维度的 content_type 和 source_id
    content_type: str = Field(index=True, description="数据结构类别 (如 arxiv, tech_conference)")
    source_id: str = Field(index=True, description="数据来源渠道标识 (如 huggingface_daily)")

    source_url: str
    publish_date: str = Field(index=True, description="发布日期")
    fetched_date: str = Field(index=True, description="抓取入库的系统时间")
    archive_updated_at: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String,
            nullable=False,
            server_default=text("''"),
            index=True,
            comment="忠实归档记录最近变更时间，仅用于 Archive Sync 增量游标",
        ),
    )
    fetch_run_id: Optional[int] = Field(default=None, index=True, description="首次入库关联的节点级运行 ID")
    job_id: Optional[int] = Field(default=None, index=True, description="首次入库关联的采集任务 ID")
    job_run_id: Optional[int] = Field(default=None, index=True, description="首次入库关联的采集任务级运行 ID")
    source_group_id: Optional[int] = Field(default=None, index=True, description="首次入库关联的采集范围 ID")
    run_scope: str = Field(default="ad_hoc", index=True, description="首次入库运行归属: ad_hoc/saved_job/legacy_task")

    has_content: bool = Field(default=True)
    content: Optional[str] = Field(default=None, description="文章正文或长摘要")
    extensions_json: Optional[str] = Field(default="{}", description="扩展元数据 (JSON 字符串)")

    # 全站累计阅读次数（跨读者）：由 POST /api/reader/articles/{id}/read 随逐用户
    # 计量一并 +1。与 ReaderReadRecord（日×用户×来源聚合，运维口径）互补——
    # 本列是文章粒度的轻量计数器，供阅读窗标题下直接展示，免去逐请求聚合。
    read_count: int = Field(default=0, description="全站累计阅读次数")


class CmsTagRecord(SQLModel, table=True):
    """受控 taxonomy 中稳定、不可复用的规范概念。"""
    __tablename__ = "cms_tags"
    __table_args__ = (
        UniqueConstraint("code", name="uq_cms_tags_code"),
        UniqueConstraint("kind", "normalized_name", name="uq_cms_tags_kind_normalized_name"),
        Index("ix_cms_tags_kind_status_user_selectable", "kind", "status", "user_selectable"),
        Index(
            "uq_cms_tags_entity_external_key",
            "external_key",
            unique=True,
            sqlite_where=text("kind = 'entity' AND external_key IS NOT NULL"),
        ),
        CheckConstraint("kind IN ('topic','industry','entity')", name="ck_cms_tags_kind"),
        CheckConstraint(
            "status IN ('draft','active','deprecated','merged')",
            name="ck_cms_tags_status",
        ),
        CheckConstraint(
            "activation_mode IN ('manual','automatic')",
            name="ck_cms_tags_activation_mode",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(description="稳定规范代码，创建后不可复用")
    kind: str = Field(description="topic/industry/entity")
    name_zh: str = Field(default="", description="中文展示名")
    name_en: str = Field(default="", description="英文展示名")
    normalized_name: str = Field(description="同分面内用于精确匹配的归一名称")
    description: str = Field(default="", description="面向 CMS 的概念说明")
    prompt_description: str = Field(default="", description="提供给打标 Prompt 的边界说明")
    status: str = Field(default="draft", description="draft/active/deprecated/merged")
    replacement_id: Optional[int] = Field(
        default=None,
        foreign_key="cms_tags.id",
        ondelete="SET NULL",
        description="合并或废弃后的规范替代项",
    )
    parent_id: Optional[int] = Field(
        default=None,
        foreign_key="cms_tags.id",
        ondelete="SET NULL",
        description="可选简单父级",
    )
    entity_type: str = Field(default="", description="仅 entity 使用的实体类型")
    external_key: Optional[str] = Field(default=None, description="实体稳定外部标识")
    user_selectable: bool = Field(default=False, description="是否进入用户兴趣目录")
    filterable: bool = Field(default=True, description="是否允许结构化筛选")
    recommendable: bool = Field(default=True, description="是否允许推荐策略消费")
    activation_mode: str = Field(default="manual", description="manual/automatic")
    taxonomy_version: int = Field(default=0, description="最近变更所属 taxonomy 版本快照")
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


class TaxonomyVersionRecord(SQLModel, table=True):
    """全局 taxonomy 单调版本；当前版本不能从标签行反推。"""
    __tablename__ = "taxonomy_versions"
    __table_args__ = (
        Index(
            "uq_taxonomy_versions_one_active",
            "status",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
        CheckConstraint("status IN ('draft','active','retired')", name="ck_taxonomy_versions_status"),
    )

    version: int = Field(primary_key=True, description="单调递增版本号")
    status: str = Field(default="draft", description="draft/active/retired")
    change_summary: str = Field(default="", description="版本变更摘要")
    activated_by: Optional[str] = Field(default=None, description="激活操作者")
    activated_at: Optional[str] = Field(default=None, description="激活时间")
    created_at: str = Field(description="创建时间")


class ArticleAnalysisRecord(SQLModel, table=True):
    """每篇文章当前权威的用户无关分析结果与可恢复任务状态。"""
    __tablename__ = "article_analyses"
    __table_args__ = (
        Index(
            "ix_article_analyses_scan",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
        Index("ix_article_analyses_content_hash", "content_hash"),
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','skipped','timeout')",
            name="ck_article_analyses_status",
        ),
        CheckConstraint(
            "tagging_status IN ('pending','succeeded','partial','failed')",
            name="ck_article_analyses_tagging_status",
        ),
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 1.0 AND quality_score <= 10.0)",
            name="ck_article_analyses_quality_score",
        ),
        CheckConstraint(
            "content_genre IS NULL OR content_genre IN ("
            "'model_release','product_update','open_source_update','research_paper',"
            "'tutorial','opinion','industry_news','conference','social_discussion',"
            "'aggregation','security_incident','regulation','other')",
            name="ck_article_analyses_content_genre",
        ),
    )

    article_id: str = Field(
        primary_key=True,
        foreign_key="articles.id",
        ondelete="CASCADE",
        description="文章主键；一篇文章一份当前权威分析",
    )
    status: str = Field(default="pending", description="基础评分与摘要状态")
    tagging_status: str = Field(default="pending", description="正式标签关联的独立状态")
    quality_score: Optional[float] = Field(default=None, ge=1.0, le=10.0)
    dimension_scores_json: str = Field(default="{}", description="内部版本化评分维度，不对外展示")
    score_reason: str = Field(default="", description="一句话评分理由（≤40 字，分数注脚，不复述内容）")
    summary: str = Field(default="", description="文章唯一摘要：速读卡 / 个人早报 / 列表 summary_zh 投影共用")
    content_genre: Optional[str] = Field(default=None, description="独立受控内容性质")
    content_features_json: str = Field(default="[]", description="附加内容特征 JSON")
    entities_json: str = Field(default="[]", description="LLM 原始实体与未归一候选 JSON")
    display_tags_json: str = Field(
        default="[]",
        description="文章级自由展示标签快照；不参与兴趣、筛选或规范标签关系",
    )
    primary_tag_id: Optional[int] = Field(
        default=None,
        foreign_key="cms_tags.id",
        ondelete="SET NULL",
        description="列表查询缓存；真实关系以 assignment 为准",
    )
    content_hash: str = Field(default="", description="参与分析的标题与正文哈希")
    model_name: str = Field(default="")
    prompt_version: str = Field(default="")
    scoring_version: str = Field(default="")
    taxonomy_version: int = Field(default=0)
    attempt_count: int = Field(default=0, ge=0)
    started_at: Optional[str] = Field(default=None)
    next_attempt_at: Optional[str] = Field(default=None)
    lease_owner: Optional[str] = Field(default=None)
    lease_expires_at: Optional[str] = Field(default=None)
    last_error: Optional[str] = Field(default=None, description="截断且脱敏的错误摘要")
    analyzed_at: Optional[str] = Field(default=None)
    tagged_at: Optional[str] = Field(default=None)
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


class ArticleAnalysisAttemptRecord(SQLModel, table=True):
    """文章分析/重标的逐次调用记录，只保存脱敏结果摘要。"""
    __tablename__ = "article_analysis_attempts"
    __table_args__ = (
        UniqueConstraint("article_id", "attempt_no", name="uq_article_analysis_attempt_number"),
        Index("ix_article_analysis_attempts_article_created", "article_id", "created_at"),
        CheckConstraint(
            "operation IN ('full_analysis','retag_only')",
            name="ck_article_analysis_attempts_operation",
        ),
        CheckConstraint(
            "status IN ('running','succeeded','failed','skipped','timeout')",
            name="ck_article_analysis_attempts_status",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    article_id: str = Field(foreign_key="articles.id", ondelete="CASCADE")
    attempt_no: int = Field(ge=1)
    operation: str = Field(default="full_analysis", description="full_analysis/retag_only")
    status: str = Field(default="running")
    content_hash: str = Field(default="")
    model_name: str = Field(default="")
    prompt_version: str = Field(default="")
    scoring_version: str = Field(default="")
    taxonomy_version: int = Field(default=0)
    started_at: str
    ended_at: Optional[str] = Field(default=None)
    duration_ms: Optional[int] = Field(default=None, ge=0)
    error: Optional[str] = Field(default=None, description="截断且脱敏的错误摘要")
    result_summary_json: str = Field(default="{}", description="不含私有正文和敏感 URL")
    created_at: str


class CmsTagAliasRecord(SQLModel, table=True):
    """规范概念 Alias；归一后在同一分面内全局唯一。"""
    __tablename__ = "cms_tag_aliases"
    __table_args__ = (
        UniqueConstraint("kind", "normalized_alias", name="uq_cms_tag_aliases_kind_normalized"),
        Index("ix_cms_tag_aliases_tag_id", "tag_id"),
        CheckConstraint("kind IN ('topic','industry','entity')", name="ck_cms_tag_aliases_kind"),
        CheckConstraint(
            "alias_type IN ('synonym','abbreviation','former_name','translation','misspelling')",
            name="ck_cms_tag_aliases_type",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tag_id: int = Field(foreign_key="cms_tags.id", ondelete="CASCADE")
    kind: str = Field(description="冗余分面，用于同分面 Alias 唯一约束")
    locale: str = Field(default="")
    alias: str
    normalized_alias: str
    alias_type: str = Field(default="synonym")
    created_at: str
    updated_at: str


class ArticleTagAssignmentRecord(SQLModel, table=True):
    """文章与 active 规范概念的关系及标注来源。"""
    __tablename__ = "article_tag_assignments"
    __table_args__ = (
        UniqueConstraint("article_id", "tag_id", name="uq_article_tag_assignments_article_tag"),
        Index("ix_article_tag_assignments_tag_article", "tag_id", "article_id"),
        Index(
            "uq_article_tag_assignments_primary_facet",
            "article_id",
            "tag_kind",
            unique=True,
            sqlite_where=text("is_primary = 1"),
        ),
        CheckConstraint("tag_kind IN ('topic','industry','entity')", name="ck_article_tag_assignments_kind"),
        CheckConstraint(
            "assignment_source IN ('llm','manual','rule','migration')",
            name="ck_article_tag_assignments_source",
        ),
        CheckConstraint("relevance >= 0.0 AND relevance <= 1.0", name="ck_article_tag_assignments_relevance"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    article_id: str = Field(foreign_key="articles.id", ondelete="CASCADE")
    tag_id: int = Field(foreign_key="cms_tags.id", ondelete="CASCADE")
    tag_kind: str = Field(description="分面快照；服务层写入时必须与 cms_tags.kind 一致")
    is_primary: bool = Field(default=False)
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    assignment_source: str = Field(default="llm")
    prompt_version: str = Field(default="")
    taxonomy_version: int = Field(default=0)
    created_at: str
    updated_at: str


class CmsTagCandidateRecord(SQLModel, table=True):
    """未知词候选的幂等聚合行；计数必须从 evidence 聚合得出。"""
    __tablename__ = "cms_tag_candidates"
    __table_args__ = (
        UniqueConstraint(
            "proposed_kind",
            "normalized_label",
            name="uq_cms_tag_candidates_kind_normalized",
        ),
        Index("ix_cms_tag_candidates_status_last_seen", "status", "last_seen_at"),
        Index("ix_cms_tag_candidates_kind_status", "proposed_kind", "status"),
        CheckConstraint(
            "proposed_kind IN ('topic','industry','entity')",
            name="ck_cms_tag_candidates_kind",
        ),
        CheckConstraint(
            "status IN ('candidate','reviewing','activated','merged','rejected')",
            name="ck_cms_tag_candidates_status",
        ),
        CheckConstraint(
            "mean_confidence >= 0.0 AND mean_confidence <= 1.0",
            name="ck_cms_tag_candidates_mean_confidence",
        ),
        CheckConstraint(
            "nearest_similarity IS NULL OR (nearest_similarity >= 0.0 AND nearest_similarity <= 1.0)",
            name="ck_cms_tag_candidates_nearest_similarity",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = Field(description="CMS 展示用首选原始名称")
    normalized_label: str
    proposed_kind: str
    status: str = Field(default="candidate")
    support_article_count_7d: int = Field(default=0, ge=0)
    support_article_count_30d: int = Field(default=0, ge=0)
    distinct_source_count_7d: int = Field(default=0, ge=0)
    distinct_source_count_30d: int = Field(default=0, ge=0)
    distinct_day_count_7d: int = Field(default=0, ge=0)
    distinct_day_count_30d: int = Field(default=0, ge=0)
    mean_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    nearest_tag_id: Optional[int] = Field(
        default=None,
        foreign_key="cms_tags.id",
        ondelete="SET NULL",
    )
    nearest_similarity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    resolution_tag_id: Optional[int] = Field(
        default=None,
        foreign_key="cms_tags.id",
        ondelete="SET NULL",
        description="activated/merged 后解析到的规范标签",
    )
    sample_article_ids_json: str = Field(default="[]")
    risk_flags_json: str = Field(default="[]")
    first_seen_at: str
    last_seen_at: str
    created_at: str
    updated_at: str


class CmsTagCandidateEvidenceRecord(SQLModel, table=True):
    """Candidate 与文章的唯一证据，防分析重试虚增频次。"""
    __tablename__ = "cms_tag_candidate_evidence"
    __table_args__ = (
        Index("ix_cms_tag_candidate_evidence_article_id", "article_id"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_candidate_evidence_confidence"),
    )

    candidate_id: int = Field(
        primary_key=True,
        foreign_key="cms_tag_candidates.id",
        ondelete="CASCADE",
    )
    article_id: str = Field(
        primary_key=True,
        foreign_key="articles.id",
        ondelete="CASCADE",
    )
    source_id: str
    source_owner_or_domain: str = Field(default="")
    published_date: str = Field(default="")
    confidence: float = Field(ge=0.0, le=1.0)
    raw_label: str
    context_excerpt: str = Field(default="", description="最小必要且不得包含私有 RSS 正文")
    prompt_version: str = Field(default="")
    created_at: str


class CmsTagEventRecord(SQLModel, table=True):
    """Taxonomy 治理审计事件。"""
    __tablename__ = "cms_tag_events"
    __table_args__ = (
        Index("ix_cms_tag_events_created_at", "created_at"),
        CheckConstraint(
            "action IN ('activate','rename','merge','deprecate','reject','change_flags','delete_candidate')",
            name="ck_cms_tag_events_action",
        ),
        CheckConstraint("actor_type IN ('user','system')", name="ck_cms_tag_events_actor_type"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    action: str
    source_tag_id: Optional[int] = Field(default=None, foreign_key="cms_tags.id", ondelete="SET NULL")
    target_tag_id: Optional[int] = Field(default=None, foreign_key="cms_tags.id", ondelete="SET NULL")
    actor_type: str = Field(default="user")
    actor_id: str = Field(default="")
    reason: str = Field(default="")
    payload_json: str = Field(default="{}")
    created_at: str


class TagRetagJobRecord(SQLModel, table=True):
    """小批量、带游标和租约的 taxonomy 重标任务。"""
    __tablename__ = "tag_retag_jobs"
    __table_args__ = (
        Index("ix_tag_retag_jobs_claim", "status", "lease_expires_at"),
        Index(
            "uq_tag_retag_jobs_one_active_full_analysis",
            "operation",
            unique=True,
            sqlite_where=text(
                "operation = 'full_analysis' AND status IN ('queued','running','paused')"
            ),
        ),
        CheckConstraint("operation IN ('full_analysis','retag_only')", name="ck_tag_retag_jobs_operation"),
        CheckConstraint(
            "status IN ('queued','running','paused','succeeded','partial_failed','failed','cancelled')",
            name="ck_tag_retag_jobs_status",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: Optional[int] = Field(default=None, foreign_key="cms_tag_events.id", ondelete="SET NULL")
    taxonomy_version: int = Field(foreign_key="taxonomy_versions.version", ondelete="RESTRICT")
    operation: str = Field(default="retag_only")
    scope_json: str = Field(default="{}")
    status: str = Field(default="queued")
    cursor: str = Field(default="")
    affected_count: int = Field(default=0, ge=0)
    succeeded_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    lease_owner: Optional[str] = Field(default=None)
    lease_expires_at: Optional[str] = Field(default=None)
    last_error: Optional[str] = Field(default=None)
    created_at: str
    updated_at: str


class TagRetagJobItemRecord(SQLModel, table=True):
    """Per-article snapshot for resumable ``full_analysis`` backfills.

    ``article_id_snapshot`` remains after an article is deleted so the job can
    settle that target as skipped without retaining article content or URLs.
    Retag-only jobs keep their lighter cursor implementation and do not create
    item rows.
    """

    __tablename__ = "tag_retag_job_items"
    __table_args__ = (
        UniqueConstraint("job_id", "article_id_snapshot", name="uq_tag_retag_job_items_job_article"),
        Index("ix_tag_retag_job_items_job_status_id", "job_id", "status", "id"),
        Index("ix_tag_retag_job_items_article_id", "article_id"),
        CheckConstraint(
            "status IN ('pending','queued','succeeded','failed','skipped')",
            name="ck_tag_retag_job_items_status",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="tag_retag_jobs.id", ondelete="CASCADE")
    article_id: Optional[str] = Field(
        default=None,
        foreign_key="articles.id",
        ondelete="SET NULL",
    )
    article_id_snapshot: str
    status: str = Field(default="pending")
    target_content_hash: str = Field(default="")
    last_error: Optional[str] = Field(default=None)
    queued_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)
    created_at: str
    updated_at: str


class DuplicateGroupRecord(SQLModel, table=True):
    """可被公共和个人日报复用的轻量同事件/重复组。"""
    __tablename__ = "duplicate_groups"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_duplicate_groups_fingerprint"),
        Index("ix_duplicate_groups_representative_article_id", "representative_article_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    fingerprint: str = Field(description="规范 URL/标题相似等确定性指纹")
    strategy: str = Field(default="deterministic_v1")
    representative_article_id: Optional[str] = Field(
        default=None,
        foreign_key="articles.id",
        ondelete="SET NULL",
    )
    created_at: str
    updated_at: str


class DuplicateGroupMemberRecord(SQLModel, table=True):
    """一篇文章最多属于一个重复组。"""
    __tablename__ = "duplicate_group_members"
    __table_args__ = (
        UniqueConstraint("article_id", name="uq_duplicate_group_members_article"),
        CheckConstraint(
            "similarity IS NULL OR (similarity >= 0.0 AND similarity <= 1.0)",
            name="ck_duplicate_group_members_similarity",
        ),
    )

    group_id: int = Field(
        primary_key=True,
        foreign_key="duplicate_groups.id",
        ondelete="CASCADE",
    )
    article_id: str = Field(
        primary_key=True,
        foreign_key="articles.id",
        ondelete="CASCADE",
    )
    similarity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_representative: bool = Field(default=False)
    created_at: str


# （实体简化阶段 2）FetchTaskRecord（旧版单节点定时任务）与 NodeGroupRecord（采集范围/
# 节点组）已退役：存量数据由 Alembic 迁移（drop 前先内联/转换）合并进 CollectionJobRecord；
# 历史运行/文章记录中的 task_id / group_id / source_group_id 列保留供回溯。


class CollectionJobRecord(SQLModel, table=True):
    """可保存、可调度的采集任务定义。"""
    __tablename__ = "collection_jobs"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, description="采集任务名称")
    description: str = Field(default="", description="采集任务说明")
    fetcher_ids_json: str = Field(default="[]", description="直接包含的节点 ID 列表 JSON")
    params_json: str = Field(default="{}", description="任务默认参数 JSON")
    per_fetcher_params_json: str = Field(default="{}", description="按节点覆盖的参数 JSON")
    cron_expr: str = Field(default="", description="可选 Cron 表达式")
    # (单节点 cron 覆盖已退役:一任务一 cron,想要不同节奏建新任务——2026-07 拆分迁移 faithful 保留)
    is_active: bool = Field(default=True, index=True, description="是否启用")
    downstream_policy_json: str = Field(default="{}", description="下游交付策略 JSON")
    legacy_task_id: Optional[int] = Field(default=None, index=True, description="迁移自旧 fetch_tasks 的任务 ID")
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


class CollectionJobRunRecord(SQLModel, table=True):
    """一次采集任务级运行，聚合多个节点级 FetchRunRecord。"""
    __tablename__ = "collection_job_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[int] = Field(default=None, index=True, description="正式采集任务 ID；临时运行为空")
    group_id: Optional[int] = Field(default=None, index=True, description="历史保留：运行时关联的采集范围 ID（节点组已退役）")
    run_scope: str = Field(default="ad_hoc", index=True, description="ad_hoc/saved_job/legacy_task")
    trigger_type: str = Field(default="manual", index=True, description="manual/scheduled")
    status: str = Field(default="running", index=True, description="running/success/partial_failed/failed")
    name: str = Field(default="", description="运行显示名称")
    node_count: int = Field(default=0, description="计划执行节点数")
    child_run_ids_json: str = Field(default="[]", description="关联 fetch_runs ID 列表 JSON")

    started_at: str = Field(index=True, description="开始时间")
    ended_at: Optional[str] = Field(default=None, description="结束时间")
    duration_ms: Optional[int] = Field(default=None, description="执行耗时，毫秒")

    fetched_count: int = Field(default=0, description="聚合抓取器产出数量")
    saved_count: int = Field(default=0, description="聚合新增入库数量")
    skipped_count: int = Field(default=0, description="聚合跳过数量")
    failed_count: int = Field(default=0, description="失败节点数量")
    error_message: Optional[str] = Field(default=None, description="聚合失败摘要")


class FetchRunRecord(SQLModel, table=True):
    """记录每次抓取执行，用于追踪成功率、耗时、增量数量与失败原因。"""
    __tablename__ = "fetch_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    fetcher_id: str = Field(index=True, description="执行的数据源节点 ID")
    task_id: Optional[int] = Field(default=None, index=True, description="历史保留：旧版定时任务 ID（旧任务已退役）")
    job_id: Optional[int] = Field(default=None, index=True, description="关联的采集任务 ID，临时执行时为空")
    job_run_id: Optional[int] = Field(default=None, index=True, description="关联的采集任务级运行 ID")
    source_group_id: Optional[int] = Field(default=None, index=True, description="历史保留：关联采集范围 ID（节点组已退役）")
    run_scope: str = Field(default="ad_hoc", index=True, description="ad_hoc/saved_job/legacy_task")
    trigger_type: str = Field(default="manual", index=True, description="触发类型: manual/scheduled")
    status: str = Field(default="running", index=True, description="执行状态: running/success/failed")
    params_json: str = Field(default="{}", description="本次执行参数")

    started_at: str = Field(index=True, description="开始时间")
    ended_at: Optional[str] = Field(default=None, description="结束时间")
    duration_ms: Optional[int] = Field(default=None, description="执行耗时，毫秒")

    fetched_count: int = Field(default=0, description="抓取器产出的条目数量")
    saved_count: int = Field(default=0, description="成功新增入库的条目数量")
    skipped_count: int = Field(default=0, description="重复或未被任何存储接受的条目数量")
    error_message: Optional[str] = Field(default=None, description="失败原因或异常摘要")


class SourceStateRecord(SQLModel, table=True):
    """每个实际数据源的抓取状态与增量游标。"""
    __tablename__ = "source_states"

    source_id: str = Field(primary_key=True, description="实际数据源 ID，内置 fetcher 通常等于 fetcher_id")
    fetcher_id: str = Field(index=True, description="最近一次使用的抓取器 ID")
    content_type: str = Field(default="", index=True, description="最近一次产出的内容结构类型")
    status: str = Field(default="never_run", index=True, description="healthy/failing/running/never_run/unknown")

    last_started_at: Optional[str] = Field(default=None, index=True, description="最近一次开始时间")
    last_completed_at: Optional[str] = Field(default=None, description="最近一次完成时间")
    last_success_at: Optional[str] = Field(default=None, index=True, description="最近一次成功时间")
    last_failure_at: Optional[str] = Field(default=None, index=True, description="最近一次失败时间")

    last_run_id: Optional[int] = Field(default=None, index=True, description="最近一次运行记录 ID")
    last_cursor_value: str = Field(default="", description="保守记录的增量游标值，通常是最新内容 ID")
    last_cursor_date: str = Field(default="", index=True, description="保守记录的增量游标时间，通常是最新内容发布时间")
    last_content_id: str = Field(default="", description="最近一次看到的最新内容 ID")

    consecutive_failures: int = Field(default=0, description="连续失败次数")
    total_runs: int = Field(default=0, description="累计运行次数")
    success_runs: int = Field(default=0, description="累计成功次数")
    failed_runs: int = Field(default=0, description="累计失败次数")

    latest_fetched_count: int = Field(default=0, description="最近一次抓取器产出数量")
    latest_saved_count: int = Field(default=0, description="最近一次新增入库数量")
    latest_skipped_count: int = Field(default=0, description="最近一次跳过数量")
    latest_error_type: str = Field(default="", description="最近一次错误类型")
    latest_error_message: Optional[str] = Field(default=None, description="最近一次错误摘要")

    updated_at: str = Field(description="状态更新时间")


class SourceConfigRecord(SQLModel, table=True):
    """可配置数据源定义，作为通用抓取器和后台数据源管理的基础。"""
    __tablename__ = "source_configs"

    source_id: str = Field(primary_key=True, description="稳定的数据源唯一标识")
    name: str = Field(index=True, description="数据源展示名称")
    source_type: str = Field(default="rss", index=True, description="数据源类型，如 rss/wechat/github/arxiv")
    url: str = Field(default="", description="数据源入口 URL")
    category: str = Field(default="", index=True, description="业务分类，如 official/news/paper/community")
    fetcher_id: str = Field(default="", index=True, description="绑定的抓取器 ID，通用源可为空或使用 generic_rss")
    description: str = Field(default="", description="数据源说明")
    source_owner: str = Field(default="", index=True, description="来源主体，如 openai/anthropic/google")
    source_brand: str = Field(default="", index=True, description="承载品牌或产品线，如 claude/gemini/qwen")
    source_scope: str = Field(default="", index=True, description="来源范围，如 company/model_family/api_platform")
    source_channel: str = Field(default="", index=True, description="承载渠道，如 blog/newsroom/changelog/github_release")
    base_url: str = Field(default="", description="审查时记录的候选源 base URL")
    provenance_tier: str = Field(default="", index=True, description="来源直接性分层，如 tier0_primary/tier1_curated")
    content_tags_json: str = Field(default="[]", description="内容标签 JSON 数组")
    signal_strength: str = Field(default="", index=True, description="信号强度判断")
    noise_risk: str = Field(default="", index=True, description="噪声风险判断")
    fetch_reliability: str = Field(default="", index=True, description="抓取可靠性判断")

    # 用户自定源(v3.40):非空=读者自助添加的私有 RSS 源的创建者;空=平台源(admin 管理)。
    # 仅作身份标记与溯源,不承担权限差异(删除语义="退订+无人订阅才物理删",与 owner 无关)。
    owner_username: str = Field(default="", index=True, description="用户自定源创建者;空=平台源")

    # 平台源的分析开关。私有 RSS 在 V1 服务层硬性禁用，直到具备逐订阅用户授权；
    # 此字段不能被解释为源创建者可代表其他订阅者授权。
    ai_analysis_enabled: bool = Field(
        default=True,
        sa_column_kwargs={"server_default": text("1")},
        description="是否允许后续文章 AI 分析",
    )

    is_active: bool = Field(default=True, index=True, description="是否启用该数据源")
    fetch_interval_minutes: Optional[int] = Field(default=None, description="建议抓取间隔，分钟")
    cron_expr: str = Field(default="", description="建议 Cron 表达式，可用于生成 FetchTaskRecord")
    params_json: str = Field(default="{}", description="抓取参数 JSON")

    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


class UserInterestTagRecord(SQLModel, table=True):
    """用户对规范标签的显式关注/屏蔽。priority 仅保留为旧库兼容字段。"""
    __tablename__ = "user_interest_tags"
    __table_args__ = (
        Index("ix_user_interest_tags_owner_stance", "owner_username", "stance"),
        Index("ix_user_interest_tags_tag_id", "tag_id"),
        CheckConstraint("stance IN ('follow','mute')", name="ck_user_interest_tags_stance"),
        CheckConstraint("priority IN ('normal','high')", name="ck_user_interest_tags_priority"),
        CheckConstraint("source = 'explicit'", name="ck_user_interest_tags_source"),
    )

    owner_username: str = Field(
        primary_key=True,
        foreign_key="users.username",
        ondelete="CASCADE",
    )
    tag_id: int = Field(
        primary_key=True,
        foreign_key="cms_tags.id",
        ondelete="CASCADE",
    )
    stance: str = Field(default="follow", description="follow/mute")
    priority: str = Field(default="normal", description="兼容旧数据；新写入固定 normal")
    source: str = Field(default="explicit", description="V1 固定 explicit")
    created_at: str
    updated_at: str


class PersonalDigestEditionRecord(SQLModel, table=True):
    """某用户某日某 revision 的个人日报不可变版本。"""
    __tablename__ = "personal_digest_editions"
    __table_args__ = (
        UniqueConstraint(
            "owner_username",
            "report_date",
            "revision",
            name="uq_personal_digest_editions_owner_date_revision",
        ),
        Index("ix_personal_digest_editions_owner_date", "owner_username", "report_date"),
        Index("ix_personal_digest_editions_ready_scan", "status", "check_after"),
        CheckConstraint("revision >= 1", name="ck_personal_digest_editions_revision"),
        CheckConstraint(
            "status IN ('pending','generating','ready','degraded','failed','superseded')",
            name="ck_personal_digest_editions_status",
        ),
        CheckConstraint(
            "generation_reason IN ("
            "'scheduled','first_open','interest_changed','subscription_changed',"
            "'manual_rebuild','daily_brief_ready','recovery')",
            name="ck_personal_digest_editions_generation_reason",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_username: str = Field(
        foreign_key="users.username",
        ondelete="CASCADE",
        description="个人隐私数据随账户删除级联清理",
    )
    report_date: str = Field(description="Asia/Shanghai 下的 YYYY-MM-DD")
    revision: int = Field(default=1, ge=1)
    status: str = Field(default="pending")
    first_open_at: Optional[str] = Field(default=None)
    check_after: str
    cutoff_at: str
    deadline_at: Optional[str] = Field(default=None)
    generated_at: Optional[str] = Field(default=None)
    expected_source_ids_json: str = Field(default="[]", description="冻结的候选权限边界")
    due_source_ids_json: str = Field(default="[]", description="本版需要等待终态的来源集合")
    source_state_snapshot_json: str = Field(default="{}", description="来源终态与 run/freshness 快照")
    policy_version: str = Field(default="personal-digest-v1")
    taxonomy_version: int = Field(default=0)
    interest_version: int = Field(default=0)
    interest_snapshot_json: str = Field(default="[]", description="本 revision 冻结的兴趣集合")
    generation_token: Optional[str] = Field(default=None, index=True)
    generation_lease_expires_at: Optional[str] = Field(default=None)
    generation_reason: str = Field(default="scheduled")
    degraded_reason: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    created_at: str
    updated_at: str


class PersonalDigestItemRecord(SQLModel, table=True):
    """个人日报条目及最小化历史快照（不复制文章正文）。"""
    __tablename__ = "personal_digest_items"
    __table_args__ = (
        UniqueConstraint("edition_id", "position", name="uq_personal_digest_items_edition_position"),
        Index("ix_personal_digest_items_article_id", "article_id"),
        CheckConstraint("position >= 0", name="ck_personal_digest_items_position"),
        CheckConstraint(
            "selection_lane IN ('interest','quality')",
            name="ck_personal_digest_items_selection_lane",
        ),
        CheckConstraint(
            "quality_score_snapshot IS NULL OR "
            "(quality_score_snapshot >= 1.0 AND quality_score_snapshot <= 10.0)",
            name="ck_personal_digest_items_quality_score",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    edition_id: int = Field(
        foreign_key="personal_digest_editions.id",
        ondelete="CASCADE",
    )
    article_id: Optional[str] = Field(
        default=None,
        foreign_key="articles.id",
        ondelete="SET NULL",
        description="原文删除后置空；最小化展示快照仍可读",
    )
    position: int = Field(ge=0)
    section: str = Field(default="")
    selection_lane: str = Field(description="interest/quality")
    quality_score_snapshot: Optional[float] = Field(default=None, ge=1.0, le=10.0)
    matched_interest_codes_json: str = Field(default="[]")
    ranking_features_json: str = Field(default="{}", description="内部排障特征，不是对外个人评分")
    coverage_adjustments_json: str = Field(default="[]")
    selection_reason: str = Field(default="")
    snapshot_json: str = Field(description="标题、来源、摘要、评分理由、标签和原文 URL 快照")
    created_at: str


class ReaderSubscriptionRecord(SQLModel, table=True):
    """Reader 侧订阅源：定义下游可消费的归档内容范围和独立访问令牌。"""
    __tablename__ = "reader_subscriptions"

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_username: str = Field(default="", index=True, description="订阅归属用户名；空字符串为历史全局订阅")
    name: str = Field(index=True, description="订阅源名称")
    description: str = Field(default="", description="订阅源说明")
    filters_json: str = Field(default="{}", description="内容过滤条件 JSON")
    delivery_policy_json: str = Field(default="{}", description="交付策略 JSON")
    token_hash: str = Field(index=True, description="订阅源访问令牌哈希")
    token_preview: str = Field(default="", description="令牌前后缀预览")
    is_active: bool = Field(default=True, index=True, description="是否启用")
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


class ReaderFeedTokenRecord(SQLModel, table=True):
    """读者个人聚合接口令牌：一个用户一个，覆盖其全部已订阅来源的统一拉取令牌。"""
    __tablename__ = "reader_feed_tokens"

    owner_username: str = Field(primary_key=True, description="令牌归属用户名")
    token_hash: str = Field(index=True, description="聚合接口访问令牌哈希")
    token_preview: str = Field(default="", description="令牌前后缀预览")
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


class ReaderFavoriteRecord(SQLModel, table=True):
    """读者文章收藏：每个用户对单篇文章的收藏关系，按收藏时间排序。

    复合主键 (owner_username, article_id) 保证同一用户对同一文章至多一条；
    article_id 关联 ArticleRecord.id，文章被删除后留存的孤儿记录在列表查询时
    自然被 join 过滤掉，无害。
    """
    __tablename__ = "reader_favorites"

    owner_username: str = Field(primary_key=True, description="收藏归属用户名")
    article_id: str = Field(primary_key=True, index=True, description="收藏的文章 ID")
    created_at: str = Field(index=True, description="收藏时间")


class ArticleShareRecord(SQLModel, table=True):
    """公开分享链接：一行 = 某读者为某篇文章签发的一个免登录只读入口。

    诉求来源：读者想把一篇内容发给同事，此前只能截屏。分享分两档，本表只承载
    「公开链接」这一档（另一档是站内深链，纯前端 URL、不落库、不外泄）。

    **令牌以明文存储，这是与 dsub_/dfeed_ 的有意口径差异**：那两者存 hash、明文
    只回显一次，因为它们能拉走整个订阅库；本表的令牌权限极小（单篇、只读、可设
    有效期、可随时撤销），而分享链接天然是「生成一次、反复复制」的东西——只存
    hash 会让读者关掉弹窗后再也复制不到，只能重新生成，链接越堆越多且旧的仍有效。
    权衡后取可用性：明文入库，仅签发者本人的会话能读回。

    过期与撤销：expires_at 为 None 表示永久；revoked_at 非空即失效（软删，保留
    view_count 供签发者查看触达）。被隐藏源（source_visibility）的文章在访问时
    一律 404——与「读者面隐藏 = 内容交付全量排除」口径一致。
    """
    __tablename__ = "article_shares"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True, description="分享令牌明文（dshr_ 前缀），URL 中的凭据")
    article_id: str = Field(index=True, description="被分享文章 ID")
    owner_username: str = Field(index=True, description="签发者用户名")
    created_at: str = Field(index=True, description="签发时间")
    expires_at: Optional[str] = Field(default=None, description="过期时间；None = 永久有效")
    revoked_at: Optional[str] = Field(default=None, description="撤销时间；非空即失效")
    view_count: int = Field(default=0, description="累计打开次数（含未登录访客）")
    last_viewed_at: Optional[str] = Field(default=None, description="最近一次打开时间")


class ReaderArticleReadStateRecord(SQLModel, table=True):
    """读者文章已读/未读的逐篇显式覆盖：一行 = 某读者对某篇文章的明确态度。

    `is_read=True` 显式已读（打开文章、手动标已读）；`is_read=False` 显式未读
    （手动「标为未读」，可撤销水位/误触带来的已读）；**无行 = 交给
    ReaderReadCursorRecord 水位裁决**。与 ReaderReadRecord（按天×用户×来源的
    计量聚合，供运维看板）职责分离：那张管「读了多少」，这张管「哪篇读没读」。
    复合主键同 ReaderFavoriteRecord；文章删除后留存的孤儿行在未读统计的 join
    中自然不可达，无害。「全部标读」不逐篇写行，而是推进水位并清掉水位覆盖的
    存量行（防表膨胀，显式未读行同样被覆盖清除）。
    """
    __tablename__ = "reader_article_read_states"

    owner_username: str = Field(primary_key=True, description="状态归属用户名")
    article_id: str = Field(primary_key=True, index=True, description="文章 ID")
    is_read: bool = Field(default=True, description="True=显式已读；False=显式未读（撤销覆盖）")
    read_at: str = Field(description="最近一次状态变更时间")


class ReaderReadCursorRecord(SQLModel, table=True):
    """读者按源已读水位：`mark_read_before` 时刻（含）之前抓取入库的文章全部视为已读。

    未读判定基准用 fetched_date 而非 publish_date——补抓历史文章不应人人弹未读。
    订阅成功时初始化水位为订阅时刻（历史存量不算未读）；存量订阅无水位行时由
    读侧懒初始化为当下（升级后首访未读从 0 起算）。「全部标读」= 推进水位到当下。
    """
    __tablename__ = "reader_read_cursors"

    owner_username: str = Field(primary_key=True, description="水位归属用户名")
    source_id: str = Field(primary_key=True, description="来源标识")
    mark_read_before: str = Field(default="", description="该 fetched_date（含）之前视为已读")
    updated_at: str = Field(description="最近一次推进时间")


class MediaAssetRecord(SQLModel, table=True):
    """媒体库（图床）资产：正文外链图片的本地缓存登记，一行 = 一个原始 URL。

    主键 url_hash = sha256(url)，寻址与查重都走它；content_hash = sha256(字节)
    用于**跨 URL 内容去重**——不同 URL 拿到相同字节时共用同一份落盘文件
    （data/media/{content_hash[:2]}/{content_hash}{ext}），删除需检查引用计数。
    归档正文里的原链**从不改写**（档案忠实性）：显示层经 /api/media/proxy 按
    url_hash 命中缓存，未命中即时下载入库，失败 302 回源优雅降级。
    status=failed 行是负缓存（带退避重试），避免对死链反复发起下载。
    """
    __tablename__ = "media_assets"

    url_hash: str = Field(primary_key=True, description="sha256(原始 URL) 十六进制")
    url: str = Field(description="原始图片 URL")
    status: str = Field(default="cached", index=True, description="cached/failed")
    content_hash: Optional[str] = Field(default=None, index=True, description="sha256(文件字节)，failed 行为空")
    mime: str = Field(default="", description="Content-Type，如 image/png")
    ext: str = Field(default="", description="落盘扩展名，含点，如 .png")
    size_bytes: int = Field(default=0, description="文件字节数")
    fail_count: int = Field(default=0, description="累计下载失败次数")
    last_error: Optional[str] = Field(default=None, description="最近一次失败原因摘要")
    created_at: str = Field(description="首次登记时间")
    fetched_at: Optional[str] = Field(default=None, description="最近一次成功下载时间")
    updated_at: str = Field(description="最近一次状态变更时间")


class AppSettingRecord(SQLModel, table=True):
    __tablename__ = "app_settings"
    key: str = Field(primary_key=True)
    value: str = ""


class JobRecord(SQLModel, table=True):
    """持久化后台任务状态机（阶段3）：取代进程内内存态 background_jobs。

    长任务（日报、媒体回填、批量抓取等）提交后立即返回 job_id，
    执行状态/进度/结果落库，从而重启不丢、可跨进程查询、为多实例与 worker 拆分铺路。
    时间戳沿用 epoch 浮点（与旧 to_dict 契约一致，前端轮询无感切换）。
    """
    __tablename__ = "jobs"

    id: str = Field(primary_key=True, description="任务 ID（uuid hex）")
    type: str = Field(index=True, description="任务类型，如 vectorize_all_pending/reindex_all")
    status: str = Field(default="queued", index=True, description="queued/running/succeeded/failed/cancelled")
    total: Optional[int] = Field(default=None, description="总步数，未知则空")
    processed: int = Field(default=0, description="已处理步数")
    payload_json: str = Field(default="{}", description="提交时的入参快照 JSON")
    result_json: Optional[str] = Field(default=None, description="成功结果 JSON")
    error: Optional[str] = Field(default=None, description="失败原因摘要")
    created_by: Optional[str] = Field(default=None, index=True, description="触发账户；系统任务为空")
    created_at: float = Field(index=True, description="创建时间 epoch 秒")
    started_at: Optional[float] = Field(default=None, description="开始执行时间 epoch 秒")
    ended_at: Optional[float] = Field(default=None, description="终态时间 epoch 秒")


class AiUsageRecord(SQLModel, table=True):
    """AI 用量按天聚合：一行 = 某天某用户某用途某模型的累计调用与 token 消耗。

    username 为登录账户名；系统级任务（定时日报等）记为 "system"。
    purpose ∈ translate / ask / daily_brief_map / daily_brief_dedup /
    daily_brief_reduce / source_config / detail_profile。
    """
    __tablename__ = "ai_usage"
    # 聚合键唯一索引（v3.43 审计 M21）：写路径是「不存在则插、存在则累加」，无约束时
    # 并发请求可各判「无记录」双双插行（或互踩旧值丢增量）——约束落库后写侧改
    # SQLite ON CONFLICT DO UPDATE 原子累加（见 ai_usage.record_usage）。
    __table_args__ = (
        Index(
            "uq_ai_usage_day_user_purpose_model",
            "day", "username", "purpose", "model",
            unique=True,
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    day: str = Field(index=True, description="YYYY-MM-DD（本地日期）")
    username: str = Field(index=True, description="归属账户；系统任务为 system")
    purpose: str = Field(index=True, description="用途标签")
    model: str = Field(default="", description="调用的模型名")
    calls: int = Field(default=0, description="累计调用次数")
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    updated_at: str = Field(description="最近一次累加时间")


class ReaderReadRecord(SQLModel, table=True):
    """阅读活动按天聚合：一行 = 某天某读者浏览某来源的累计阅读次数。

    在阅读器中**主动打开一篇文章**即记一次（按文章所属 source_id 归集）；
    供运维面板统计用户阅读总量、各源浏览分布与每日阅读趋势。计量绝不阻断
    阅读主流程（写入异常吞掉）。
    """
    __tablename__ = "reader_reads"
    # 聚合键唯一索引（v3.43 审计 M21）：与 ai_usage 同因同修，写侧原子 upsert
    # 见 reader_activity.record_read。
    __table_args__ = (
        Index(
            "uq_reader_reads_day_user_source",
            "day", "username", "source_id",
            unique=True,
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    day: str = Field(index=True, description="YYYY-MM-DD（本地日期）")
    username: str = Field(index=True, description="归属读者账户")
    source_id: str = Field(index=True, description="被阅读文章所属来源")
    reads: int = Field(default=0, description="累计阅读次数")
    updated_at: str = Field(description="最近一次累加时间")


class LoginEventRecord(SQLModel, table=True):
    """登录事件流：每次成功登录写一行（含精确时间），与 UserRecord.last_login_at
    互补——后者是「最近一次」快照，本表保留历史以支持窗口内登录次数与「最近若干次
    登录时间」列表。登录低频，原始事件留存可控。
    """
    __tablename__ = "login_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, description="登录账户")
    at: str = Field(index=True, description="登录时间 ISO 串")


class AdminAuditRecord(SQLModel, table=True):
    """管理操作审计流：每次穿透到 handler 的管理写请求写一行（多管理员平权后需要
    留痕「谁改了什么」）。故意**不存请求体全文**（防密码等敏感字段落盘）、不存
    IP/UA（对齐 LoginEventRecord 颗粒度）；summary 是按注册表渲染的语义描述，
    无匹配时为空串（前端退化显示 `method path`）。
    """
    __tablename__ = "admin_audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, description="操作者账户")
    method: str = Field(description="HTTP 方法")
    path: str = Field(description="请求路径")
    status_code: int = Field(description="响应状态码")
    summary: str = Field(default="", description="语义描述，无匹配为空串")
    target: Optional[str] = Field(default=None, index=True, description="操作目标，如目标用户名")
    at: str = Field(index=True, description="操作时间 ISO 串")


FEEDBACK_CATEGORIES = frozenset({"source_request", "bug", "suggestion", "other"})
FEEDBACK_STATUSES = frozenset({"open", "in_progress", "resolved", "dismissed"})


class FeedbackRecord(SQLModel, table=True):
    """读者反馈:读者提交诉求/问题(想要新源、bug、建议),管理员收件处理并回复。

    读者只能看/撤回自己的(撤回仅限 open 态);管理员全量可见,流转 status 并写
    admin_note(读者可见的回复)。分类与状态枚举见 FEEDBACK_CATEGORIES / FEEDBACK_STATUSES。
    """
    __tablename__ = "feedbacks"

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_username: str = Field(index=True, description="提交者用户名")
    category: str = Field(index=True, description="source_request/bug/suggestion/other")
    content: str = Field(description="反馈正文,≤2000 字")
    status: str = Field(default="open", index=True, description="open/in_progress/resolved/dismissed")
    admin_note: str = Field(default="", description="管理员回复/处理备注(读者可见)")
    created_at: str = Field(index=True, description="提交时间")
    updated_at: str = Field(description="最近一次状态/回复变更时间")


ANNOUNCEMENT_LEVELS = frozenset({"info", "accent", "warning"})


class AnnouncementRecord(SQLModel, table=True):
    """管理员公告:读者面顶部横幅,逐用户一次性(dismiss 后不再出现,跨设备一致)。

    content 为受限 markdown 子集(仅 **加粗** 与 [文字](http(s)链接)),渲染端
    白名单解析、绝不注入 HTML;level 决定横幅配色(映射 design token,不存任意色值)。
    """
    __tablename__ = "announcements"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(default="", description="可空短标题")
    content: str = Field(description="正文,受限 markdown 子集")
    level: str = Field(default="info", index=True, description="info/accent/warning → 横幅配色档")
    is_active: bool = Field(default=True, index=True, description="是否在读者面展示")
    created_by: str = Field(default="", description="发布者用户名")
    created_at: str = Field(index=True, description="发布时间")
    updated_at: str = Field(description="最近一次编辑时间")


class AnnouncementDismissRecord(SQLModel, table=True):
    """公告逐用户关闭记录:一行 = 某读者关闭了某条公告(「一次性通知」语义的落点)。

    复合主键防重;公告删除时级联清理本表对应行。
    """
    __tablename__ = "announcement_dismissals"

    owner_username: str = Field(primary_key=True, description="关闭者用户名")
    announcement_id: int = Field(primary_key=True, index=True, description="公告 ID")
    dismissed_at: str = Field(description="关闭时间")


class UserRecord(SQLModel, table=True):
    """登录账户：数据库托管，密码以 PBKDF2 哈希存储。

    username 即全局唯一身份，不可重命名（reader_subscriptions /
    reader_feed_tokens 均按 owner_username 值关联）。config 的 [auth]
    仅在该表为空时作为初始种子，之后以本表为准。
    """
    __tablename__ = "users"

    username: str = Field(primary_key=True, description="登录账号，全局唯一身份")
    password_hash: str = Field(description="PBKDF2 编码串 pbkdf2_sha256$iters$salt$hash")
    # 会话世代（v3.40.4 审计 M04）：登录 token 携带签发时的世代值，校验时必须与本列
    # 一致。密码重置/自助改密时轮换 → 既有 Cookie 立即吊销；建号时随机初始化 →
    # 删号后同名重建不复活旧 Cookie。存量行迁移默认 ""（旧 token 无世代字段按 ""
    # 对待，升级不强制全员重登，首次改密后收紧）。
    session_epoch: str = Field(default="", description="会话世代：改密/建号轮换，吊销既有登录态")
    avatar: Optional[str] = Field(default=None, description="头像，存为 data:image/* base64 URL；空表示用首字母占位")
    role: str = Field(default="user", index=True, description="账户角色：admin | user")
    is_active: bool = Field(default=True, index=True, description="是否启用该账户")
    # 登录默认落地界面：admin 可在 console（管理台）/ reader（阅读器）间选择；
    # user 恒为读者、该字段不生效。server_default 由迁移补，兼容存量行。
    default_surface: str = Field(default="console", description="登录默认落地界面：console | reader")
    ai_beta_enabled: bool = Field(default=False, index=True, description="是否为该用户开启 AI Beta 功能（阅读器内翻译/问答）")
    interest_onboarding_completed_at: Optional[str] = Field(
        default=None,
        description="首次兴趣选择完成时间；空表示普通用户登录后仍需引导",
    )
    # 轻量运维埋点：仅在成功登录/成功调用 AI 时写入，供管理员运维面板统计活跃度与用量。
    last_login_at: Optional[str] = Field(default=None, description="最近一次成功登录时间")
    ai_translate_count: int = Field(default=0, description="累计成功翻译次数")
    ai_ask_count: int = Field(default=0, description="累计成功问答次数")
    ai_last_used_at: Optional[str] = Field(default=None, description="最近一次使用 AI（翻译/问答）的时间")
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")
