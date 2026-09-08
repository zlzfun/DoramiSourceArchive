"""Shared bounded-text validation for every Podcast artifact boundary."""

from __future__ import annotations


class PodcastTextLimitExceeded(ValueError):
    """A text artifact cannot be accepted or projected within configured limits."""


def validate_text_artifact(
    text: str,
    *,
    max_chars: int,
    max_bytes: int,
) -> tuple[int, int]:
    """Validate Unicode character and encoded-byte ceilings without truncation."""

    if not isinstance(text, str) or not text:
        raise PodcastTextLimitExceeded("Podcast 文本内容为空")
    char_count = len(text)
    if char_count > max_chars:
        raise PodcastTextLimitExceeded("Podcast 文本超过配置的字符上限")
    try:
        byte_count = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PodcastTextLimitExceeded(
            "Podcast 文本包含无效的 Unicode 字符"
        ) from exc
    if byte_count > max_bytes:
        raise PodcastTextLimitExceeded("Podcast 文本超过配置的字节上限")
    return char_count, byte_count
