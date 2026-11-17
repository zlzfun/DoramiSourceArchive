"""Evidence required to recommend spending time on an item (issue #178).

This is a consumption rule, not a new scoring ruler or a paid-work trigger.
Podcast show notes remain readable, but cannot certify the value of a full episode.
"""

from __future__ import annotations

import math
from typing import Any

from services.content_classification import current_content_value


def recommendation_value(content_type: str | None, analysis: Any) -> float | None:
    if content_type == "podcast_episode":
        from services.podcast_premium import final_score

        value = final_score(analysis)
    else:
        value, _ = current_content_value(analysis)
    return value if value is not None and math.isfinite(value) and 1 <= value <= 10 else None


def positive_duration(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None
