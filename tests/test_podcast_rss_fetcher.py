import asyncio
import json
import os
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.articles_view import serialize_article_list_item
from api.routers.source_configs import (
    build_source_fetch_params,
    resolve_source_fetcher_id,
    serialize_source_config,
)
from fetchers.impl.podcast_rss_fetcher import GenericPodcastRssFetcher
from fetchers.registry import fetcher_registry
from models.content import PodcastEpisodeContent, serialize_to_metadata
from models.db import ArticleRecord, SourceConfigRecord


class DummyResponse:
    def __init__(self, text: str, url: str = "https://example.test/feed.xml"):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url


def _podcast_feed_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"
      xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
      xmlns:podcast="https://podcastindex.org/namespace/1.0">
      <channel>
        <title>Example AI Podcast</title>
        <itunes:explicit>false</itunes:explicit>
        <itunes:image href="https://cdn.example.test/show.jpg" />
        <item>
          <guid isPermaLink="false">episode-42</guid>
          <title>Long Episode</title>
          <link>https://example.test/episodes/42</link>
          <pubDate>Wed, 02 Sep 2026 01:00:00 GMT</pubDate>
          <description><![CDATA[<p>Detailed show notes.</p>]]></description>
          <category>AI</category>
          <enclosure url="https://cdn.example.test/42.mp3" length="12345678" type="audio/mpeg" />
          <itunes:duration>01:02:03</itunes:duration>
          <itunes:episode>42</itunes:episode>
          <itunes:season>3</itunes:season>
          <itunes:explicit>true</itunes:explicit>
          <itunes:image href="https://cdn.example.test/42.jpg" />
          <podcast:transcript url="https://cdn.example.test/42.vtt" type="text/vtt" language="zh" rel="captions" />
          <podcast:transcript url="https://cdn.example.test/42.json" type="application/json" language="en" />
          <podcast:chapters url="https://cdn.example.test/42.chapters.json" type="application/json+chapters" />
        </item>
        <item>
          <guid isPermaLink="false">episode-43</guid>
          <title>Short Episode</title>
          <link>https://example.test/episodes/43</link>
          <pubDate>Thu, 03 Sep 2026 01:00:00 GMT</pubDate>
          <description>Short episode notes.</description>
          <enclosure url="https://cdn.example.test/43.m4a" length="7654321" type="audio/mp4" />
          <itunes:duration>30:00</itunes:duration>
          <itunes:episode>43</itunes:episode>
          <itunes:season>3</itunes:season>
        </item>
        <item>
          <guid>trailer-without-enclosure</guid>
          <title>Trailer Without Audio</title>
          <pubDate>Fri, 04 Sep 2026 01:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>"""


def test_generic_podcast_rss_parses_episode_metadata_without_downloading_audio():
    fetcher = GenericPodcastRssFetcher()
    requested_urls = []

    async def fake_safe_get(client, url):
        requested_urls.append(url)
        return DummyResponse(_podcast_feed_xml(), url)

    fetcher._safe_get = fake_safe_get

    async def collect():
        return [
            item
            async for item in fetcher.fetch(
                feed_url="https://example.test/feed.xml",
                source_id="podcast_example_ai",
                category="podcast",
                limit=10,
            )
        ]

    episodes = asyncio.run(collect())

    # 只有 feed 被请求；enclosure 与 transcript 都只记录 URL，不在采集阶段下载。
    assert requested_urls == ["https://example.test/feed.xml"]
    # 无 enclosure 条目被过滤；其余单集按发布时间倒序。
    assert [item.title for item in episodes] == ["Short Episode", "Long Episode"]
    short_episode, episode = episodes
    # 原始 XML 补充数据先绑定 entry 再排序；单集未声明 explicit 时继承频道值。
    assert short_episode.explicit is False
    assert short_episode.image_url == "https://cdn.example.test/show.jpg"
    assert short_episode.duration_seconds == 1800
    assert isinstance(episode, PodcastEpisodeContent)
    assert episode.source_id == "podcast_example_ai"
    assert episode.content_type == "podcast_episode"
    assert episode.show_title == "Example AI Podcast"
    assert episode.source_url == "https://example.test/episodes/42"
    assert episode.audio_url == "https://cdn.example.test/42.mp3"
    assert episode.audio_mime == "audio/mpeg"
    assert episode.audio_bytes == 12345678
    assert episode.duration_seconds == 3723
    assert episode.episode == 42
    assert episode.season == 3
    assert episode.explicit is True  # feedparser 丢 true 时由原始 XML 补回
    assert episode.image_url == "https://cdn.example.test/42.jpg"
    assert episode.transcripts == [
        {
            "url": "https://cdn.example.test/42.vtt",
            "type": "text/vtt",
            "language": "zh",
            "rel": "captions",
        },
        {
            "url": "https://cdn.example.test/42.json",
            "type": "application/json",
            "language": "en",
            "rel": "",
        },
    ]
    assert episode.chapters_url == "https://cdn.example.test/42.chapters.json"
    assert episode.chapters_mime == "application/json+chapters"
    assert episode.content == "Detailed show notes."
    assert episode.has_content is True

    metadata = serialize_to_metadata(episode)
    assert metadata["content_type"] == "podcast_episode"
    assert metadata["extensions"]["audio_url"] == episode.audio_url
    assert metadata["extensions"]["duration_seconds"] == 3723


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30:00", 1800),
        ("30:01", 1801),
        ("01:02:03", 3723),
        ("1805", 1805),
        ("bad", None),
        ("1:2:3:4", None),
        (None, None),
    ],
)
def test_podcast_duration_normalization(raw, expected):
    assert GenericPodcastRssFetcher._duration_seconds(raw) == expected


def test_guidless_podcast_ids_use_stable_enclosure_and_publication_identity():
    fetcher = GenericPodcastRssFetcher()
    published = "Wed, 02 Sep 2026 01:00:00 GMT"

    def entry(title, enclosure_url):
        return {
            "title": title,
            "published": published,
            "enclosures": [{"href": enclosure_url, "type": "audio/mpeg"}],
        }

    first = entry(
        "  Daily\u3000Update  ",
        "https://CDN.example.test/audio.mp3?episode=42&X-Amz-Signature=old&X-Amz-Expires=60",
    )
    renamed = entry(
        "Daily Update (corrected)",
        "https://cdn.example.test/audio.mp3?X-Amz-Expires=120&episode=42&X-Amz-Signature=new",
    )
    different_episode = entry(
        "Daily Update",
        "https://cdn.example.test/audio.mp3?episode=43&X-Amz-Signature=other",
    )

    first_id = fetcher._entry_id("podcast_daily", first)
    assert fetcher._entry_id("podcast_daily", renamed) == first_id
    assert fetcher._entry_id("podcast_daily", different_episode) != first_id


def test_podcast_id_keeps_existing_guid_and_link_compatibility():
    fetcher = GenericPodcastRssFetcher()
    assert fetcher._entry_id("podcast_daily", {"id": "episode-42"}) == super(
        GenericPodcastRssFetcher, fetcher
    )._entry_id("podcast_daily", {"id": "episode-42"})
    assert fetcher._entry_id(
        "podcast_daily", {"link": "https://example.test/episodes/42"}
    ) == super(GenericPodcastRssFetcher, fetcher)._entry_id(
        "podcast_daily", {"link": "https://example.test/episodes/42"}
    )


def test_podcast_source_config_routes_to_dedicated_fetcher_and_shape():
    record = SourceConfigRecord(
        source_id="podcast_demo",
        name="Podcast Demo",
        source_type="podcast",
        url="https://example.test/podcast.xml",
        category="podcast",
        params_json=json.dumps({"limit": 8}),
        created_at="2026-09-02T00:00:00+00:00",
        updated_at="2026-09-02T00:00:00+00:00",
    )

    assert resolve_source_fetcher_id(record) == "generic_podcast_rss"
    params = build_source_fetch_params(record)
    assert params == {
        "limit": 8,
        "source_id": "podcast_demo",
        "category": "podcast",
        "feed_url": "https://example.test/podcast.xml",
        "feed_name": "Podcast Demo",
    }
    serialized = serialize_source_config(record)
    assert serialized["shape"] == "podcast"

    metadata = next(
        item for item in fetcher_registry.get_all_metadata()
        if item["id"] == "generic_podcast_rss"
    )
    assert metadata["content_type"] == "podcast_episode"
    assert metadata["shape"] == "podcast"


def _podcast_record(duration_seconds: int) -> ArticleRecord:
    return ArticleRecord(
        id=f"podcast-{duration_seconds}",
        title="Podcast Episode",
        content_type="podcast_episode",
        source_id="podcast_demo",
        source_url="https://example.test/episodes/1",
        publish_date="2026-09-02T00:00:00+00:00",
        fetched_date="2026-09-02T00:05:00+00:00",
        has_content=True,
        content="Show notes",
        extensions_json=json.dumps(
            {
                "show_title": "Podcast Demo",
                "audio_url": "https://cdn.example.test/1.mp3",
                "audio_mime": "audio/mpeg",
                "audio_bytes": 1000,
                "duration_seconds": duration_seconds,
                "episode": 1,
                "season": 2,
                "explicit": False,
                "image_url": "https://cdn.example.test/1.jpg",
                "transcripts": [
                    {
                        "url": "https://cdn.example.test/1.vtt",
                        "type": "text/vtt",
                        "language": "zh",
                        "rel": "captions",
                    }
                ],
                "chapters_url": "https://cdn.example.test/1.chapters.json",
                "chapters_mime": "application/json+chapters",
                "raw_data": {"large": "must not leak into the light projection"},
            }
        ),
    )


def test_article_list_and_detail_serializer_project_lightweight_podcast_contract():
    exactly_thirty = serialize_article_list_item(
        _podcast_record(1800), include_content=False, include_extensions=False
    )
    over_thirty = serialize_article_list_item(
        _podcast_record(1801), include_content=True, include_extensions=False
    )

    assert "extensions_json" not in exactly_thirty
    assert "content" not in exactly_thirty
    assert exactly_thirty["podcast"] == {
        "show_title": "Podcast Demo",
        "audio_url": "https://cdn.example.test/1.mp3",
        "audio_mime": "audio/mpeg",
        "audio_bytes": 1000,
        "duration_seconds": 1800,
        "episode": 1,
        "season": 2,
        "explicit": False,
        "image_url": "https://cdn.example.test/1.jpg",
        "transcripts": [
            {
                "url": "https://cdn.example.test/1.vtt",
                "type": "text/vtt",
                "language": "zh",
                "rel": "captions",
            }
        ],
        "chapters_url": "https://cdn.example.test/1.chapters.json",
        "chapters_mime": "application/json+chapters",
        "processing_eligible": False,
        "transcript_available": True,
        "processing_status": "",
        "condensed_audio_url": "",
        "condensed_duration_seconds": None,
    }
    assert over_thirty["podcast"]["processing_eligible"] is True
    assert "raw_data" not in over_thirty["podcast"]


def test_existing_podcast_refreshes_feed_metadata_without_erasing_derived_fields(tmp_path):
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'podcast-refresh.db'}")
    common = {
        "id": "podcast-refresh-1",
        "source_id": "podcast_refresh",
        "source_url": "https://example.test/episodes/1",
        "publish_date": "2026-09-02T00:00:00+00:00",
        "content": "Original show notes",
        "has_content": True,
        "show_title": "Podcast Show",
    }
    initial = PodcastEpisodeContent(
        **common,
        title="Original title",
        fetched_date="2026-09-02T01:00:00+00:00",
        audio_url="https://cdn.example.test/1.mp3?token=old",
        image_url="https://cdn.example.test/cover.jpg",
        explicit=True,
    )
    assert asyncio.run(sink.save(initial)) is True

    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, initial.id)
        extensions = json.loads(record.extensions_json)
        extensions.update(
            {
                "summary_zh": "平台生成的中文摘要",
                "processing_status": "audio_ready",
                "condensed_audio_url": "https://media.example.test/condensed.mp3",
            }
        )
        record.extensions_json = json.dumps(extensions, ensure_ascii=False)
        session.add(record)
        session.commit()

    refreshed = PodcastEpisodeContent(
        **common,
        title="Corrected title",
        fetched_date="2026-09-03T01:00:00+00:00",
        audio_url="https://cdn.example.test/1.mp3?token=new",
        audio_mime="audio/mpeg",
        audio_bytes=123456,
        duration_seconds=1900,
        explicit=False,
        transcripts=[{"url": "https://cdn.example.test/1.vtt", "type": "text/vtt"}],
        chapters_url="https://cdn.example.test/1.chapters.json",
        chapters_mime="application/json+chapters",
    )
    # Metadata is refreshed, but save() remains an insertion signal for pipeline
    # statistics and therefore must not report an existing episode as newly saved.
    assert asyncio.run(sink.save(refreshed)) is False

    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, initial.id)
        extensions = json.loads(record.extensions_json)
        assert record.title == "Corrected title"
        assert record.content == "Original show notes"
        assert record.fetched_date == "2026-09-02T01:00:00+00:00"
        assert record.archive_updated_at == "2026-09-03T01:00:00+00:00"
        assert extensions["audio_url"] == "https://cdn.example.test/1.mp3?token=new"
        assert extensions["duration_seconds"] == 1900
        assert extensions["explicit"] is False
        assert extensions["transcripts"][0]["url"].endswith("/1.vtt")
        assert extensions["chapters_url"].endswith("/1.chapters.json")
        assert extensions["image_url"] == "https://cdn.example.test/cover.jpg"
        assert extensions["summary_zh"] == "平台生成的中文摘要"
        assert extensions["processing_status"] == "audio_ready"
        assert extensions["condensed_audio_url"].endswith("/condensed.mp3")

    # Missing values in a transiently incomplete feed do not erase good stored metadata.
    assert asyncio.run(sink.save(refreshed)) is False


def test_articles_list_and_detail_endpoints_expose_same_podcast_projection(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from models.db import UserRecord
    from services import accounts as accounts_service
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'podcast-api.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role="all")),
    )
    with Session(sink.engine) as session:
        session.add(
            UserRecord(
                username="admin",
                password_hash=accounts_service.hash_password("admin"),
                role="admin",
                is_active=True,
                created_at="2026-09-02T00:00:00+00:00",
                updated_at="2026-09-02T00:00:00+00:00",
            )
        )
        session.commit()

    episode = PodcastEpisodeContent(
        id="podcast-e2e-1",
        title="Endpoint Episode",
        source_url="https://example.test/episodes/e2e",
        publish_date="2026-09-02T00:00:00+00:00",
        source_id="podcast_e2e",
        content="Endpoint show notes",
        has_content=True,
        show_title="Endpoint Show",
        audio_url="https://cdn.example.test/e2e.mp3",
        audio_mime="audio/mpeg",
        duration_seconds=1801,
        transcripts=[{"url": "https://cdn.example.test/e2e.vtt", "type": "text/vtt"}],
    )
    assert asyncio.run(sink.save(episode)) is True

    with TestClient(app_module.app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        listing = client.get(
            "/api/articles",
            params={"include_content": "false", "include_total": "true"},
        )
        assert listing.status_code == 200
        list_item = next(
            item for item in listing.json()["items"] if item["id"] == "podcast-e2e-1"
        )
        detail = client.get("/api/articles/podcast-e2e-1")
        assert detail.status_code == 200
        detail_item = detail.json()

    assert list_item["podcast"] == detail_item["podcast"]
    assert list_item["podcast"]["processing_eligible"] is True
    assert list_item["podcast"]["transcript_available"] is True
    assert "content" not in list_item
    assert detail_item["content"] == "Endpoint show notes"
