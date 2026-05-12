from typing import Optional
from sqlmodel import SQLModel, Field


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

    has_content: bool = Field(default=True)
    content: Optional[str] = Field(default=None, description="文章正文或长摘要")
    extensions_json: Optional[str] = Field(default="{}", description="扩展元数据 (JSON 字符串)")

    is_vectorized: bool = Field(default=False, index=True, description="是否已经经过向量化并存入 ChromaDB")


class FetchTaskRecord(SQLModel, table=True):
    """用于存储用户配置的定时抓取任务"""
    __tablename__ = "fetch_tasks"

    id: Optional[int] = Field(default=None, primary_key=True)
    fetcher_id: str = Field(index=True, description="绑定的抓取器ID，如 huggingface_daily")
    cron_expr: str = Field(description="Cron 表达式，例如 '0 8 * * *' (每天早上8点)")
    params_json: str = Field(default="{}", description="抓取参数 (limit, past_days 等)")
    is_active: bool = Field(default=True, description="是否启用")
    created_at: str = Field(description="任务创建时间")


class FetchRunRecord(SQLModel, table=True):
    """记录每次抓取执行，用于追踪成功率、耗时、增量数量与失败原因。"""
    __tablename__ = "fetch_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    fetcher_id: str = Field(index=True, description="执行的数据源节点 ID")
    task_id: Optional[int] = Field(default=None, index=True, description="关联的定时任务 ID，手动执行时为空")
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

    is_active: bool = Field(default=True, index=True, description="是否启用该数据源")
    fetch_interval_minutes: Optional[int] = Field(default=None, description="建议抓取间隔，分钟")
    cron_expr: str = Field(default="", description="建议 Cron 表达式，可用于生成 FetchTaskRecord")
    params_json: str = Field(default="{}", description="抓取参数 JSON")

    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")
