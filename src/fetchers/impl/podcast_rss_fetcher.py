"""Podcast RSS/Atom 元数据抓取器。

抓取阶段只解析 feed 与 enclosure 元数据，不下载音频、不执行转录。这样单次采集
仍是轻量 I/O；超过 30 分钟的后处理资格由 API 投影按时长严格派生。
"""

import hashlib
import unicodedata
from collections.abc import Mapping
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

import feedparser
import httpx

from fetchers.impl.rss_fetcher import GenericRssFetcher
from models.content import BaseContent, PodcastEpisodeContent


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1].split(":", 1)[-1].lower()


def _element_url(element: ElementTree.Element) -> str:
    direct = str(element.attrib.get("href") or element.attrib.get("url") or "").strip()
    if direct:
        return direct
    for child in list(element):
        if _local_name(child.tag) == "url" and (child.text or "").strip():
            return str(child.text).strip()
    return str(element.text or "").strip() if not list(element) else ""


def _raw_podcast_supplements(feed_bytes: bytes) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """补回 feedparser 会丢失的命名空间值（例如 true/false explicit、itunes:image）。"""
    try:
        root = ElementTree.fromstring(feed_bytes)
    except (ElementTree.ParseError, ValueError):
        return {}, []

    containers = [element for element in root.iter() if _local_name(element.tag) in {"channel", "feed"}]
    container = containers[0] if containers else root

    def parse_element(parent: ElementTree.Element) -> Dict[str, Any]:
        data: Dict[str, Any] = {"transcripts": []}
        for child in list(parent):
            name = _local_name(child.tag)
            if name == "explicit":
                data["explicit"] = str(child.text or "").strip()
            elif name == "image":
                image_url = _element_url(child)
                if image_url:
                    data["image_url"] = image_url
            elif name == "transcript":
                transcript = {
                    key: str(child.attrib.get(key) or "").strip()
                    for key in ("url", "type", "language", "rel")
                }
                if transcript["url"]:
                    data["transcripts"].append(transcript)
            elif name == "chapters":
                data["chapters"] = {
                    "url": str(child.attrib.get("url") or "").strip(),
                    "type": str(child.attrib.get("type") or "").strip(),
                }
        return data

    channel_data = parse_element(container)
    item_elements = [
        element for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]
    return channel_data, [parse_element(element) for element in item_elements]


