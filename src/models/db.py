from typing import Optional
from sqlmodel import SQLModel, Field


# 向量索引状态枚举（阶段2/3 跨存储一致性）：比布尔 is_vectorized 更细，区分
# 从未索引 / 索引中 / 已索引 / 失败 / 陈旧（内容改动待重索引）。
# is_vectorized 保留为向后兼容派生位（== indexed），二者由存储层同步维护。
INDEX_STATUS_PENDING = "pending"
INDEX_STATUS_INDEXING = "indexing"
INDEX_STATUS_INDEXED = "indexed"
INDEX_STATUS_FAILED = "failed"
INDEX_STATUS_STALE = "stale"
INDEX_STATUSES = frozenset({
    INDEX_STATUS_PENDING, INDEX_STATUS_INDEXING, INDEX_STATUS_INDEXED,
    INDEX_STATUS_FAILED, INDEX_STATUS_STALE,
})


class ArticleRecord(SQLModel, table=True):
    """关系型数据库表结构：用于 CMS 后端管理系统"""
    __tablename__ = "articles"

    id: str = Field(primary_key=True, description="唯一序号")
    title: str = Field(index=True, description="文章标题")

    # 【架构重构】: 将原先模糊的 source_type 拆分为双维度的 content_type 和 source_id
    content_type: str = Field(index=True, description="数据结构类别 (如 arxiv, tech_conference)")
    source_id: str = Field(index=True, description="数据来源渠道标识 (如 huggingface_daily)")

    source_url: str
    publish_date: str = Field(index=True, description="发布日期")
    fetched_date: str = Field(description="抓取入库的系统时间")
    fetch_run_id: Optional[int] = Field(default=None, index=True, description="首次入库关联的节点级运行 ID")
    job_id: Optional[int] = Field(default=None, index=True, description="首次入库关联的采集任务 ID")
    job_run_id: Optional[int] = Field(default=None, index=True, description="首次入库关联的采集任务级运行 ID")
    source_group_id: Optional[int] = Field(default=None, index=True, description="首次入库关联的采集范围 ID")
    run_scope: str = Field(default="ad_hoc", index=True, description="首次入库运行归属: ad_hoc/saved_job/legacy_task")

    has_content: bool = Field(default=True)
    content: Optional[str] = Field(default=None, description="文章正文或长摘要")
    extensions_json: Optional[str] = Field(default="{}", description="扩展元数据 (JSON 字符串)")

    is_vectorized: bool = Field(default=False, index=True, description="是否已向量化（向后兼容派生位，== index_status 'indexed'）")
    index_status: str = Field(default=INDEX_STATUS_PENDING, index=True, description="向量索引状态: pending/indexing/indexed/failed/stale")


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

    is_active: bool = Field(default=True, index=True, description="是否启用该数据源")
    fetch_interval_minutes: Optional[int] = Field(default=None, description="建议抓取间隔，分钟")
    cron_expr: str = Field(default="", description="建议 Cron 表达式，可用于生成 FetchTaskRecord")
    params_json: str = Field(default="{}", description="抓取参数 JSON")

    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


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

    长任务（全量向量化、全量重索引、日报、批量抓取等）提交后立即返回 job_id，
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


class UserRecord(SQLModel, table=True):
    """登录账户：数据库托管，密码以 PBKDF2 哈希存储。

    username 即全局唯一身份，不可重命名（reader_subscriptions /
    reader_feed_tokens 均按 owner_username 值关联）。config 的 [auth]
    仅在该表为空时作为初始种子，之后以本表为准。
    """
    __tablename__ = "users"

    username: str = Field(primary_key=True, description="登录账号，全局唯一身份")
    password_hash: str = Field(description="PBKDF2 编码串 pbkdf2_sha256$iters$salt$hash")
    avatar: Optional[str] = Field(default=None, description="头像，存为 data:image/* base64 URL；空表示用首字母占位")
    role: str = Field(default="user", index=True, description="账户角色：admin | user")
    is_active: bool = Field(default=True, index=True, description="是否启用该账户")
    ai_beta_enabled: bool = Field(default=False, index=True, description="是否为该用户开启 AI Beta 功能（阅读器内翻译/问答）")
    # 轻量运维埋点：仅在成功登录/成功调用 AI 时写入，供管理员运维面板统计活跃度与用量。
    last_login_at: Optional[str] = Field(default=None, description="最近一次成功登录时间")
    ai_translate_count: int = Field(default=0, description="累计成功翻译次数")
    ai_ask_count: int = Field(default=0, description="累计成功问答次数")
    ai_last_used_at: Optional[str] = Field(default=None, description="最近一次使用 AI（翻译/问答）的时间")
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")
