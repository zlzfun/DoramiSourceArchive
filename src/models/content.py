"""
内容类定义模块 (src/models/content.py)

该模块专注于定义所有内容类型的数据结构，用于存储爬取的内容数据。
"""

from dataclasses import dataclass, field, fields
from typing import List, Optional, Dict, Any, ClassVar
from datetime import datetime


# ==========================================
# 1. 数据模型定义 (纯粹的数据容器 + 元数据)
# ==========================================

@dataclass
class BaseContent:
    """
    内容基类
    定义所有内容通用的基础属性。
    """
    # 强制子类声明内容结构类型 (Schema类别)，不参与实例初始化
    content_type: ClassVar[str]

    # 基础属性 (使用 metadata 替代硬编码注释，支持运行时反射)
    id: str = field(metadata={"description": "唯一序号 (001, 002...)"})
    title: str = field(metadata={"description": "标题"})
    source_url: str = field(metadata={"description": "原始URL"})
    publish_date: str = field(metadata={"description": "发布时间 (ISO 8601格式)"})

    # 架构解耦新增：该实例具体从哪个抓取通道而来
    source_id: str = field(
        default="unknown_source",
        metadata={"description": "数据来源通道标识 (如 huggingface_daily)"}
    )

    fetched_date: str = field(
        default_factory=lambda: datetime.now().isoformat(),
        metadata={"description": "抓取时间"}
    )
    content_format: str = field(
        default="markdown",
        metadata={"description": "内容格式 (markdown/html/txt/pdf/placeholder)"}
    )
    content_file: Optional[str] = field(
        default=None,
        metadata={"description": "内容文件名"}
    )
    has_content: bool = field(
        default=True,
        metadata={"description": "是否有正文内容"}
    )
    content: Optional[str] = field(
        default=None,
        metadata={"description": "正文内容（可选字段）"}
    )

    # --- 动态注册与工厂方法 (管理生命周期) ---

    @classmethod
    def _get_registry(cls) -> Dict[str, type]:
        """动态扫描所有子类并根据 content_type 构建映射表"""
        return {
            subcls.content_type: subcls
            for subcls in cls.__subclasses__()
            if hasattr(subcls, 'content_type')
        }

    @classmethod
    def get_class(cls, content_type: str) -> type:
        """根据 content_type 获取对应的结构子类"""
        registry = cls._get_registry()
        if content_type not in registry:
            raise ValueError(
                f"Unknown content_type: {content_type}. "
                f"Available: {list(registry.keys())}"
            )
        return registry[content_type]

    @classmethod
    def get_all_types(cls) -> List[str]:
        """获取所有支持的内容结构类型"""
        return list(cls._get_registry().keys())


@dataclass
class TechConferenceContent(BaseContent):
    """大厂技术大会内容类"""
    content_type: ClassVar[str] = "tech_conference"

    organization: str = field(default="", metadata={"description": "主办组织/公司"})
    location: str = field(default="", metadata={"description": "举办地点"})
    duration: str = field(default="", metadata={"description": "活动持续时间"})
    keywords: List[str] = field(default_factory=list, metadata={"description": "关键词"})


@dataclass
class AICompanyBlogContent(BaseContent):
    """AI公司博客内容类"""
    content_type: ClassVar[str] = "ai_company_blog"

    organization: str = field(default="", metadata={"description": "发布组织"})
    author: str = field(default="", metadata={"description": "作者"})
    category: str = field(default="", metadata={"description": "文章分类"})
    keywords: List[str] = field(default_factory=list, metadata={"description": "关键词"})


@dataclass
class AIToolsContent(BaseContent):
    """AI研发工具动态内容类"""
    content_type: ClassVar[str] = "ai_tools"

    tool_name: str = field(default="", metadata={"description": "工具名称"})
    version: str = field(default="", metadata={"description": "版本号"})
    repository_url: str = field(default="", metadata={"description": "代码仓库地址"})
    update_type: str = field(
        default="",
        metadata={"description": "更新类型 (major/minor/patch/feature/fix)"}
    )


