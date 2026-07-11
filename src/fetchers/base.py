"""
抓取器基类模块 (src/fetchers/base.py)

定义了所有抓取器的标准接口。
新增了基于 Schema 的前端 UI 反射驱动能力，以及 source_id / content_type 的维度解耦。
"""

import abc
import logging
import asyncio
from typing import AsyncGenerator, Awaitable, Callable, Iterable, Optional, Dict, Any, List
import httpx

from models.content import BaseContent


# 可选的去重预检钩子：给定一批内容 id，返回 {id: has_content}，仅含库中已存在者。
# 由 DataPipeline 在运行前注入（指向 db sink 的 existing_content_flags），
# 抓取器据此在请求正文前跳过重复条目，避免重复访问正文 URL。
DedupLookup = Callable[[Iterable[str]], Awaitable[Dict[str, bool]]]


class BaseFetcher(abc.ABC):
    """
    无状态抓取器基类
    """

    # 模板节点(2026-07 拍板):参数驱动的通用抓取器只在后端保留——作为 source-configs/
    # source_builder 的执行底座与新节点开发的模板参考;前端节点目录一律不显现
    # (质量无保障的通用路径不给运营入口;新增源的正道 = 写代码固化一个质量有保障的 preset)。
    is_template = False

    # ==========================================
    # 1. 架构解耦标识 (必须由子类覆盖)
    # ==========================================

    # 抓取渠道的唯一标识 (例如: "huggingface_daily", "arxiv_official")
    source_id: str = "unknown_source"

    # 产出的数据结构类型，对应 models.content (例如: "arxiv", "tech_news")
    content_type: str = "unknown_content"

    # 面向管理台的来源分类，用于在节点较多时筛选和分组展示。
    category: str = "general"

    # 新一代数据源准入元数据。用于区分来源身份、准入层级、内容标签与抓取可靠性。
    source_owner: str = ""
    source_brand: str = ""
    source_scope: str = ""
    source_channel: str = ""
    source_url: str = ""
    provenance_tier: str = ""
    content_tags: List[str] = []
    signal_strength: str = ""
    noise_risk: str = ""
    fetch_reliability: str = ""

    # 网页详情迁移开关：置 True 的节点，若运行环境装了 crawl4ai 且 URL 命中专用 Profile，
    # 则详情正文走统一的 crawl4ai 后端；否则（未装/未命中/失败）回退现有 httpx 提取。
    # 默认 False —— 协议型/API 型节点不启动浏览器，行为与改动前完全一致。
    web_backend_enabled: bool = False

    # ==========================================
    # 2. 前端 UI 渲染元数据 (必须由子类覆盖)
    # ==========================================
    name: str = "未命名抓取器"
    icon: str = "📦"
    description: str = "缺少描述信息"

    def __init__(self, timeout: int = 30, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.logger = logging.getLogger(self.__class__.__name__)

        # 去重预检钩子，默认 None（不预检）。DataPipeline 运行前可注入。
        self.dedup_lookup: Optional[DedupLookup] = None

        # 本次抓取运行内复用的 crawl4ai 详情后端（仅 web_backend_enabled 节点按需启动）。
        self._web_backend = None

        # 配置通用的请求头，防止被基础反爬拦截
        self.default_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        """
        返回该抓取器需要的配置参数列表 Schema。
        注册中心将把这个 Schema 发给前端，前端据此动态渲染输入框。
        默认返回空列表 (无需参数)。

        示例重写格式:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 5},
            {"field": "category", "label": "分类", "type": "text", "default": ""}
        ]
        """
        return []

    async def fetch(self, **kwargs) -> AsyncGenerator[BaseContent, None]:
        """
        公开的主干调用方法 (模板方法模式)。
        外层的定时任务(Cron)或异步队列池只调用这个方法。
        """
        self.logger.info(f"🚀 开始执行抓取任务: {self.source_id} | 参数: {kwargs}")

        # 按需启动浏览器详情后端（生命周期与本次运行一致；非迁移节点恒为 None）
        self._web_backend = await self._open_web_backend()

        try:
            # 使用上下文管理器统一管理 HTTP 连接池
            async with httpx.AsyncClient(
                    timeout=self.timeout,
                    headers=self.default_headers,
                    follow_redirects=True
            ) as client:
                # 将 client 传递给子类的具体实现逻辑
                async for content_item in self._run(client, **kwargs):
                    # ⚠️ 架构约束点：强制校验并覆写实例的血统证明
                    # 防止由于具体实现者疏忽，导致进入台账的数据标识错乱
                    content_item.source_id = self.source_id
                    content_item.content_type = self.content_type

                    yield content_item

        except Exception as e:
            self.logger.error(f"❌ 抓取任务异常中断: {str(e)}", exc_info=True)
            raise
        finally:
            if self._web_backend is not None:
                try:
                    await self._web_backend.__aexit__(None, None, None)
                finally:
                    self._web_backend = None
            self.logger.info(f"🏁 抓取任务结束: {self.source_id}")

    async def _open_web_backend(self):
        """按需启动 crawl4ai 详情后端。

        未启用 / 未安装 crawl4ai / 浏览器启动失败，均返回 None —— 调用方据此回退现有 httpx 提取，
        保证默认环境（无 crawl4ai）行为与改动前完全一致。
        """
        if not getattr(self, "web_backend_enabled", False):
            return None
        try:
            from fetchers.web_content.crawl4ai_backend import Crawl4AIContentBackend
        except Exception:
            return None
        if not Crawl4AIContentBackend.is_available():
            return None
        backend = Crawl4AIContentBackend()
        await backend.__aenter__()
        if not getattr(backend, "available", False):
            await backend.__aexit__(None, None, None)
            return None
        self.logger.info(f"🌐 已启用 crawl4ai 详情后端: {self.source_id}")
        return backend

    async def _web_backend_detail(
        self, url: str, max_chars: int, profile=None
    ) -> Optional[Dict[str, str]]:
        """命中专用 Profile 时用 crawl4ai 后端提取详情。

        返回与 ``_detail_for_url`` 同构的 dict；后端未启用 / URL 无专用 Profile / 提取失败时返回 None，
        调用方据此回退现有 httpx 提取（旁路保护）。

        ``profile`` 显式传入时（配置驱动节点 generic_web）直接用该 Profile，跳过 URL 匹配预检；
        为空时按 URL 匹配全局 PROFILES，未命中则返回 None 留给现有逻辑。
        """
        backend = getattr(self, "_web_backend", None)
        if backend is None:
            return None
        try:
            if profile is None:
                from fetchers.web_content.profiles import DEFAULT_PROFILE, resolve_profile

                if resolve_profile(url) is DEFAULT_PROFILE:
                    return None  # 该 URL 没有专用 Profile，不接管，留给现有逻辑
            result = await backend.extract(url, max_chars=max_chars, profile=profile)
        except Exception as e:
            self.logger.warning(f"⚠️ web 后端提取失败，回退现有逻辑: {e}")
            return None
        if result.success and result.text:
            return {
                "title": result.title,
                "text": result.text,
                "method": result.method,
                "url": result.url,
            }
        return None

    @abc.abstractmethod
    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        """
        抽象方法，由子类实现具体的抓取和解析逻辑。
        必须通过 `yield` 逐个返回实例化的 Content 对象。
        """
        pass

    async def _lookup_existing_content_flags(self, item_ids: Iterable[str]) -> Dict[str, bool]:
        """通过注入的去重钩子批量查询 ``{id: has_content}``（仅含已存在者）。

        未注入钩子或查询异常时返回空 dict，调用方据此降级为“全部当作新条目”，
        即保持原有抓取行为。
        """
        if not self.dedup_lookup:
            return {}
        try:
            return await self.dedup_lookup(item_ids)
        except Exception as e:  # 去重只是优化，失败不应阻断抓取
            self.logger.warning(f"⚠️ 去重预检失败，回退为不预检: {e}")
            return {}

    async def _safe_get(self, client: httpx.AsyncClient, url: str, **kwargs) -> Optional[httpx.Response]:
        """
        基类提供的安全网络请求工具，内置重试机制。
        子类在 _run 中应当优先调用此方法而不是原生的 client.get
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPError as e:
                self.logger.warning(f"⚠️ 请求失败 ({attempt}/{self.max_retries}) [{url}]: {e}")
                if attempt == self.max_retries:
                    self.logger.error(f"❌ 达到最大重试次数，放弃请求: {url}")
                    return None
                # 指数退避重试 (1s, 2s, 4s...)
                await asyncio.sleep(2 ** (attempt - 1))
        return None

    async def _safe_post(self, client: httpx.AsyncClient, url: str, json_data: Optional[Dict[str, Any]] = None,
                         data: Optional[Any] = None, **kwargs) -> Optional[httpx.Response]:
        """
        基类提供的安全POST请求工具，内置重试机制。
        子类在 _run 中应当优先调用此方法而不是原生的 client.post
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                if json_data is not None:
                    response = await client.post(url, json=json_data, **kwargs)
                elif data is not None:
                    response = await client.post(url, data=data, **kwargs)
                else:
                    response = await client.post(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPError as e:
                self.logger.warning(f"⚠️ POST请求失败 ({attempt}/{self.max_retries}) [{url}]: {e}")
                if attempt == self.max_retries:
                    self.logger.error(f"❌ 达到最大重试次数，放弃POST请求: {url}")
                    return None
                # 指数退避重试 (1s, 2s, 4s...)
                await asyncio.sleep(2 ** (attempt - 1))
        return None
