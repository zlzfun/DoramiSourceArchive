"""X API v2 时间线：字段映射、preset 契约、增量游标与月度配额。"""

import asyncio
import os
import sys

import httpx
import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import XApiConfig  # noqa: E402
from fetchers.impl.x_timeline_fetcher import (  # noqa: E402
    DeepSeekXTimelineFetcher,
    KarpathyXTimelineFetcher,
    MoonshotXTimelineFetcher,
    OpenAIXTimelineFetcher,
    QwenXTimelineFetcher,
    SamAltmanXTimelineFetcher,
    XTimelineFetcher,
)
from fetchers.registry import fetcher_registry  # noqa: E402
from models.content import serialize_to_metadata  # noqa: E402
from models.db import AppSettingRecord, SourceStateRecord  # noqa: E402
from services.x_api_config import read_user_cache  # noqa: E402
from services.x_api_quota import XApiQuotaExceeded, XApiQuotaGuard  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


PRESETS = {
    "x_deepseek_ai": (DeepSeekXTimelineFetcher, "deepseek_ai"),
    "x_alibaba_qwen": (QwenXTimelineFetcher, "Alibaba_Qwen"),
    "x_moonshot_ai": (MoonshotXTimelineFetcher, "Kimi_Moonshot"),
    "x_karpathy": (KarpathyXTimelineFetcher, "karpathy"),
    "x_sama": (SamAltmanXTimelineFetcher, "sama"),
    "x_openai": (OpenAIXTimelineFetcher, "OpenAI"),
}