@dataclass
class AICommunityContent(BaseContent):
    """AI社区洞察内容类"""
    content_type: ClassVar[str] = "ai_community"

    community: str = field(default="", metadata={"description": "社区名称"})
    author: str = field(default="", metadata={"description": "作者"})
    upvotes: Optional[int] = field(default=None, metadata={"description": "点赞数"})
    comments: Optional[int] = field(default=None, metadata={"description": "评论数"})
    keywords: List[str] = field(default_factory=list, metadata={"description": "关键词"})


@dataclass
class SocialPostContent(BaseContent):
    """社交平台帖子内容类，用于 X/Twitter 等外部采集器导入。"""
    content_type: ClassVar[str] = "social_post"

    platform: str = field(default="", metadata={"description": "社交平台，如 x/twitter"})
    author_id: str = field(default="", metadata={"description": "作者稳定 ID"})
    author_handle: str = field(default="", metadata={"description": "作者 handle"})
    author_name: str = field(default="", metadata={"description": "作者展示名称"})
    author_avatar_url: str = field(default="", metadata={"description": "作者原始头像 URL"})
    author_avatar_url_large: str = field(default="", metadata={"description": "作者大尺寸头像 URL"})
    post_id: str = field(default="", metadata={"description": "平台原始帖子 ID"})
    conversation_id: str = field(default="", metadata={"description": "会话或 thread ID"})
    in_reply_to_id: str = field(default="", metadata={"description": "回复目标 ID"})
    quoted_post_id: str = field(default="", metadata={"description": "引用帖子 ID"})
    reposted_post_id: str = field(default="", metadata={"description": "转发帖子 ID"})
    lang: str = field(default="", metadata={"description": "语言"})
    tags: List[str] = field(default_factory=list, metadata={"description": "标签"})
    media_urls: List[str] = field(default_factory=list, metadata={"description": "媒体 URL 列表"})
    metrics: Dict[str, Any] = field(default_factory=dict, metadata={"description": "平台指标，如点赞/转发/回复"})
    # 跨平台引用/转载抽象：适配器在入库前把各平台原始 JSON 归一化为
    # {author_name, author_handle, author_avatar_url, author_avatar_url_large,
    #  text, url, media_urls}，前端不得依赖 X includes.tweets。
    # 作者契约：顶层 author_* 始终是时间线账号（转推者）；
    # reposted.author_* 才是原帖作者。无对应语义时序列化器省略该键。
    quoted: Optional[Dict[str, Any]] = field(
        default=None,
        metadata={"description": "被引用帖摘要", "omit_if_none": True},
    )
    reposted: Optional[Dict[str, Any]] = field(
        default=None,
        metadata={"description": "被转载原帖摘要", "omit_if_none": True},
    )
    raw_data: Optional[Dict[str, Any]] = field(default_factory=dict,
                                               metadata={"description": "外部采集器提供的原始数据"})


@dataclass
class ArxivContent(BaseContent):
    """Arxiv论文内容类"""
    content_type: ClassVar[str] = "arxiv"

    arxiv_id: str = field(default="", metadata={"description": "Arxiv论文ID"})
    arxiv_category: str = field(default="", metadata={"description": "Arxiv分类"})
    authors: List[str] = field(default_factory=list, metadata={"description": "作者列表"})
    doi: str = field(default="", metadata={"description": "DOI链接"})
    journal_ref: str = field(default="", metadata={"description": "期刊引用"})
    code_url: str = field(default="", metadata={"description": "代码链接"})


@dataclass
class RssArticleContent(BaseContent):
    """通用RSS订阅文章内容类"""
    content_type: ClassVar[str] = "rss_article"

    feed_name: str = field(default="", metadata={"description": "RSS频道/站点名称"})
    author: str = field(default="", metadata={"description": "文章作者"})
    tags: List[str] = field(default_factory=list, metadata={"description": "文章标签与分类"})
    guid: str = field(default="", metadata={"description": "RSS全局唯一标识(GUID)"})
    summary: str = field(default="", metadata={"description": "文章摘要(区别于正文全文)"})
    updated_date: str = field(default="", metadata={"description": "文章更新时间"})
    media_url: str = field(default="", metadata={"description": "题图或多媒体链接"})
    raw_data: Optional[Dict[str, Any]] = field(default_factory=dict,
                                               metadata={"description": "原始抓取字典(保证无限扩展性)"})


