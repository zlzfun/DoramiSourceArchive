"""
核心中枢：数据流水线 (src/pipeline/core.py)
"""
import logging
from typing import List
from storage.base import BaseStorage
from fetchers.base import BaseFetcher


class DataPipeline:
    def __init__(self, storages: List[BaseStorage]):
        self.logger = logging.getLogger("Pipeline")
        self.storages = storages  # 注入所有的存储汇点

    async def run_task(self, fetcher: BaseFetcher, **kwargs):
        """
        驱动 Fetcher 抓取，并将数据并发广播给所有的 Storage
        """
        self.logger.info(f"🚀 开始执行抓取任务: {fetcher.__class__.__name__}")

        total_fetched = 0
        async for item in fetcher.fetch(**kwargs):
            total_fetched += 1
            # 广播给所有的 Storage
            for storage in self.storages:
                await storage.save(item)

        self.logger.info(f"🏁 任务结束: 共抓取 {total_fetched} 条数据。\n")
