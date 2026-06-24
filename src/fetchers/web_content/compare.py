"""旁路双路对比：同一 URL 跑 Legacy 与 crawl4ai 两个后端并量化差异。

阶段一的核心交付：在不改动生产路径的前提下，用统一指标评估 crawl4ai 是否值得在某站点接管详情提取。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Dict

from .backend import DetailResult


def _normalize(text: str) -> str:
    """空白归一，便于跨后端做文本相似度比较（crawl4ai 多保留 Markdown 标记）。"""
    return re.sub(r"\s+", " ", (text or "")).strip()


def text_similarity(a: str, b: str) -> float:
    """0~1 的粗粒度相似度（归一后 difflib ratio），用于判断两路正文是否实质一致。"""
    na, nb = _normalize(a), _normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


@dataclass
class ComparisonRow:
    label: str
    url: str
    legacy_chars: int
    legacy_method: str
    legacy_success: bool
    crawl_chars: int
    crawl_method: str
    crawl_profile: str
    crawl_success: bool
    crawl_status: Any
    similarity: float
    len_ratio: float  # crawl / legacy，>1 表示 crawl 取到更多正文
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compare_detail(label: str, url: str, legacy: DetailResult, crawl: DetailResult) -> ComparisonRow:
    legacy_chars = legacy.chars
    crawl_chars = crawl.chars
    similarity = text_similarity(legacy.text, crawl.text)
    len_ratio = round(crawl_chars / legacy_chars, 3) if legacy_chars else 0.0

    notes = []
    if not crawl.success:
        notes.append(f"crawl 失败: {crawl.error or crawl.backend + ' unavailable'}")
    elif similarity >= 0.6:
        notes.append("正文实质一致，可考虑迁移")
    elif crawl_chars > legacy_chars * 1.5:
        notes.append("crawl 正文显著更多，需人工核对是否含噪声")
    elif crawl_chars and crawl_chars < legacy_chars * 0.5:
        notes.append("crawl 正文明显偏少，疑似过滤过度/选择器不准")

    return ComparisonRow(
        label=label,
        url=url,
        legacy_chars=legacy_chars,
        legacy_method=legacy.method,
        legacy_success=legacy.success,
        crawl_chars=crawl_chars,
        crawl_method=crawl.method,
        crawl_profile=crawl.profile_name,
        crawl_success=crawl.success,
        crawl_status=crawl.status_code,
        similarity=round(similarity, 3),
        len_ratio=len_ratio,
        note="; ".join(notes),
    )
