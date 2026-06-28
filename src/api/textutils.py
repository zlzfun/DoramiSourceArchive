"""无状态的小工具集合（阶段1 共享 helper 模块化）。

把原本散落在 app.py、被多处复用的纯函数（CSV 切分 / 日期边界 / 当前时间 /
宽松 JSON 编解码）集中到此，供 app.py 与各 Router/视图模块共享，且不依赖任何
app 级可变全局，因此可被任意模块安全 import、不成环。
"""

import datetime
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


def _split_csv(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _date_end_value(raw_value: str) -> str:
    return raw_value if "T" in raw_value else f"{raw_value}T23:59:59"


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _json_loads(raw_value: Optional[str], default: Any = None) -> Any:
    if not raw_value:
        return default if default is not None else {}
    try:
        return json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else {}


def _json_dumps(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _model_dump(model: BaseModel, **kwargs) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _model_to_clean_dict(model: BaseModel) -> Dict[str, Any]:
    return {
        key: value
        for key, value in _model_dump(model).items()
        if value is not None and value != ""
    }
