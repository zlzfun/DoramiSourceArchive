"""收录推送里社交帖载荷的归一化(issue #341)。

调用方用两种方式之一描述一条帖子:

- 结构化字段:``content`` / ``author`` / ``media_urls`` / ``quoted`` / ``reposted``——引用帖与被转推的
  原帖用与外层同样的字段描述;
- ``raw``:X API v2 的帖子对象(``{"data": …, "includes": …}``),交给抓取器同一个归一化函数。

两种都落成与抓取器一致的社交扩展字段——读者卡片、推文翻译、配图预取读的就是这些键。

调用方给的原始 JSON 只用来归一化,不落库:``raw_data`` 会被社交回填当成平台事实重放(连带重写
账号缓存),互动数字同理不收。本模块是纯函数,不访问数据库与网络。
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

from services.x_api_config import larger_avatar_url
from services.x_social_normalizer import InvalidXRawData, normalize_x_post_extensions, x_post_text


MAX_MEDIA = 20
MAX_TAGS = 50
MAX_URL_CHARS = 2000
# X 长帖上限 25,000 字;引用对象随扩展字段一起发给读者列表,不收更长的。
MAX_REFERENCE_TEXT = 25_000
REFERENCE_KEYS = ("quoted", "reposted")
# 引用帖 / 原帖对象认的字段,与外层帖子同名。
REFERENCE_FIELDS = frozenset({"post_id", "url", "content", "lang", "author", "media_urls"})
AUTHOR_FIELDS = frozenset({"name", "handle", "user_id", "avatar_url"})

_X_STATUS = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)", re.IGNORECASE
)


class SocialPayloadRejected(ValueError):
    """这一条社交帖的载荷无法采用;原因直接进逐条回执。"""


def http_url(value: Any) -> str:
    """只认 http(s) 且带主机的链接;其余返回空串。"""
    text = str(value or "").strip()
    if not text or len(text) > MAX_URL_CHARS:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    return text if parts.scheme in {"http", "https"} and parts.hostname else ""


def parse_status_url(url: str) -> Optional[tuple[str, str]]:
    """``x.com/<账号>/status/<帖子 id>`` → (handle, post_id)。"""
    match = _X_STATUS.match(str(url or "").strip())
    return (match.group(1), match.group(2)) if match else None


def unknown_reference_fields(item: dict[str, Any]) -> list[str]:
    """引用帖 / 原帖对象里不认识的字段名(带路径),供回执提示。"""
    unknown: list[str] = []
    for key in REFERENCE_KEYS:
        reference = item.get(key)
        if not isinstance(reference, dict):
            continue
        unknown.extend(f"{key}.{name}" for name in reference if name not in REFERENCE_FIELDS)
        author = reference.get("author")
        if isinstance(author, dict):
            unknown.extend(f"{key}.author.{name}" for name in author if name not in AUTHOR_FIELDS)
    return unknown


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [value]
    return list(value) if isinstance(value, (list, tuple)) else []


def _object(value: Any, label: str, warnings: list[str]) -> dict[str, Any]:
    """期望是对象的字段:给了别的形状就忽略并提示,不让整条失败。"""
    if isinstance(value, dict):
        return value
    if value not in (None, "", [], {}):
        warnings.append(f"{label} 需为对象,已忽略")
    return {}


def _clean_url(value: Any, label: str, warnings: list[str]) -> str:
    url = http_url(value)
    if not url and str(value or "").strip():
        warnings.append(f"{label} 不是 http(s) 链接,已忽略")
    return url


def _clean_media(values: Any, label: str, warnings: list[str]) -> list[str]:
    urls: list[str] = []
    rejected = 0
    if values is not None and not isinstance(values, (str, list, tuple)):
        warnings.append(f"{label} 需为字符串列表,已忽略")
    for value in _as_list(values):
        url = http_url(value)
        if not url:
            rejected += 1 if str(value or "").strip() else 0
        elif url not in urls:
            urls.append(url)
    if rejected:
        warnings.append(f"{label} 里有 {rejected} 项不是 http(s) 链接,已忽略")
    if len(urls) > MAX_MEDIA:
        warnings.append(f"{label} 超过 {MAX_MEDIA} 项,只留前 {MAX_MEDIA} 项")
        urls = urls[:MAX_MEDIA]
    return urls


def _reference(raw: Any, label: str, platform: str, warnings: list[str]) -> tuple[Optional[dict[str, Any]], str]:
    """引用帖 / 被转推原帖 →(入库形状的对象或 None,帖子 id)。入库形状与抓取器归一化的一致。"""
    raw = _object(raw, label, warnings)
    if not raw:
        return None, ""
    author = _object(raw.get("author"), f"{label}.author", warnings)
    text = str(raw.get("content") or "").strip()
    if len(text) > MAX_REFERENCE_TEXT:
        raise SocialPayloadRejected(f"{label}.content 过长(上限 {MAX_REFERENCE_TEXT} 字)")
    url = _clean_url(raw.get("url"), f"{label}.url", warnings)
    post_id = str(raw.get("post_id") or "").strip()[:64]
    handle = str(author.get("handle") or "").strip().lstrip("@")
    if platform == "x":
        parsed = parse_status_url(url)
        if parsed:
            handle = handle or parsed[0]
            post_id = post_id or parsed[1]
        if not url and post_id:
            url = (
                f"https://x.com/{handle}/status/{post_id}" if handle
                else f"https://x.com/i/web/status/{post_id}"
            )
    name = str(author.get("name") or handle).strip()
    avatar = _clean_url(author.get("avatar_url"), f"{label}.author.avatar_url", warnings)
    media = _clean_media(raw.get("media_urls"), f"{label}.media_urls", warnings)
    if not (text or media or handle or name or url):
        warnings.append(f"{label} 是空的,已忽略")
        return None, post_id
    return {
        "author_name": name,
        "author_handle": handle,
        "author_avatar_url": avatar,
        "author_avatar_url_large": larger_avatar_url(avatar),
        "text": text,
        "url": url,
        "media_urls": media,
        "lang": str(raw.get("lang") or "").strip()[:16],
    }, post_id


def _from_fields(
    item: dict[str, Any], platform: str, author: dict[str, Any], warnings: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    extensions = item.get("extensions") if isinstance(item.get("extensions"), dict) else {}
    url = _clean_url(item.get("url"), "url", warnings)
    post_id = str(item.get("post_id") or "").strip()
    handle = str(author.get("handle") or "").strip().lstrip("@")
    if platform == "x":
        parsed = parse_status_url(url)
        if parsed:
            # 只给帖子链接也能收:账号与帖子 id 都在链接里。
            handle = handle or parsed[0]
            post_id = post_id or parsed[1]
    avatar = _clean_url(author.get("avatar_url"), "author.avatar_url", warnings)
    quoted, quoted_id = _reference(item.get("quoted"), "quoted", platform, warnings)
    reposted, reposted_id = _reference(item.get("reposted"), "reposted", platform, warnings)
    top_media = item.get("media_urls")
    if top_media is not None and not isinstance(top_media, (str, list, tuple)):
        warnings.append("media_urls 需为字符串列表,已忽略")
    media = _clean_media(
        [*_as_list(top_media), *_as_list(extensions.get("media_urls"))], "media_urls", warnings
    )
    if reposted:
        # 与抓取器同一口径:转推卡的主体是原帖,原帖的图并进顶层;引用帖的图只留在 quoted 里。
        for media_url in reposted["media_urls"]:
            if media_url not in media and len(media) < MAX_MEDIA:
                media.append(media_url)
    content = str(item.get("content") or "")
    if not content.strip() and reposted and reposted["text"]:
        # 纯转推没有自己的正文:按平台惯例写成「RT @原作者: 原文」,检索与评分才有东西可读。
        prefix = f"RT @{reposted['author_handle']}: " if reposted["author_handle"] else ""
        content = f"{prefix}{reposted['text']}"
    social = {
        "author_id": str(author.get("user_id") or "").strip(),
        "author_handle": handle,
        "author_name": str(author.get("name") or "").strip(),
        "author_avatar_url": avatar,
        "author_avatar_url_large": larger_avatar_url(avatar),
        "post_id": post_id,
        "conversation_id": "",
        "in_reply_to_id": "",
        "quoted_post_id": quoted_id if quoted else "",
        "reposted_post_id": reposted_id if reposted else "",
        "lang": str(item.get("lang") or extensions.get("lang") or "").strip()[:16],
        "tags": [],
        "media_urls": media,
        "quoted": quoted,
        "reposted": reposted,
    }
    derived = {
        "url": url,
        "post_id": post_id,
        "content": content,
        "author": {**author, "handle": handle, "avatar_url": avatar},
    }
    return social, derived


_RAW_SHAPE = 'raw 需为 {"data": {…}, "includes": {…}} 形态的 X API 帖子对象'


def _from_raw(
    item: dict[str, Any], raw: Any, platform: str, author: dict[str, Any], warnings: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if platform != "x":
        raise SocialPayloadRejected("raw 只支持 X 的帖子对象")
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
        raise SocialPayloadRejected(_RAW_SHAPE)
    post = raw["data"]
    includes = raw.get("includes")
    if includes is not None and not isinstance(includes, dict):
        raise SocialPayloadRejected(_RAW_SHAPE)
    raw_id = str(post.get("id") or "").strip()
    if not raw_id:
        raise SocialPayloadRejected("raw.data 缺少帖子 id")
    given_id = str(item.get("post_id") or "").strip()
    if given_id and given_id != raw_id:
        raise SocialPayloadRejected("post_id 与 raw.data.id 不一致")
    overridden = [
        name for name in ("content", "lang", "media_urls", "quoted", "reposted") if item.get(name)
    ]
    if overridden:
        warnings.append(f"已给 raw,这些字段以 raw 为准:{'、'.join(overridden)}")
    handle = str(author.get("handle") or "").strip().lstrip("@")
    fallback_user = {
        "id": str(author.get("user_id") or "").strip(),
        "username": handle,
        "name": str(author.get("name") or "").strip(),
        "profile_image_url": http_url(author.get("avatar_url")),
    }
    try:
        social = normalize_x_post_extensions(
            post, {"includes": includes or {}}, fallback_handle=handle, fallback_user=fallback_user
        )
        content = x_post_text(post)
    except InvalidXRawData as exc:
        raise SocialPayloadRejected(str(exc)) from exc
    except (AttributeError, TypeError, ValueError) as exc:
        raise SocialPayloadRejected(_RAW_SHAPE) from exc
    # 调用方给的互动数字不当事实收;头像与引用对象里的链接同样只认 http(s)。
    social.pop("platform", None)
    social["metrics"] = {}
    social["author_avatar_url"] = http_url(social.get("author_avatar_url"))
    social["author_avatar_url_large"] = larger_avatar_url(social["author_avatar_url"])
    social["lang"] = str(social.get("lang") or "")[:16]
    for key in REFERENCE_KEYS:
        reference = social.get(key)
        if reference is None:
            continue
        if len(str(reference.get("text") or "")) > MAX_REFERENCE_TEXT:
            raise SocialPayloadRejected(f"raw 里的{'引用帖' if key == 'quoted' else '原帖'}正文过长")
        reference["author_avatar_url"] = http_url(reference.get("author_avatar_url"))
        reference["author_avatar_url_large"] = larger_avatar_url(reference["author_avatar_url"])
        reference["lang"] = str(reference.get("lang") or "")[:16]
        reference["media_urls"] = [url for url in reference["media_urls"] if http_url(url)][:MAX_MEDIA]
    social["media_urls"] = [url for url in social["media_urls"] if http_url(url)][:MAX_MEDIA]
    social["tags"] = social["tags"][:MAX_TAGS]
    social.setdefault("quoted", None)
    social.setdefault("reposted", None)
    derived = {
        # 账号在手就用抓取器的链接公式,与抓取入库的同一条天然同链接。
        "url": "" if social["author_handle"] else http_url(item.get("url")),
        "post_id": raw_id,
        "content": content,
        "publish_date": str(post.get("created_at") or item.get("publish_date") or "").strip(),
        "author": {
            "handle": social["author_handle"],
            "user_id": social["author_id"],
            "name": social["author_name"],
            "avatar_url": social["author_avatar_url"],
        },
    }
    return social, derived


def prepare(item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """把一条 ``social_post`` 载荷整理成可入库的形状,返回(整理后的条目,提示)。

    整理后的条目带 ``_social``(社交扩展字段,键与 ``SocialPostContent`` 一致),并把 ``post_id`` /
    ``content`` / ``url`` / ``author`` 写回成可直接用于出处解析与建条目的值。已整理过的条目原样返回。
    载荷不可用时抛 ``SocialPayloadRejected``。
    """
    if isinstance(item.get("_social"), dict):
        return item, []
    warnings: list[str] = []
    platform = str(item.get("platform") or "x").strip().casefold() or "x"
    author = dict(item["author"]) if isinstance(item.get("author"), dict) else {}
    if item.get("raw"):
        social, derived = _from_raw(item, item["raw"], platform, author, warnings)
    else:
        social, derived = _from_fields(item, platform, author, warnings)
    prepared = {**item, **derived, "_social": social}
    prepared.pop("raw", None)
    return prepared, warnings
