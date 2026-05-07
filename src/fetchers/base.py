"""
抓取器基类模块 (src/fetchers/base.py)

定义了所有抓取器的标准接口。
新增了基于 Schema 的前端 UI 反射驱动能力，以及 source_id / content_type 的维度解耦。
"""

import abc
import logging
import asyncio
from typing import AsyncGenerator, Optional, Dict, Any, List
import httpx

from models.content import BaseContent


class BaseFetcher(abc.ABC):
    """
    无状态抓取器基类
    """

    # ==========================================
    # 1. 架构解耦标识 (必须由子类覆盖)
    # ==========================================

    # 抓取渠道的唯一标识 (例如: "huggingface_daily", "arxiv_official")
    source_id: str = "unknown_source"

    # 产出的数据结构类型，对应 models.content (例如: "arxiv", "tech_news")
    content_type: str = "unknown_content"

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

        try:
            # 使用上下文管理器统一管理 HTTP 连接池
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.default_headers) as client:
                # 将 client 传递给子类的具体实现逻辑
                async for content_item in self._run(client, **kwargs):
                    # ⚠️ 架构约束点：强制校验并覆写实例的血统证明
                    # 防止由于具体实现者疏忽，导致进入台账的数据标识错乱
                    content_item.source_id = self.source_id
                    content_item.content_type = self.content_type

                    yield content_item

        except Exception as e:
            self.logger.error(f"❌ 抓取任务异常中断: {str(e)}", exc_info=True)
        finally:
            self.logger.info(f"🏁 抓取任务结束: {self.source_id}")

    @abc.abstractmethod
    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        """
        抽象方法，由子类实现具体的抓取和解析逻辑。
        必须通过 `yield` 逐个返回实例化的 Content 对象。
        """
        pass

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