@dataclass
class WebPageArticleContent(BaseContent):
    """官网/博客/新闻网页列表文章内容类"""
    content_type: ClassVar[str] = "web_article"

    site_name: str = field(default="", metadata={"description": "站点名称"})
    source_section: str = field(default="", metadata={"description": "站点栏目或页面来源"})
    summary: str = field(default="", metadata={"description": "列表页摘要或上下文"})
    tags: List[str] = field(default_factory=list, metadata={"description": "页面标签与分类"})
    raw_data: Optional[Dict[str, Any]] = field(default_factory=dict,
                                               metadata={"description": "原始列表页解析信息"})


@dataclass
class GitHubReleaseContent(BaseContent):
    """GitHub Release 内容类"""
    content_type: ClassVar[str] = "github_release"

    repository: str = field(default="", metadata={"description": "仓库全名 owner/repo"})
    owner: str = field(default="", metadata={"description": "仓库 owner"})
    repo: str = field(default="", metadata={"description": "仓库名称"})
    tag_name: str = field(default="", metadata={"description": "Release 标签"})
    release_name: str = field(default="", metadata={"description": "Release 名称"})
    author_login: str = field(default="", metadata={"description": "发布者 GitHub login"})
    target_commitish: str = field(default="", metadata={"description": "目标分支或提交"})
    draft: bool = field(default=False, metadata={"description": "是否草稿"})
    prerelease: bool = field(default=False, metadata={"description": "是否预发布"})
    assets: List[Dict[str, Any]] = field(default_factory=list, metadata={"description": "Release 资产元数据"})
    tarball_url: str = field(default="", metadata={"description": "源码 tarball URL"})
    zipball_url: str = field(default="", metadata={"description": "源码 zipball URL"})
    raw_data: Optional[Dict[str, Any]] = field(default_factory=dict,
                                               metadata={"description": "GitHub API 原始摘要信息"})


@dataclass
class GitHubRepositoryContent(BaseContent):
    """GitHub 仓库内容类，用于跟踪组织下的新仓库信号。"""
    content_type: ClassVar[str] = "github_repository"

    repository: str = field(default="", metadata={"description": "仓库全名 owner/repo"})
    owner: str = field(default="", metadata={"description": "仓库 owner"})
    repo: str = field(default="", metadata={"description": "仓库名称"})
    description: str = field(default="", metadata={"description": "仓库描述"})
    language: str = field(default="", metadata={"description": "主要语言"})
    default_branch: str = field(default="", metadata={"description": "默认分支"})
    stars: int = field(default=0, metadata={"description": "Star 数"})
    forks: int = field(default=0, metadata={"description": "Fork 数"})
    open_issues: int = field(default=0, metadata={"description": "Open issues 数"})
    archived: bool = field(default=False, metadata={"description": "是否归档"})
    fork: bool = field(default=False, metadata={"description": "是否 fork 仓库"})
    license_name: str = field(default="", metadata={"description": "许可证名称"})
    pushed_at: str = field(default="", metadata={"description": "最近 push 时间"})
    updated_at: str = field(default="", metadata={"description": "最近更新时间"})
    raw_data: Optional[Dict[str, Any]] = field(default_factory=dict,
                                               metadata={"description": "GitHub API 原始摘要信息"})


