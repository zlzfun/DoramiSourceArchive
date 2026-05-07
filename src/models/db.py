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