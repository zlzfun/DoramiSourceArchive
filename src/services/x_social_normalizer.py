"""X API v2 响应到跨平台社交 extensions 的纯归一化层。

本模块刻意不导入 ``httpx``、fetcher 或 API 配置：它只消费已经取得的 JSON，
既供在线抓取映射，也供存量 ``raw_data`` 离线重放。这样回填路径从依赖图上就不具备
访问 X 的能力。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.x_api_config import larger_avatar_url


class InvalidXRawData(ValueError):
    """归档的 X raw_data 缺失或不足以忠实重放。"""


def x_includes(payload: Dict[str, Any], name: str) -> list[Dict[str, Any]]:
    includes = payload.get("includes")
    if not isinstance(includes, dict) or not isinstance(includes.get(name), list):
        return []
    return [item for item in includes[name] if isinstance(item, dict)]


def x_reference_ids(post: Dict[str, Any]) -> Dict[str, str]:
    ids = {"replied_to": "", "quoted": "", "retweeted": ""}
    for ref in post.get("referenced_tweets") or []:
        if isinstance(ref, dict) and ref.get("type") in ids and ref.get("id"):
            ids[str(ref["type"])] = str(ref["id"])
    return ids


def x_media_urls_for_item(
    item: Dict[str, Any], media_by_key: Dict[str, Dict[str, Any]]
) -> list[str]:
    attachments = item.get("attachments")
    keys = (
        [str(key) for key in attachments.get("media_keys") or [] if key]
        if isinstance(attachments, dict)
        else []
    )
    urls: list[str] = []
    for key in dict.fromkeys(keys):
        media = media_by_key.get(key) or {}
        url = str(media.get("url") or media.get("preview_image_url") or "").strip()
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return urls


def x_post_text(post: Dict[str, Any]) -> str:
    note = post.get("note_tweet")
    if isinstance(note, dict) and note.get("text"):
        return str(note["text"]).strip()
    return str(post.get("text") or "").strip()


def _normalized_reference(
    post_id: str,
    *,
    tweets_by_id: Dict[str, Dict[str, Any]],
    users_by_id: Dict[str, Dict[str, Any]],
    media_by_key: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    referenced = tweets_by_id.get(post_id)
    if not referenced:
        return None
    author_id = str(referenced.get("author_id") or "")
    author = users_by_id.get(author_id, {})
    author_handle = str(author.get("username") or "").strip().lstrip("@")
    author_name = str(author.get("name") or author_handle).strip()
    author_avatar_url = str(author.get("profile_image_url") or "").strip()
    url = (
        f"https://x.com/{author_handle}/status/{post_id}"
        if author_handle
        else f"https://x.com/i/web/status/{post_id}"
    )
    return {
        "author_name": author_name,
        "author_handle": author_handle,
        "author_avatar_url": author_avatar_url,
        "author_avatar_url_large": larger_avatar_url(author_avatar_url),
        "text": x_post_text(referenced),
        "url": url,
        "media_urls": x_media_urls_for_item(referenced, media_by_key),
    }


def normalize_x_post_extensions(
    post: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    fallback_handle: str = "",
    fallback_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把一条 X Post 及其 includes 归一化为完整 social extensions。"""
    fallback = fallback_user if isinstance(fallback_user, dict) else {}
    post_id = str(post.get("id") or "").strip()
    if not post_id:
        raise InvalidXRawData("X raw_data.data 缺少 post id")

    users = x_includes(payload, "users")
    users_by_id = {str(user.get("id")): user for user in users if user.get("id")}
    author_id = str(post.get("author_id") or fallback.get("id") or "").strip()
    author = users_by_id.get(author_id, fallback)
    author_handle = str(author.get("username") or fallback_handle).strip().lstrip("@")
    author_name = str(author.get("name") or author_handle).strip()
    author_avatar_url = str(author.get("profile_image_url") or "").strip()

    media_items = x_includes(payload, "media")
    media_by_key = {
        str(media.get("media_key") or media.get("id")): media
        for media in media_items
        if media.get("media_key") or media.get("id")
    }
    included_tweets = x_includes(payload, "tweets")
    tweets_by_id = {
        str(tweet.get("id")): tweet for tweet in included_tweets if tweet.get("id")
    }
    references = x_reference_ids(post)
    quoted = _normalized_reference(
        references["quoted"],
        tweets_by_id=tweets_by_id,
        users_by_id=users_by_id,
        media_by_key=media_by_key,
    )
    reposted = _normalized_reference(
        references["retweeted"],
        tweets_by_id=tweets_by_id,
        users_by_id=users_by_id,
        media_by_key=media_by_key,
    )
    entities = post.get("entities") if isinstance(post.get("entities"), dict) else {}
    tags = [
        str(tag.get("tag"))
        for tag in entities.get("hashtags") or []
        if isinstance(tag, dict) and tag.get("tag")
    ]

    media_urls = x_media_urls_for_item(post, media_by_key)
    # 转发卡的视觉主体是原帖；引用媒体只留在 quoted.media_urls。
    reposted_id = references["retweeted"]
    if reposted_id and reposted_id in tweets_by_id:
        for url in x_media_urls_for_item(tweets_by_id[reposted_id], media_by_key):
            if url not in media_urls:
                media_urls.append(url)

    normalized: Dict[str, Any] = {
        "platform": "x",
        "author_id": author_id,
        "author_handle": author_handle,
        "author_name": author_name,
        "author_avatar_url": author_avatar_url,
        "author_avatar_url_large": larger_avatar_url(author_avatar_url),
        "post_id": post_id,
        "conversation_id": str(post.get("conversation_id") or post_id),
        "in_reply_to_id": references["replied_to"],
        "quoted_post_id": references["quoted"],
        "reposted_post_id": references["retweeted"],
        "lang": str(post.get("lang") or ""),
        "tags": list(dict.fromkeys(tags)),
        "media_urls": media_urls,
        "metrics": dict(post.get("public_metrics") or {}),
    }
    if quoted is not None:
        normalized["quoted"] = quoted
    if reposted is not None:
        normalized["reposted"] = reposted
    return normalized


def normalize_x_raw_extensions(
    raw_data: Any,
    *,
    existing_extensions: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """校验并重放归档 raw_data，返回（合并后的 extensions，顶层作者 User）。"""
    if not isinstance(raw_data, dict):
        raise InvalidXRawData("raw_data 不是对象")
    post = raw_data.get("data")
    includes = raw_data.get("includes")
    if not isinstance(post, dict) or not isinstance(includes, dict):
        raise InvalidXRawData("raw_data 缺少对象形态的 data/includes")
    author_id = str(post.get("author_id") or "").strip()
    if not author_id:
        raise InvalidXRawData("raw_data.data 缺少 author_id")
    users = x_includes(raw_data, "users")
    author = next(
        (user for user in users if str(user.get("id") or "") == author_id), None
    )
    if author is None:
        raise InvalidXRawData("raw_data.includes.users 缺少顶层作者")

    existing = dict(existing_extensions or {})
    normalized = normalize_x_post_extensions(
        post,
        raw_data,
        fallback_handle=str(existing.get("author_handle") or ""),
        fallback_user=author,
    )
    # quoted/reposted 是“有则写、无则不写”的规范键；先移除旧值再合并，避免
    # 历史错误键在 raw 中已无对应语义时残留。raw_data 自身保持原对象不动。
    existing.pop("quoted", None)
    existing.pop("reposted", None)
    existing.update(normalized)
    return existing, author
