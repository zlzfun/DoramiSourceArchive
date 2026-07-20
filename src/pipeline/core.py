"""
核心中枢：数据流水线 (src/pipeline/core.py)
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from storage.base import BaseStorage
from fetchers.base import BaseFetcher
from models.content import BaseContent
from pipeline.progress import complete_progress, set_progress


@dataclass
class PipelineRunResult:
    fetched_count: int = 0
    saved_count: int = 0
    skipped_count: int = 0
    saved_content_ids: List[str] = field(default_factory=list)
    latest_content_id: str = ""
    latest_cursor_value: str = ""
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

    def _inject_dedup_lookup(self, fetcher: BaseFetcher) -> None:
        """把首个支持 existing_content_flags 的 sink 挂到 fetcher 的去重钩子上。

        抓取器只读地用它做正文请求前的去重预检；找不到这样的 sink 时保持 None
        （抓取器降级为不预检，行为与改动前一致）。
        """
        if not hasattr(fetcher, "dedup_lookup"):
            return
        for storage in self.storages:
            lookup = getattr(storage, "existing_content_flags", None)
            if callable(lookup):
                fetcher.dedup_lookup = lookup
                return

    def _inject_runtime_engine(self, fetcher: BaseFetcher) -> None:
        """给需要持久化运行上下文的 fetcher 注入现有数据库 engine。

        当前由 XTimelineFetcher 使用，用于读取 SourceState cursor 与 AppSetting 配额；
        采用鸭子类型，普通 fetcher 和非数据库 sink 行为不变。
        """
        binder = getattr(fetcher, "bind_runtime_engine", None)
        if not callable(binder):
            return
        for storage in self.storages:
            engine = getattr(storage, "engine", None)
            if engine is not None:
                binder(engine)
                return

    async def run_task(
            self,
            fetcher: BaseFetcher,
            lineage: Optional[Dict[str, Any]] = None,
            **kwargs
    ) -> PipelineRunResult:
        """
        驱动 Fetcher 抓取，并将数据并发广播给所有的 Storage
        """
        self.logger.info(f"🚀 开始执行抓取任务: {fetcher.__class__.__name__}")

        # 注入去重预检钩子：让抓取器在请求正文前先批量查库，跳过重复条目的正文抓取。
        self._inject_dedup_lookup(fetcher)
        self._inject_runtime_engine(fetcher)

        result = PipelineRunResult()
        source_id = getattr(fetcher, "source_id", None)
        limit_hint = kwargs.get("limit")
        total = limit_hint if isinstance(limit_hint, int) and limit_hint > 0 else None
        set_progress(source_id, 0, total)
        try:
            async for item in fetcher.fetch(**kwargs):
                result.fetched_count += 1
                for key, value in (lineage or {}).items():
                    setattr(item, key, value)
                if _is_newer_content(item, result.latest_content_publish_date):
                    result.latest_content_id = item.id
                    result.latest_cursor_value = str(
                        getattr(item, "_cursor_value", "") or item.id
                    )
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
                    result.saved_content_ids.append(item.id)
                else:
                    result.skipped_count += 1

                set_progress(source_id, result.fetched_count, total)
        finally:
            complete_progress(source_id, result.fetched_count, total)

        self.logger.info(
            f"🏁 任务结束: 共抓取 {result.fetched_count} 条数据，"
            f"新增 {result.saved_count} 条，跳过 {result.skipped_count} 条。\n"
        )
        return result
