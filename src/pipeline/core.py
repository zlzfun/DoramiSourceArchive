"""
核心中枢：数据流水线 (src/pipeline/core.py)
"""
import logging
from dataclasses import dataclass
from typing import List
from storage.base import BaseStorage
from fetchers.base import BaseFetcher


@dataclass
class PipelineRunResult:
    fetched_count: int = 0
    saved_count: int = 0
    skipped_count: int = 0


class DataPipeline:
    def __init__(self, storages: List[BaseStorage]):
        self.logger = logging.getLogger("Pipeline")
        self.storages = storages  # 注入所有的存储汇点

    async def run_task(self, fetcher: BaseFetcher, **kwargs) -> PipelineRunResult:
        """
        驱动 Fetcher 抓取，并将数据并发广播给所有的 Storage
        """
        self.logger.info(f"🚀 开始执行抓取任务: {fetcher.__class__.__name__}")

        result = PipelineRunResult()
        async for item in fetcher.fetch(**kwargs):
            result.fetched_count += 1
            item_saved = False
            # 广播给所有的 Storage
            for storage in self.storages:
                if await storage.save(item):
                    item_saved = True

            if item_saved:
                result.saved_count += 1
            else:
                result.skipped_count += 1

        self.logger.info(
            f"🏁 任务结束: 共抓取 {result.fetched_count} 条数据，"
            f"新增 {result.saved_count} 条，跳过 {result.skipped_count} 条。\n"
        )
        return result