def _sink(tmp_path, name="x.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _x_config(**overrides):
    values = {
        "bearer_token": "test-bearer-secret",
        "base_url": "https://api.x.test/2",
        "timeout_seconds": 5,
        "max_results": 5,
        "monthly_budget_usd": 5.0,
    }
    values.update(overrides)
    return XApiConfig(**values)


async def _run(fetcher, transport, **kwargs):
    async with httpx.AsyncClient(transport=transport) as client:
        return [item async for item in fetcher._run(client, **kwargs)]


def _timeline_payload():
    return {
        "data": [
            {
                "id": "200",
                "text": "short fallback",
                "note_tweet": {"text": "Long note first line\nsecond line"},
                "author_id": "1",
                "conversation_id": "200",
                "created_at": "2026-07-20T05:00:00.000Z",
                "lang": "en",
                "attachments": {"media_keys": ["m1"]},
                "referenced_tweets": [{"type": "quoted", "id": "150"}],
                "entities": {"hashtags": [{"tag": "AI"}, {"tag": "Agents"}]},
                "public_metrics": {"like_count": 42, "retweet_count": 7},
            },
            {
                "id": "199",
                "text": "RT @builder: useful demo",
                "author_id": "1",
                "conversation_id": "199",
                "created_at": "2026-07-20T04:00:00.000Z",
                "lang": "en",
                "referenced_tweets": [{"type": "retweeted", "id": "140"}],
                "public_metrics": {"like_count": 0, "retweet_count": 12},
            },
        ],
        "includes": {
            "users": [
                {"id": "1", "username": "tester", "name": "Test User", "profile_image_url": "https://img/u.jpg"},
                {
                    "id": "2", "username": "quoted", "name": "Quoted User",
                    "profile_image_url": "https://img/quoted_normal.jpg",
                },
                {
                    "id": "3", "username": "builder", "name": "Builder",
                    "profile_image_url": "https://img/builder_normal.png",
                },
            ],
            "tweets": [
                {
                    "id": "150",
                    "text": "quoted truncated",
                    "note_tweet": {"text": "Quoted full note body"},
                    "author_id": "2",
                    "attachments": {"media_keys": ["m3"]},
                },
                {
                    "id": "140",
                    "text": "original truncated",
                    "note_tweet": {"text": "Original full note body"},
                    "author_id": "3",
                    "attachments": {"media_keys": ["m2"]},
                },
            ],
            "media": [
                {"media_key": "m1", "type": "photo", "url": "https://img/own.jpg"},
                {"media_key": "m2", "type": "video", "preview_image_url": "https://img/video.jpg"},
                {"media_key": "m3", "type": "photo", "url": "https://img/quote.jpg"},
            ],
        },
        "meta": {"result_count": 2, "newest_id": "200", "oldest_id": "199"},
    }


def test_presets_are_registered_with_social_contract():
    metadata = {item["id"]: item for item in fetcher_registry.get_all_metadata()}
    assert fetcher_registry.get_class("generic_x_timeline") is XTimelineFetcher
    for source_id, (fetcher_class, handle) in PRESETS.items():
        assert fetcher_registry.get_class(source_id) is fetcher_class
        assert fetcher_class.handle == handle
        assert fetcher_class.user_id.isdigit()
        assert fetcher_class.content_type == "social_post"
        assert fetcher_class.content_shape == "social"
        assert fetcher_class.platform == "x"
        assert fetcher_class.category == "incubating"
        assert fetcher_class.is_template is False
        assert metadata[source_id]["shape"] == "social"
        assert metadata[source_id]["platform"] == "x"
        assert metadata[source_id]["category"] == "incubating"
        assert metadata[source_id]["default_visible"] is True


def test_x_api_mapping_uses_note_text_references_media_and_quota(tmp_path):
    sink = _sink(tmp_path)
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer test-bearer-secret"
        if request.url.path.endswith("/users/by/username/tester"):
            return httpx.Response(
                200,
                json={"data": {"id": "1", "username": "tester", "name": "Test User"}},
            )
        assert request.url.path.endswith("/users/1/tweets")
        assert request.url.params["exclude"] == "replies"
        assert request.url.params["max_results"] == "5"
        assert "referenced_tweets.id" in request.url.params["expansions"]
        return httpx.Response(200, json=_timeline_payload())

    fetcher = XTimelineFetcher(
        x_config=_x_config(), runtime_engine=sink.engine, max_retries=1
    )
    items = asyncio.run(
        _run(
            fetcher,
            httpx.MockTransport(handler),
            source_id="x_tester",
            handle="tester",
            limit=100,  # 配置硬上限仍压到 5
        )
    )

    assert len(requests) == 2
    assert [item.id for item in items] == ["x_tester_200", "x_tester_199"]
    first, repost = items
    assert first.content == "Long note first line\nsecond line"
    assert first.title == "Long note first line"
    assert first.content_format == "txt"
    assert first.author_id == "1" and first.author_handle == "tester"
    assert first.post_id == "200" and first.conversation_id == "200"
    assert first.quoted_post_id == "150" and not first.reposted_post_id
    assert first.tags == ["AI", "Agents"]
    assert first.media_urls == ["https://img/own.jpg"]
    assert first.metrics["like_count"] == 42
    assert first.author_avatar_url == "https://img/u.jpg"
    assert first.author_avatar_url_large == "https://img/u.jpg"
    assert first.raw_data["data"]["id"] == "200"
    assert first._cursor_value == "200"
    first_extensions = serialize_to_metadata(first)["extensions"]
    assert first_extensions["quoted"] == {
        "author_name": "Quoted User",
        "author_handle": "quoted",
        "author_avatar_url": "https://img/quoted_normal.jpg",
        "author_avatar_url_large": "https://img/quoted_400x400.jpg",
        "text": "Quoted full note body",
        "url": "https://x.com/quoted/status/150",
        "media_urls": ["https://img/quote.jpg"],
    }
    assert "reposted" not in first_extensions

    assert repost.reposted_post_id == "140"
    # 转推作者契约：顶层是时间线账号/转推者，reposted 才是原作者。
    assert (repost.author_name, repost.author_handle) == ("Test User", "tester")
    repost_extensions = serialize_to_metadata(repost)["extensions"]
    assert repost_extensions["reposted"] == {
        "author_name": "Builder",
        "author_handle": "builder",
        "author_avatar_url": "https://img/builder_normal.png",
        "author_avatar_url_large": "https://img/builder_400x400.png",
        "text": "Original full note body",  # 不用顶层 RT 截断文本
        "url": "https://x.com/builder/status/140",
        "media_urls": ["https://img/video.jpg"],
    }
    assert "quoted" not in repost_extensions
    assert repost.content == "RT @builder: useful demo"
    assert repost.media_urls == ["https://img/video.jpg"]
    assert "https://img/own.jpg" not in repost.content

    assert asyncio.run(sink.save(first)) is True
    assert asyncio.run(sink.save(first)) is False  # source_id + post_id 复合主键幂等

    snapshot = XApiQuotaGuard(sink.engine, monthly_budget_usd=5).snapshot()
    assert snapshot.post_reads == 4  # 2 主帖 + 2 includes.tweets
    assert snapshot.media_reads == 3
    assert snapshot.note_reads == 3  # 顶层 + 引用 + 转推原帖均返回 note_tweet
    assert snapshot.user_reads == 3  # lookup 用户与 includes 同日去重
    assert snapshot.estimated_cost_usd == pytest.approx(0.08)
    assert snapshot.by_source["x_tester"]["estimated_cost_usd"] == pytest.approx(0.08)
    with Session(sink.engine) as session:
        values = [row.value for row in session.exec(select(AppSettingRecord)).all()]
    assert values and all("test-bearer-secret" not in value for value in values)


def test_since_id_is_read_from_source_state(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add(
            SourceStateRecord(
                source_id="x_tester",
                fetcher_id="x_tester",
                last_cursor_value="190",
                created_at="2026-07-20T00:00:00",
                updated_at="2026-07-20T00:00:00",
            )
        )
        session.commit()

    def handler(request: httpx.Request):
        assert request.url.params["since_id"] == "190"
        return httpx.Response(200, json={"meta": {"result_count": 0}})

    fetcher = XTimelineFetcher(
        x_config=_x_config(), runtime_engine=sink.engine, max_retries=1
    )
    items = asyncio.run(
        _run(
            fetcher,
            httpx.MockTransport(handler),
            source_id="x_tester",
            handle="tester",
            user_id="1",
        )
    )
    assert items == []


def test_user_id_cache_skips_second_lookup_and_handle_change_re_resolves(tmp_path):
    sink = _sink(tmp_path, "user-cache.db")
    paths = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        if "/users/by/username/" in request.url.path:
            handle = request.url.path.rsplit("/", 1)[-1]
            user_id = "77" if handle == "cached_ai" else "88"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": user_id,
                        "username": handle,
                        "name": handle.title(),
                        "profile_image_url": f"https://img/{handle}_normal.jpg",
                    }
                },
            )
        user_id = request.url.path.split("/users/", 1)[1].split("/", 1)[0]
        handle = "cached_ai" if user_id == "77" else "renamed_ai"
        return httpx.Response(
            200,
            json={
                "includes": {
                    "users": [{"id": user_id, "username": handle, "name": handle.title()}]
                },
                "meta": {"result_count": 0},
            },
        )

    fetcher = XTimelineFetcher(
        x_config=_x_config(), runtime_engine=sink.engine, max_retries=1
    )
    transport = httpx.MockTransport(handler)
    assert asyncio.run(_run(fetcher, transport, source_id="x_configured", handle="cached_ai")) == []
    assert asyncio.run(_run(fetcher, transport, source_id="x_configured", handle="cached_ai")) == []
    # handle 变更使原缓存失效，重新解析并替换稳定 ID。
    assert asyncio.run(_run(fetcher, transport, source_id="x_configured", handle="renamed_ai")) == []

    assert paths.count("/2/users/by/username/cached_ai") == 1
    assert paths.count("/2/users/77/tweets") == 2
    assert paths.count("/2/users/by/username/renamed_ai") == 1
    assert paths.count("/2/users/88/tweets") == 1
    with Session(sink.engine) as session:
        cached = read_user_cache(session, "x_configured", handle="renamed_ai")
    assert cached and cached["user_id"] == "88"


