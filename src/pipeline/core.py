"""
核心中枢：数据流水线 (src/pipeline/core.py)
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List
from storage.base import BaseStorage
from fetchers.base import BaseFetcher
from models.content import BaseContent


@dataclass
class PipelineRunResult:
    fetched_count: int = 0
    saved_count: int = 0
    skipped_count: int = 0
    latest_content_id: str = ""
    latest_content_publish_date: str = ""
    latest_content_source_id: str = ""
    latest_content_type: str = ""


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_newer_content(candidate: BaseContent, current_publish_date: str) -> bool:
    current_dt = _parse_iso_datetime(current_publish_date)
    candidate_dt = _parse_iso_datetime(candidate.publish_date)
    if candidate_dt and current_dt:
        return candidate_dt > current_dt
    if candidate_dt and not current_dt:
        return True
    if not current_dt and not current_publish_date:
        return True
    return False


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
            if _is_newer_content(item, result.latest_content_publish_date):
                result.latest_content_id = item.id
                result.latest_content_publish_date = item.publish_date
                result.latest_content_source_id = item.source_id
                result.latest_content_type = item.content_type
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