@dataclass
class HuggingFaceModelContent(BaseContent):
    """Hugging Face 模型内容类，用于跟踪组织下的新模型信号。"""
    content_type: ClassVar[str] = "hf_model"

    model_id: str = field(default="", metadata={"description": "Hugging Face 模型 ID"})
    author: str = field(default="", metadata={"description": "模型作者或组织"})
    pipeline_tag: str = field(default="", metadata={"description": "模型任务类型"})
    library_name: str = field(default="", metadata={"description": "模型库名称"})
    tags: List[str] = field(default_factory=list, metadata={"description": "模型标签"})
    downloads: int = field(default=0, metadata={"description": "下载量"})
    likes: int = field(default=0, metadata={"description": "点赞量"})
    last_modified: str = field(default="", metadata={"description": "最近更新时间"})
    gated: str = field(default="", metadata={"description": "访问限制状态"})
    private: bool = field(default=False, metadata={"description": "是否私有"})
    raw_data: Optional[Dict[str, Any]] = field(default_factory=dict,
                                               metadata={"description": "Hugging Face API 原始摘要信息"})


@dataclass
class WechatArticleContent(BaseContent):
    """微信公众号文章内容类"""
    content_type: ClassVar[str] = "wechat_article"

    account_name: str = field(default="", metadata={"description": "公众号名称"})
    digest: str = field(default="", metadata={"description": "文章摘要"})
    cover_url: str = field(default="", metadata={"description": "微信文章封面图链接"})
    original_url: str = field(default="", metadata={"description": "未清洗的原始长链接预留"})
    media_type: str = field(default="text/html", metadata={"description": "内容类型 (如图文、视频、纯文本)"})


@dataclass
class DailyBriefContent(BaseContent):
    """每日 AI 资讯日报内容类，由后端 LLM 编排生成。

    content 字段存日报 Markdown 全文；扩展字段保留生成过程的结构化元数据，
    便于追溯纳入范围与去重游标。写一条 ArticleRecord 即自动成为可订阅源
    （source_id="dorami_daily_brief"）。
    """
    content_type: ClassVar[str] = "daily_brief"

    report_date: str = field(default="", metadata={"description": "报告日期 YYYY-MM-DD"})
    articles_count: int = field(default=0, metadata={"description": "最终纳入日报的文章数"})
    categories_count: int = field(default=0, metadata={"description": "覆盖的分类数"})
    included_article_ids: List[str] = field(default_factory=list, metadata={"description": "纳入的 article id 列表"})
    items: List[Dict[str, Any]] = field(default_factory=list, metadata={"description": "各条目结构化概括 (Dify schema + score)"})
    cursor_before: str = field(default="", metadata={"description": "本次纳入前的 fetched_date 游标"})
    cursor_after: str = field(default="", metadata={"description": "本次推进后的 fetched_date 游标"})
    llm_model: str = field(default="", metadata={"description": "生成所用模型"})
    generated_at: str = field(default="", metadata={"description": "生成完成时间 (ISO)"})


# ==========================================
# 2. 外部工具方法 (序列化)
# ==========================================

def serialize_to_metadata(content_obj: BaseContent) -> Dict[str, Any]:
    """
    将内容对象序列化为字典格式。
    自动提取并嵌套子类特有的扩展字段到 'extensions' 中。
    """
    base_field_names = {f.name for f in fields(BaseContent)}
    metadata: Dict[str, Any] = {
        # 强制将类属性写入字典
        "content_type": getattr(content_obj.__class__, 'content_type', 'unknown')
    }
    extensions: Dict[str, Any] = {}

    for f in fields(content_obj):
        value = getattr(content_obj, f.name)
        if value is None and f.metadata.get("omit_if_none"):
            continue
        if f.name in base_field_names:
            metadata[f.name] = value
        else:
            extensions[f.name] = value

    if extensions:
        metadata["extensions"] = extensions

    return metadata


@dataclass
class GitHubTrendingDigestContent(BaseContent):
    """GitHub Trending 每日汇总内容类:一天一条,正文为当日榜单的 GFM 表格。

    逐仓库条目模式已否决(2026-07 用户拍板):连续在榜者要么沉底要么反复置顶,
    且逐条刷动态流;榜单本是「一天一景」的快照,汇总即原生形态。结构化榜单
    数据保留在 items_json 扩展字段供下游使用。
    """
    content_type: ClassVar[str] = "github_trending"

    items_json: str = field(default="[]", metadata={"description": "当日榜单结构化数组 JSON"})
    repo_count: int = field(default=0, metadata={"description": "榜单条目数"})