def test_pipeline_cursor_keeps_composite_content_id_but_persists_numeric_cursor(
    monkeypatch, tmp_path
):
    import api.app as app_module
    from pipeline.core import DataPipeline

    sink = _sink(tmp_path, "cursor.db")
    with Session(sink.engine) as session:
        session.add(
            SourceStateRecord(
                source_id="x_tester",
                fetcher_id="x_tester",
                created_at="2026-07-20T00:00:00",
                updated_at="2026-07-20T00:00:00",
            )
        )
        session.commit()

    item = XTimelineFetcher(
        x_config=_x_config(), runtime_engine=sink.engine
    )._content_for_post(
        {
            "id": "250",
            "text": "cursor post",
            "author_id": "1",
            "created_at": "2026-07-20T06:00:00Z",
        },
        runtime_source_id="x_tester",
        fallback_handle="tester",
        fallback_user={"id": "1", "username": "tester", "name": "Tester"},
        payload={},
    )

    class YieldFetcher:
        source_id = "x_tester"
        dedup_lookup = None

        def bind_runtime_engine(self, engine):
            self.engine = engine

        async def fetch(self, **kwargs):
            item.source_id = self.source_id
            item.content_type = "social_post"
            yield item

    result = asyncio.run(DataPipeline([sink]).run_task(YieldFetcher()))
    assert result.latest_content_id == "x_tester_250"
    assert result.latest_cursor_value == "250"

    monkeypatch.setattr(app_module, "db_sink", sink)
    app_module.mark_source_state_finished(
        "x_tester", {}, 1, status="success", result=result
    )
    with Session(sink.engine) as session:
        state = session.get(SourceStateRecord, "x_tester")
        assert state.last_content_id == "x_tester_250"
        assert state.last_cursor_value == "250"


