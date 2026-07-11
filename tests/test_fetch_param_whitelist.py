"""BaseFetcher.fetch 的 schema 白名单过滤契约(参数固化波)。

非模板节点只接受自己 schema 声明的参数——历史任务残留的已退场字段被剔除,
不会击穿类内固化默认;模板节点(is_template)不过滤。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.base import BaseFetcher  # noqa: E402


class _ProbeFetcher(BaseFetcher):
    source_id = "probe_fetcher"
    content_type = "rss"
    name = "探针"
    icon = "x"
    description = "测试用"

    @classmethod
    def get_parameter_schema(cls):
        return [{"field": "limit", "label": "单次获取上限", "type": "number", "default": 5}]

    async def _run(self, client, **kwargs):
        self.seen_kwargs = dict(kwargs)
        if False:
            yield None


async def _drain(agen):
    async for _ in agen:
        pass


def test_non_template_fetch_drops_unknown_params():
    f = _ProbeFetcher()
    asyncio.run(_drain(f.fetch(limit=3, detail_max_chars=8000, fetch_detail=True)))
    assert f.seen_kwargs == {"limit": 3}


def test_template_fetch_keeps_all_params():
    f = _ProbeFetcher()
    f.is_template = True
    asyncio.run(_drain(f.fetch(limit=3, feed_url="https://x/rss")))
    assert f.seen_kwargs == {"limit": 3, "feed_url": "https://x/rss"}
