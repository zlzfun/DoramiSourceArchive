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
class WechatArticleContent(BaseContent):
    """微信公众号文章内容类"""
    content_type: ClassVar[str] = "wechat_article"

    account_name: str = field(default="", metadata={"description": "公众号名称"})
    digest: str = field(default="", metadata={"description": "文章摘要"})
    cover_url: str = field(default="", metadata={"description": "微信文章封面图链接"})
    original_url: str = field(default="", metadata={"description": "未清洗的原始长链接预留"})
    media_type: str = field(default="text/html", metadata={"description": "内容类型 (如图文、视频、纯文本)"})


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
        if f.name in base_field_names:
            metadata[f.name] = value
        else:
            extensions[f.name] = value

    if extensions:
        metadata["extensions"] = extensions

    return metadata