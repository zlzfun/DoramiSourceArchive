"""现有 httpx 提取路径的后端封装（Legacy / 生产基线）。

直接复用 ``fetchers.impl.article_extractor.extract_article_detail``，即当前
``BaseWebPageListFetcher._detail_for_url`` 走的同一套逻辑，作为旁路对比的基线，
也是"httpx 优先"策略下的默认后端。本类不改动现有 fetcher，只把同一能力暴露为统一接口。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Tuple

import httpx

from config import settings
from fetchers.impl.article_extractor import extract_article_detail

from .backend import DetailResult, WebContentBackend

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class LegacyArticleExtractorBackend(WebContentBackend):
    name = "legacy"

    def __init__(self, *, timeout: int = 30, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "LegacyArticleExtractorBackend":
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": _DEFAULT_UA},
            follow_redirects=True,
            verify=settings.network.tls_verify,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _safe_get(
        self, client: httpx.AsyncClient, url: str, **kwargs
    ) -> Optional[httpx.Response]:
        """带指数退避的 GET，签名与 ``article_extractor`` 期望的 SafeGet 一致。"""
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPError as e:
                logger.warning("⚠️ 请求失败 (%s/%s) [%s]: %s", attempt, self.max_retries, url, e)
                if attempt == self.max_retries:
                    return None
                await asyncio.sleep(2 ** (attempt - 1))
        return None

    async def _fetch(self, url: str) -> Tuple[str, str, Optional[int]]:
        """返回 (html, final_url, status_code)。抽成方法便于测试时打桩，免去真实网络。"""
        assert self._client is not None, "backend 必须作为 async 上下文管理器使用"
        response = await self._safe_get(self._client, url)
        if not response:
            return "", url, None
        return response.text, str(response.url), response.status_code

    async def extract(
        self, url: str, *, max_chars: int = 8_000, detail_min_chars: int = 200,
        profile=None,  # legacy 无 Profile 概念，忽略；仅为契约一致
    ) -> DetailResult:
        if self._client is None:
            raise RuntimeError("LegacyArticleExtractorBackend 必须作为 async 上下文管理器使用")

        html, final_url, status = await self._fetch(url)
        if not html:
            return DetailResult(
                url=url, success=False, backend=self.name, status_code=status,
                error="fetch failed",
            )

        detail = await extract_article_detail(
            self._client, self._safe_get, final_url, html, max_chars, detail_min_chars
        )
        return DetailResult(
            title=detail.title,
            text=detail.text,
            method=detail.method,
            url=detail.url or final_url,
            success=bool(detail.text),
            backend=self.name,
            status_code=status,
            raw_chars=len(html),
        )