class GenericPodcastRssFetcher(GenericRssFetcher):
    """参数驱动的 Podcast RSS/Atom 模板抓取器。"""

    is_template = True
    source_id = "generic_podcast_rss"
    content_type = "podcast_episode"
    content_shape = "podcast"
    category = "advanced"

    name = "通用 Podcast RSS"
    description = "抓取 Podcast RSS 单集、音频 enclosure、时长、封面、transcript 与 chapters 元数据。"
    icon = "🎙️"
    default_limit = 20

    _volatile_enclosure_query_keys = {
        "auth",
        "auth-key",
        "expires",
        "exp",
        "hdntl",
        "hdnts",
        "jwt",
        "key-pair-id",
        "policy",
        "sig",
        "signature",
        "token",
    }

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "feed_url", "label": "Podcast RSS 地址", "type": "url", "default": ""},
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "feed_name", "label": "节目名称", "type": "text", "default": ""},
            {"field": "category", "label": "业务分类", "type": "text", "default": "podcast"},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
        ]

    @staticmethod
    def _mapping_value(value: Any, *keys: str) -> str:
        if isinstance(value, Mapping):
            for key in keys:
                candidate = value.get(key)
                if candidate not in (None, ""):
                    return str(candidate).strip()
        return ""

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @classmethod
    def _duration_seconds(cls, value: Any) -> Optional[int]:
        if value in (None, "") or isinstance(value, bool):
            return None
        raw = str(value).strip()
        if ":" not in raw:
            return cls._optional_int(raw)
        parts = raw.split(":")
        if len(parts) not in {2, 3}:
            return None
        try:
            numbers = [int(part) for part in parts]
        except (TypeError, ValueError):
            return None
        if any(number < 0 for number in numbers):
            return None
        if len(numbers) == 2:
            minutes, seconds = numbers
            return minutes * 60 + seconds
        hours, minutes, seconds = numbers
        return hours * 3600 + minutes * 60 + seconds

    @staticmethod
    def _explicit(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if value in (None, ""):
            return None
        normalized = str(value).strip().lower()
        if normalized in {"yes", "true", "explicit", "1"}:
            return True
        if normalized in {"no", "false", "clean", "0"}:
            return False
        return None

    def _enclosure(self, entry: Any) -> Tuple[str, str, Optional[int]]:
        candidates = list(entry.get("enclosures") or [])
        for link in entry.get("links") or []:
            rel = self._mapping_value(link, "rel").lower()
            href = self._mapping_value(link, "href", "url")
            if rel == "enclosure" and href and link not in candidates:
                candidates.append(link)
        if not candidates:
            return "", "", None
        audio_candidates = [
            candidate for candidate in candidates
            if self._mapping_value(candidate, "type").lower().startswith("audio/")
        ]
        selected = (audio_candidates or candidates)[0]
        return (
            self._mapping_value(selected, "href", "url"),
            self._mapping_value(selected, "type"),
            self._optional_int(self._mapping_value(selected, "length")),
        )

    @classmethod
    def _stable_enclosure_url(cls, value: str) -> str:
        """Remove rotating CDN credentials while retaining episode-identifying query data."""
        raw = str(value or "").strip()
        if not raw:
            return ""
        parts = urlsplit(raw)
        stable_query = []
        for key, query_value in parse_qsl(parts.query, keep_blank_values=True):
            normalized_key = key.strip().casefold().replace("_", "-")
            if (
                normalized_key in cls._volatile_enclosure_query_keys
                or normalized_key.startswith("x-amz-")
                or normalized_key.startswith("x-goog-")
            ):
                continue
            stable_query.append((key, query_value))
        return urlunsplit(
            (
                parts.scheme.casefold(),
                parts.netloc.casefold(),
                parts.path,
                urlencode(sorted(stable_query)),
                "",
            )
        )

    @staticmethod
    def _normalized_identity_text(value: Any) -> str:
        return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()

    def _publication_identity(self, entry: Any) -> str:
        for field_name in ("published", "updated", "created"):
            parsed = self._datetime_from_entry_field(entry, field_name)
            if parsed:
                return parsed.isoformat()
            raw = entry.get(field_name)
            if raw:
                return self._normalized_identity_text(raw)
        return ""

    def _entry_id(self, runtime_source_id: str, entry: Any) -> str:
        """Prefer publisher identity; make GUID-less podcast fallbacks episode-specific."""
        stable_value = entry.get("id") or entry.get("guid") or entry.get("link")
        if not stable_value:
            enclosure_url, _, _ = self._enclosure(entry)
            enclosure_identity = self._stable_enclosure_url(enclosure_url)
            publication_identity = self._publication_identity(entry)
            title_identity = self._normalized_identity_text(entry.get("title"))
            if enclosure_identity:
                stable_value = f"enclosure:{enclosure_identity}|published:{publication_identity}"
            elif publication_identity:
                stable_value = f"published:{publication_identity}|title:{title_identity}"
            else:
                stable_value = f"title:{title_identity or repr(entry)}"
        digest = hashlib.sha1(str(stable_value).encode("utf-8")).hexdigest()[:16]
        return f"{runtime_source_id}_{digest}"

    def _transcripts(self, entry: Any, raw: Dict[str, Any]) -> List[Dict[str, str]]:
        raw_values = raw.get("transcripts") or []
        parsed_value = entry.get("podcast_transcript")
        if raw_values:
            values = raw_values
        elif isinstance(parsed_value, (list, tuple)):
            values = list(parsed_value)
        elif parsed_value:
            values = [parsed_value]
        else:
            values = []

        transcripts: List[Dict[str, str]] = []
        seen = set()
        for value in values:
            transcript = {
                "url": self._mapping_value(value, "url", "href"),
                "type": self._mapping_value(value, "type"),
                "language": self._mapping_value(value, "language", "lang"),
                "rel": self._mapping_value(value, "rel"),
            }
            identity = tuple(transcript.values())
            if transcript["url"] and identity not in seen:
                seen.add(identity)
                transcripts.append(transcript)
        return transcripts

    def _chapters(self, entry: Any, raw: Dict[str, Any]) -> Tuple[str, str]:
        value = raw.get("chapters") or entry.get("podcast_chapters") or {}
        return self._mapping_value(value, "url", "href"), self._mapping_value(value, "type")

    def _image_url(
        self,
        entry: Any,
        feed: Any,
        raw_entry: Dict[str, Any],
        raw_feed: Dict[str, Any],
    ) -> str:
        for value in (
            entry.get("itunes_image"),
            entry.get("image"),
            feed.get("itunes_image"),
            feed.get("image"),
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
            mapped = self._mapping_value(value, "href", "url")
            if mapped:
                return mapped
        media_thumbnail = entry.get("media_thumbnail") or []
        if media_thumbnail:
            mapped = self._mapping_value(media_thumbnail[0], "url", "href")
            if mapped:
                return mapped
        return str(raw_entry.get("image_url") or raw_feed.get("image_url") or "").strip()

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        feed_url = str(kwargs.get("feed_url", "")).strip()
        runtime_source_id = str(kwargs.get("source_id", "")).strip() or self.source_id
        requested_show_title = str(kwargs.get("feed_name", "")).strip()
        category = str(kwargs.get("category", "")).strip()
        limit = self._entry_limit(kwargs.get("limit"), self.default_limit)
        if not feed_url:
            raise ValueError("Podcast RSS/Atom 地址不能为空")

        # 与 generic_rss 一致：运行时配置源 ID 是最终归档来源身份。
        self.source_id = runtime_source_id

        is_user_source = runtime_source_id.startswith(("user_rss_", "user_podcast_"))
        ssrf_guard = is_user_source or self._bool_param(kwargs.get("ssrf_guard"), False)
        max_response_bytes = self._positive_int_param(kwargs.get("max_response_bytes"), 0)
        if is_user_source:
            user_cap = 5 * 1024 * 1024
            max_response_bytes = min(max_response_bytes or user_cap, user_cap)
        if ssrf_guard:
            from urllib.parse import urlsplit

            from services.media_store import ensure_public_host

            await ensure_public_host(urlsplit(feed_url).hostname or "")

        if max_response_bytes:
            feed_bytes = await self._fetch_feed_limited(client, feed_url, max_response_bytes)
        else:
            response = await self._safe_get(client, feed_url)
            if not response:
                raise RuntimeError(f"Podcast RSS/Atom 请求失败: {feed_url}")
            feed_bytes = response.content

        parsed_feed = feedparser.parse(feed_bytes)
        if parsed_feed.bozo:
            self.logger.warning(f"Podcast RSS 解析存在异常: {parsed_feed.bozo_exception}")

        raw_feed, raw_entries = _raw_podcast_supplements(feed_bytes)
        if len(raw_entries) == len(parsed_feed.entries):
            for entry, supplement in zip(parsed_feed.entries, raw_entries):
                entry["_dorami_podcast_raw"] = supplement

        show_title = requested_show_title or parsed_feed.feed.get("title", "") or runtime_source_id
        audio_entries = [entry for entry in parsed_feed.entries if self._enclosure(entry)[0]]
        entries = self._sort_entries_newest_first(audio_entries)[:limit]

        for entry in entries:
            raw_entry = entry.get("_dorami_podcast_raw") or {}
            audio_url, audio_mime, audio_bytes = self._enclosure(entry)
            source_url = str(entry.get("link") or audio_url or feed_url)
            html_text = self._entry_html(entry)
            content_text = self._entry_content_text(html_text, source_url)
            explicit_value = entry.get("itunes_explicit")
            if explicit_value is None:
                explicit_value = raw_entry.get("explicit")
            if explicit_value in (None, ""):
                explicit_value = raw_feed.get("explicit")
            chapters_url, chapters_mime = self._chapters(entry, raw_entry)

            raw_data = self._raw_entry(entry)
            raw_data["enclosure"] = {
                "url": audio_url,
                "type": audio_mime,
                "length": audio_bytes,
            }
            yield PodcastEpisodeContent(
                id=self._entry_id(runtime_source_id, entry),
                title=str(entry.get("title") or "未命名播客单集"),
                source_url=source_url,
                publish_date=self._entry_datetime(entry, "published"),
                content=content_text,
                has_content=bool(content_text),
                show_title=str(show_title),
                author=str(entry.get("author") or parsed_feed.feed.get("author") or ""),
                tags=self._entry_tags(entry, category),
                guid=str(entry.get("id") or entry.get("guid") or ""),
                summary=self._clean_text(str(entry.get("summary") or "")),
                updated_date=(
                    self._entry_datetime(entry, "updated")
                    if "updated" in entry or "updated_parsed" in entry
                    else ""
                ),
                audio_url=audio_url,
                audio_mime=audio_mime,
                audio_bytes=audio_bytes,
                duration_seconds=self._duration_seconds(entry.get("itunes_duration")),
                episode=self._optional_int(entry.get("itunes_episode")),
                season=self._optional_int(entry.get("itunes_season")),
                explicit=self._explicit(explicit_value),
                image_url=self._image_url(entry, parsed_feed.feed, raw_entry, raw_feed),
                transcripts=self._transcripts(entry, raw_entry),
                chapters_url=chapters_url,
                chapters_mime=chapters_mime,
                raw_data=raw_data,
            )
