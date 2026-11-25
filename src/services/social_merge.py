"""同一条社交帖再次到达时的结构补全(issue #341)。纯函数,只处理 ``extensions_json`` 里的社交键。

两条规则,都只作用于收录来路(推送 / 手工 / 导入)、且还没有平台原始数据的条目:

- 收录条目再次被收录:只补已有行缺的结构,已有的值一律不动(``fill_missing``);
- 抓取器抓到同一条:抓取版是平台原样,整体换成抓取版(``adopt_fetched``)。

抓取入库的条目、已经换成抓取版的条目都带 ``raw_data.data``,不再被任何一条规则改写。
译文缓存、摘要缓存等服务端写的键不在社交键里,两条规则都原样保留。
"""

from __future__ import annotations

from typing import Any, Optional


# 可以补的键:作者展示信息、帖子关系、语言与标签、配图、引用帖与被转推的原帖。
FILL_KEYS = (
    "author_id",
    "author_name",
    "author_avatar_url",
    "author_avatar_url_large",
    "conversation_id",
    "in_reply_to_id",
    "quoted_post_id",
    "reposted_post_id",
    "lang",
    "tags",
    "media_urls",
    "quoted",
    "reposted",
)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def has_platform_raw(extensions: dict[str, Any]) -> bool:
    """这一条是否带着平台原始数据(抓取器入库的形态)。"""
    raw = extensions.get("raw_data")
    return isinstance(raw, dict) and isinstance(raw.get("data"), dict) and bool(raw["data"])


def fill_missing(existing: dict[str, Any], incoming: dict[str, Any]) -> Optional[dict[str, Any]]:
    """只补不覆盖:已有行为空而来者有值的键取来者;没有可补的返回 None。

    引用帖 / 原帖按整个对象补——已有的对象不替换、也不逐项拼合(两次推送描述的未必是同一条)。
    """
    merged = dict(existing)
    for key in FILL_KEYS:
        if _empty(existing.get(key)) and not _empty(incoming.get(key)):
            merged[key] = incoming[key]
    return merged if merged != existing else None


def adopt_fetched(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """整体换成抓取版的社交键;抓取版没有引用帖 / 原帖时,收录时写的也去掉。"""
    merged = dict(existing)
    merged.pop("quoted", None)
    merged.pop("reposted", None)
    merged.update(incoming)
    return merged