def test_quota_guard_stops_before_network_at_budget(tmp_path):
    sink = _sink(tmp_path, "quota.db")
    guard = XApiQuotaGuard(sink.engine, monthly_budget_usd=0.025)
    guard.record_response(
        {"data": [{"id": str(i)} for i in range(5)]}, primary_resource="post"
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    fetcher = XTimelineFetcher(
        x_config=_x_config(monthly_budget_usd=0.025),
        runtime_engine=sink.engine,
        max_retries=1,
    )
    with pytest.raises(XApiQuotaExceeded, match="配额"):
        asyncio.run(
            _run(
                fetcher,
                httpx.MockTransport(handler),
                source_id="x_tester",
                handle="tester",
                user_id="1",
            )
        )
    assert calls == []


def test_transient_x_error_retries_with_backoff(monkeypatch, tmp_path):
    import fetchers.impl.x_timeline_fetcher as x_module

    sink = _sink(tmp_path, "retry.db")
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"title": "temporary"})
        return httpx.Response(200, json={"meta": {"result_count": 0}})

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(x_module.asyncio, "sleep", no_wait)
    fetcher = XTimelineFetcher(
        x_config=_x_config(), runtime_engine=sink.engine, max_retries=2
    )
    items = asyncio.run(
        _run(
            fetcher,
            httpx.MockTransport(handler),
            source_id="x_tester",
            handle="tester",
            user_id="1",
        )
    )
    assert items == []
    assert len(calls) == 2


def test_bearer_token_env_overrides_ini(monkeypatch, tmp_path):
    import config

    ini = tmp_path / "x.ini"
    ini.write_text(
        "[x_api]\nbearer_token = ini-secret\nmax_results = 7\nmonthly_budget_usd = 4.5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    monkeypatch.setenv("DORAMI_X_BEARER_TOKEN", "env-secret")
    loaded = config.load_config()
    assert loaded.x_api.bearer_token == "env-secret"
    assert loaded.x_api.max_results == 7
    assert loaded.x_api.monthly_budget_usd == 4.5


def test_source_config_routes_handle_to_generic_x_template(tmp_path):
    import api.app as app_module
    from models.db import SourceConfigRecord

    record = SourceConfigRecord(
        source_id="x_configured_lab",
        name="Configured Lab",
        source_type="x_timeline",
        url="https://x.com/configured_lab",
        category="incubating",
        params_json='{"handle":"configured_ai","limit":5}',
        created_at="2026-07-20T00:00:00",
        updated_at="2026-07-20T00:00:00",
    )

    assert app_module.resolve_source_fetcher_id(record) == "generic_x_timeline"
    params = app_module.build_source_fetch_params(record)
    assert params["source_id"] == "x_configured_lab"
    assert params["handle"] == "configured_ai"  # params.handle 优先于 url 兜底
    assert "feed_url" not in params and "listing_url" not in params
    assert app_module.serialize_source_config(record)["shape"] == "social"
    assert app_module.serialize_source_config(record)["platform"] == "x"

    requested_paths = []

    def handler(request: httpx.Request):
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/users/by/username/configured_ai"):
            return httpx.Response(
                200,
                json={"data": {"id": "77", "username": "configured_ai", "name": "Configured AI"}},
            )
        assert request.url.path.endswith("/users/77/tweets")
        return httpx.Response(200, json={"meta": {"result_count": 0}})

    fetcher = XTimelineFetcher(
        x_config=_x_config(), runtime_engine=_sink(tmp_path, "configured.db").engine,
        max_retries=1,
    )
    assert asyncio.run(_run(fetcher, httpx.MockTransport(handler), **params)) == []
    assert requested_paths == [
        "/2/users/by/username/configured_ai",
        "/2/users/77/tweets",
    ]
