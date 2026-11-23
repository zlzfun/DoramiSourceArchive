from __future__ import annotations

import hashlib
import asyncio
import json
import struct
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from config import PodcastArtifactStorageConfig, PodcastConfig
from models.db import (
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastSourceMediaSnapshotRecord,
)
from services.podcast_artifacts import PodcastArtifactStore
import datetime as dt
import inspect

from models.db import AppSettingRecord
from services import podcast_source_media as source_media
from services.podcast_source_media import (
    EnclosureSnapshot,
    REJECTION_MEMORY_KEY,
    SourceMediaConflict,
    SourceMediaTooLarge,
    SourceMediaTooLong,
    download_snapshot_media,
    remembered_rejection,
    validate_source_media,
)
from storage.impl.db_storage import DatabaseStorage


def _wav() -> bytes:
    pcm = b"\x00\x00" * 16
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8_000, 16_000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def _probe(*_args, **_kwargs):
    return subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps(
            {
                "streams": [{"codec_type": "audio", "duration": "0.002"}],
                "format": {"duration": "0.002"},
            }
        ),
        stderr="",
    )


def test_validation_is_idempotent_and_never_persists_source_bytes(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'source-media.db'}")
    raw_url = "http://audio.publisher.test/episode.wav?token=do-not-persist"
    payload = _wav()
    with Session(sink.engine) as session:
        session.add(
            ArticleRecord(
                id="episode-source-media",
                title="Episode",
                content_type="podcast_episode",
                source_id="podcast-source",
                source_url="https://publisher.test/episode",
                publish_date="2026-09-10T00:00:00+00:00",
                fetched_date="2026-09-10T00:00:00+00:00",
                content="notes",
                extensions_json=json.dumps(
                    {
                        "audio_url": raw_url,
                        "audio_mime": "audio/wav",
                        "audio_bytes": len(payload),
                    }
                ),
            )
        )
        session.commit()

    storage = PodcastArtifactStorageConfig(
        root_dir=str(tmp_path / "podcast-cas"),
        max_audio_mb=1,
        total_quota_bytes=2 * 1024 * 1024,
        minimum_free_bytes=0,
        allowed_mime_types=("audio/wav",),
        download_timeout_seconds=5,
        download_max_redirects=2,
        orphan_grace_seconds=0,
        staging_ttl_seconds=0,
    )
    store = PodcastArtifactStore(
        sink.engine,
        storage.root_dir,
        max_bytes=storage.max_audio_mb * 1024 * 1024,
        total_quota_bytes=storage.total_quota_bytes,
        minimum_free_bytes=storage.minimum_free_bytes,
        staging_ttl_seconds=storage.staging_ttl_seconds,
        allowed_mime_types=storage.allowed_mime_types,
        orphan_grace_seconds=storage.orphan_grace_seconds,
        probe_runner=_probe,
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav"},
            stream=httpx.ByteStream(payload),
        )

    def client_factory(*_args, **_kwargs):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )

    config = PodcastConfig(
        installation="external",
        authority_id="podcast-external-test",
        allowed_stages=("fetch", "asr"),
    )
    def validate():
        return asyncio.run(
            validate_source_media(
                sink.engine,
                store,
                episode_id="episode-source-media",
                podcast_config=config,
                storage_config=storage,
                max_audio_seconds_per_file=43_200,
                client_factory=client_factory,
            )
        )

    first = validate()
    second = validate()

    assert second == first
    assert requests == [raw_url, raw_url]
    assert first["locator_hash"] == hashlib.sha256(raw_url.encode()).hexdigest()
    assert first["content_hash"] == hashlib.sha256(payload).hexdigest()
    assert list((Path(storage.root_dir) / ".incoming").iterdir()) == []
    assert list(Path(storage.root_dir).glob("*/*")) == []
    with Session(sink.engine) as session:
        snapshots = session.exec(select(PodcastSourceMediaSnapshotRecord)).all()
        assert len(snapshots) == 1
        assert session.exec(select(PodcastArtifactRecord)).all() == []
        assert raw_url not in repr(snapshots[0])


def test_validation_rejects_overlong_audio_after_probe_without_persisting_snapshot(
    tmp_path,
):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'source-media-too-long.db'}")
    raw_url = "https://audio.publisher.test/episode.wav"
    payload = _wav()
    with Session(sink.engine) as session:
        session.add(
            ArticleRecord(
                id="episode-too-long",
                title="Long episode",
                content_type="podcast_episode",
                source_id="podcast-source",
                source_url="https://publisher.test/episode-too-long",
                publish_date="2026-09-10T00:00:00+00:00",
                fetched_date="2026-09-10T00:00:00+00:00",
                content="notes",
                extensions_json=json.dumps({"audio_url": raw_url}),
            )
        )
        session.commit()

    storage = PodcastArtifactStorageConfig(
        root_dir=str(tmp_path / "podcast-cas-too-long"),
        max_audio_mb=1,
        total_quota_bytes=2 * 1024 * 1024,
        minimum_free_bytes=0,
        allowed_mime_types=("audio/wav",),
    )

    def long_probe(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "streams": [{"codec_type": "audio", "duration": "2.0"}],
                    "format": {"duration": "2.0"},
                }
            ),
            stderr="",
        )

    store = PodcastArtifactStore(
        sink.engine,
        storage.root_dir,
        max_bytes=storage.max_audio_mb * 1024 * 1024,
        total_quota_bytes=storage.total_quota_bytes,
        minimum_free_bytes=0,
        staging_ttl_seconds=0,
        allowed_mime_types=storage.allowed_mime_types,
        orphan_grace_seconds=0,
        probe_runner=long_probe,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav"},
            stream=httpx.ByteStream(payload),
        )

    def client_factory(*_args, **_kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(SourceMediaTooLong, match="时长上限"):
        asyncio.run(
            validate_source_media(
                sink.engine,
                store,
                episode_id="episode-too-long",
                podcast_config=PodcastConfig(
                    installation="external",
                    authority_id="podcast-external-test",
                    allowed_stages=("fetch", "asr"),
                ),
                storage_config=storage,
                max_audio_seconds_per_file=1,
                client_factory=client_factory,
            )
        )

    assert list((Path(storage.root_dir) / ".incoming").iterdir()) == []
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastSourceMediaSnapshotRecord)).all() == []


# ── issue #323:订阅源声明的大小只是预留参考,不是下载上限 ─────────────────────


def _media_env(tmp_path, name: str, *, declared: int | None, max_bytes: int = 1024 * 1024):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / (name + '.db')}")
    extensions = {"audio_url": f"https://audio.publisher.test/{name}.wav", "audio_mime": "audio/wav"}
    if declared is not None:
        extensions["audio_bytes"] = declared
    with Session(sink.engine) as session:
        session.add(
            ArticleRecord(
                id=name, title="Episode", content_type="podcast_episode",
                source_id="podcast-source", source_url=f"https://publisher.test/{name}",
                publish_date="2026-09-10T00:00:00+00:00",
                fetched_date="2026-09-10T00:00:00+00:00",
                content="notes", extensions_json=json.dumps(extensions),
            )
        )
        session.commit()
    storage = PodcastArtifactStorageConfig(
        root_dir=str(tmp_path / f"cas-{name}"), max_audio_mb=1,
        total_quota_bytes=2 * 1024 * 1024, minimum_free_bytes=0,
        allowed_mime_types=("audio/wav",), download_timeout_seconds=5,
        download_max_redirects=2, orphan_grace_seconds=0, staging_ttl_seconds=0,
    )
    return sink, _store(sink, storage, max_bytes=max_bytes), storage


def _store(sink, storage, *, max_bytes: int = 1024 * 1024, seconds: str = "0.002"):
    def probe(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps(
                {"streams": [{"codec_type": "audio", "duration": seconds}],
                 "format": {"duration": seconds}}
            ),
            stderr="",
        )

    return PodcastArtifactStore(
        sink.engine, storage.root_dir, max_bytes=max_bytes,
        total_quota_bytes=storage.total_quota_bytes, minimum_free_bytes=0,
        staging_ttl_seconds=0, allowed_mime_types=storage.allowed_mime_types,
        orphan_grace_seconds=0, probe_runner=probe,
    )


def _memory(sink) -> dict:
    with Session(sink.engine) as session:
        record = session.get(AppSettingRecord, REJECTION_MEMORY_KEY)
        return json.loads(record.value) if record is not None else {}


def _serving(payload: bytes, *, content_length: bool):
    """一个只会回 ``payload`` 的发布方;记录每次请求以及当时产物库里的预留额。"""

    seen: list[int] = []

    def factory(store):
        def handler(_request: httpx.Request) -> httpx.Response:
            seen.append(sum(size for _path, size in store._download_reservations()))
            headers = {"Content-Type": "audio/wav"}
            if content_length:
                headers["Content-Length"] = str(len(payload))
            return httpx.Response(200, headers=headers, stream=httpx.ByteStream(payload))

        def client_factory(*_args, **_kwargs):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler), follow_redirects=False
            )

        return client_factory

    return seen, factory


def _validate(sink, store, storage, name, client_factory, *, max_seconds=43_200, **kwargs):
    config = PodcastConfig(
        installation="external", authority_id="podcast-external-test",
        allowed_stages=("fetch", "asr"),
    )
    return asyncio.run(
        validate_source_media(
            sink.engine, store, episode_id=name, podcast_config=config,
            storage_config=storage, max_audio_seconds_per_file=max_seconds,
            client_factory=client_factory, **kwargs,
        )
    )


def _nothing_left_behind(storage) -> bool:
    return list((Path(storage.root_dir) / ".incoming").iterdir()) == []


@pytest.mark.parametrize(
    ("content_length", "reservations"),
    [
        # 响应自报了长度:第一次在读响应头时就知道声明值不够,第二次按真实大小预留。
        (True, lambda declared, actual, cap: [declared, actual]),
        # 响应没有长度:流超过声明值才发现,第二次只能按配置上限预留。
        (False, lambda declared, actual, cap: [declared, cap]),
    ],
)
def test_a_file_larger_than_the_feed_declared_is_still_validated(
    tmp_path, content_length, reservations
):
    payload = _wav()
    declared = len(payload) - 20  # 订阅源写小了
    sink, store, storage = _media_env(tmp_path, "ep-understated", declared=declared)
    seen, factory = _serving(payload, content_length=content_length)

    snapshot = _validate(sink, store, storage, "ep-understated", factory(store))

    assert snapshot["size_bytes"] == len(payload)
    assert snapshot["content_hash"] == hashlib.sha256(payload).hexdigest()
    assert seen == reservations(declared, len(payload), store.max_bytes)
    assert _nothing_left_behind(storage)


def test_an_honest_feed_is_downloaded_once_with_the_declared_reservation(tmp_path):
    payload = _wav()
    sink, store, storage = _media_env(tmp_path, "ep-honest", declared=len(payload))
    seen, factory = _serving(payload, content_length=True)

    _validate(sink, store, storage, "ep-honest", factory(store))

    assert seen == [len(payload)]


@pytest.mark.parametrize(("content_length", "requests"), [(True, 1), (False, 2)])
def test_only_the_configured_cap_makes_a_file_too_large(tmp_path, content_length, requests):
    payload = _wav()
    cap = len(payload) - 1  # 真实文件确实超过配置上限
    sink, store, storage = _media_env(
        tmp_path, "ep-over-cap", declared=len(payload) - 30, max_bytes=cap
    )
    seen, factory = _serving(payload, content_length=content_length)

    with pytest.raises(SourceMediaTooLarge):
        _validate(sink, store, storage, "ep-over-cap", factory(store))

    # 响应自报的长度已经超过上限时,不必再下载一遍来确认。
    assert len(seen) == requests
    assert _nothing_left_behind(storage)
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastSourceMediaSnapshotRecord)).all() == []


def test_a_declared_size_over_the_cap_is_rejected_without_a_request(tmp_path):
    payload = _wav()
    sink, store, storage = _media_env(
        tmp_path, "ep-declared-huge", declared=10_000, max_bytes=len(payload) + 10
    )
    seen, factory = _serving(payload, content_length=True)

    with pytest.raises(SourceMediaTooLarge):
        _validate(sink, store, storage, "ep-declared-huge", factory(store))

    assert seen == []


def test_snapshot_redownload_reserves_the_verified_size_not_the_declared_one(tmp_path):
    """OSS 中转前的重新下载:手里有校验过的快照,订阅源写小了也不该再被拦。"""

    payload = _wav()
    declared = len(payload) - 20
    sink, store, storage = _media_env(tmp_path, "ep-relay", declared=declared)
    _seen, factory = _serving(payload, content_length=False)
    _validate(sink, store, storage, "ep-relay", factory(store))
    with Session(sink.engine) as session:
        expected = session.exec(select(PodcastSourceMediaSnapshotRecord)).one()
    enclosure = EnclosureSnapshot(
        url="https://audio.publisher.test/ep-relay.wav", mime="audio/wav",
        declared_bytes=declared,
    )

    seen, factory = _serving(payload, content_length=False)
    with download_snapshot_media(
        store, enclosure=enclosure, expected=expected, storage_config=storage,
        client_factory=factory(store),
    ) as media:
        assert media.size_bytes == len(payload)
    assert seen == [len(payload)]

    # 同一地址换成了更大的文件:这是内容变了,不是「超过配置上限」。
    grown = payload + b"\x00" * 8
    _seen, factory = _serving(grown, content_length=False)
    with pytest.raises(SourceMediaConflict):
        with download_snapshot_media(
            store, enclosure=enclosure, expected=expected, storage_config=storage,
            client_factory=factory(store),
        ):
            pass
    assert _nothing_left_behind(storage)


def test_a_response_length_is_trusted_only_once(tmp_path):
    """发布方每次都比我们的预留多报一个字节:不能陪它一轮一轮加上去。

    最多三轮——声明值、响应自报的长度(只采信一次)、配置上限。
    """

    payload = _wav()
    declared = len(payload) - 30
    sink, store, storage = _media_env(tmp_path, "ep-creeping", declared=declared)
    seen: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        reserved = sum(size for _path, size in store._download_reservations())
        seen.append(reserved)
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav", "Content-Length": str(reserved + 1)},
            stream=httpx.ByteStream(payload),
        )

    def client_factory(*_args, **_kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    with pytest.raises(SourceMediaTooLarge):
        _validate(sink, store, storage, "ep-creeping", client_factory)

    assert seen == [declared, declared + 1, store.max_bytes]
    assert _nothing_left_behind(storage)


# ── 量过的「过长 / 过大」不重复下载 ───────────────────────────────────────────


def test_a_measured_too_long_episode_is_not_downloaded_again_until_it_could_fit(
    tmp_path, monkeypatch
):
    """过长要把整个文件下载下来才量得出;量过一次,同一个地址、上限又容不下它,就不必再下。"""

    payload = _wav()
    sink, _store_unused, storage = _media_env(tmp_path, "ep-long", declared=len(payload))
    store = _store(sink, storage, seconds="9000.0")  # 量到 2.5 小时
    seen, factory = _serving(payload, content_length=True)
    client = factory(store)

    with pytest.raises(SourceMediaTooLong):
        _validate(sink, store, storage, "ep-long", client, max_seconds=7200)
    assert len(seen) == 1
    assert _memory(sink)["ep-long"]["kind"] == "too_long"
    assert _memory(sink)["ep-long"]["seconds"] == 9000.0

    # 第二天、第三天再来:同样的结论,但没有再发请求。
    for _ in range(3):
        with pytest.raises(SourceMediaTooLong):
            _validate(sink, store, storage, "ep-long", client, max_seconds=7200)
    assert len(seen) == 1
    # 上限调高了但仍容不下 2.5 小时:还是不必下载。
    with pytest.raises(SourceMediaTooLong):
        _validate(sink, store, storage, "ep-long", client, max_seconds=8999)
    assert len(seen) == 1
    # 管理员显式要求校验:重新执行一遍。
    with pytest.raises(SourceMediaTooLong):
        _validate(sink, store, storage, "ep-long", client, max_seconds=7200,
                  use_rejection_memory=False)
    assert len(seen) == 2

    # 上限调到容得下它:重新下载并通过;此前的拒绝随之清掉。
    snapshot = _validate(sink, store, storage, "ep-long", client, max_seconds=10800)
    assert len(seen) == 3 and snapshot["duration_seconds"] == 9000.0
    assert _memory(sink)["ep-long"]["kind"] == ""
    assert remembered_rejection(
        sink.engine, episode_id="ep-long",
        enclosure=EnclosureSnapshot(
            url="https://audio.publisher.test/ep-long.wav", mime="audio/wav",
            declared_bytes=len(payload),
        ),
        max_bytes=store.max_bytes, max_audio_seconds=7200,
    ) is None


def test_the_measurement_expires_and_a_hit_never_renews_it(tmp_path, monkeypatch):
    payload = _wav()
    sink, _unused, storage = _media_env(tmp_path, "ep-expiring", declared=len(payload))
    store = _store(sink, storage, seconds="9000.0")
    seen, factory = _serving(payload, content_length=True)
    client = factory(store)
    started = source_media._utc_now()
    clock = {"now": started}
    monkeypatch.setattr(source_media, "_utc_now", lambda: clock["now"])

    with pytest.raises(SourceMediaTooLong):
        _validate(sink, store, storage, "ep-expiring", client, max_seconds=7200)
    # 保留期内每隔几天命中一次——命中不续期。
    for days in (5, 12, 20, 29):
        clock["now"] = started + dt.timedelta(days=days)
        with pytest.raises(SourceMediaTooLong):
            _validate(sink, store, storage, "ep-expiring", client, max_seconds=7200)
    assert len(seen) == 1
    # 过了保留期:重新量一次(发布方可能在同一地址下换过文件)。
    clock["now"] = started + dt.timedelta(
        seconds=source_media.REJECTION_RETENTION_SECONDS + 60
    )
    with pytest.raises(SourceMediaTooLong):
        _validate(sink, store, storage, "ep-expiring", client, max_seconds=7200)
    assert len(seen) == 2


def test_a_new_audio_address_is_measured_afresh(tmp_path):
    payload = _wav()
    sink, _unused, storage = _media_env(tmp_path, "ep-moved", declared=len(payload))
    store = _store(sink, storage, seconds="9000.0")
    seen, factory = _serving(payload, content_length=True)
    client = factory(store)
    with pytest.raises(SourceMediaTooLong):
        _validate(sink, store, storage, "ep-moved", client, max_seconds=7200)

    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "ep-moved")
        extensions = json.loads(episode.extensions_json)
        extensions["audio_url"] = "https://audio.publisher.test/ep-moved-shorter.wav"
        episode.extensions_json = json.dumps(extensions)
        session.add(episode)
        session.commit()
    shorter = _store(sink, storage, seconds="3600.0")
    _validate(sink, shorter, storage, "ep-moved", factory(shorter), max_seconds=7200)
    assert len(seen) == 2


@pytest.mark.parametrize("content_length", [True, False])
def test_a_measured_too_large_file_is_remembered_as_a_lower_bound(tmp_path, content_length):
    payload = _wav()
    cap = len(payload) - 10
    sink, store, storage = _media_env(tmp_path, "ep-big", declared=None, max_bytes=cap)
    seen, factory = _serving(payload, content_length=content_length)

    with pytest.raises(SourceMediaTooLarge):
        _validate(sink, store, storage, "ep-big", factory(store))
    assert len(seen) == 1
    # 响应自报了长度就记它;没报、流到上限仍没完,只知道「比上限大」。
    known = len(payload) if content_length else cap + 1
    assert _memory(sink)["ep-big"] == {
        "locator_hash": hashlib.sha256(b"https://audio.publisher.test/ep-big.wav").hexdigest(),
        "kind": "too_large", "bytes": known,
        "fetched_at": _memory(sink)["ep-big"]["fetched_at"],
    }
    with pytest.raises(SourceMediaTooLarge):
        _validate(sink, store, storage, "ep-big", factory(store))
    assert len(seen) == 1

    # 上限放宽到「已知下界」本身时结论不再成立,必须重新校验——只有严格大于才拒绝。
    relaxed = _store(sink, storage, max_bytes=known)
    if content_length:
        _validate(sink, relaxed, storage, "ep-big", factory(relaxed))
    else:
        with pytest.raises(SourceMediaTooLarge):  # 真实文件仍比新上限大,但这是重新量出来的
            _validate(sink, relaxed, storage, "ep-big", factory(relaxed))
    assert len(seen) == 2


def test_a_wrong_declared_size_is_never_remembered(tmp_path):
    """声明值自己超限(不下载)与声明值偏小(换预留额重下)都不是量出来的拒绝。"""

    payload = _wav()
    sink, store, storage = _media_env(
        tmp_path, "ep-misdeclared", declared=10_000, max_bytes=len(payload) + 10
    )
    seen, factory = _serving(payload, content_length=True)
    with pytest.raises(SourceMediaTooLarge):
        _validate(sink, store, storage, "ep-misdeclared", factory(store))
    assert seen == [] and "ep-misdeclared" not in _memory(sink)

    # 订阅源把声明值改对了(地址没变):照常下载并通过。
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "ep-misdeclared")
        extensions = json.loads(episode.extensions_json)
        extensions["audio_bytes"] = len(payload) - 20  # 这次写小了
        episode.extensions_json = json.dumps(extensions)
        session.add(episode)
        session.commit()
    _validate(sink, store, storage, "ep-misdeclared", factory(store))
    assert seen == [len(payload) - 20, len(payload)]
    assert _memory(sink)["ep-misdeclared"]["kind"] == ""


def test_older_evidence_never_overwrites_newer_evidence(tmp_path):
    """A 先开始、下载到旧的长音频;B 后开始、量到新文件并通过;A 晚结束,不能把旧结论写回来。"""

    payload = _wav()
    sink, store, storage = _media_env(tmp_path, "ep-raced", declared=len(payload))
    enclosure = EnclosureSnapshot(
        url="https://audio.publisher.test/ep-raced.wav", mime="audio/wav",
        declared_bytes=len(payload),
    )
    now = source_media._utc_now()
    earlier, later = now - dt.timedelta(seconds=40), now - dt.timedelta(seconds=5)
    limits = dict(max_bytes=store.max_bytes, max_audio_seconds=7200)

    source_media._record_evidence(
        sink.engine, episode_id="ep-raced", enclosure=enclosure, fetched_at=later
    )  # B:通过
    source_media._record_evidence(
        sink.engine, episode_id="ep-raced", enclosure=enclosure, fetched_at=earlier,
        kind="too_long", seconds=9000.0,
    )  # A:过时的拒绝
    assert _memory(sink)["ep-raced"]["kind"] == ""
    assert remembered_rejection(
        sink.engine, episode_id="ep-raced", enclosure=enclosure, **limits
    ) is None

    # 反过来,更新的拒绝当然盖掉更早的通过。
    source_media._record_evidence(
        sink.engine, episode_id="ep-raced", enclosure=enclosure, fetched_at=now,
        kind="too_long", seconds=9000.0,
    )
    assert isinstance(
        remembered_rejection(sink.engine, episode_id="ep-raced", enclosure=enclosure, **limits),
        SourceMediaTooLong,
    )


def test_passing_watermarks_are_short_lived_and_rejections_long_lived(tmp_path, monkeypatch):
    payload = _wav()
    sink, store, storage = _media_env(tmp_path, "ep-a", declared=len(payload))
    enclosure = EnclosureSnapshot(url="https://x.test/a.wav", mime="audio/wav", declared_bytes=1)
    started = source_media._utc_now()
    clock = {"now": started}
    monkeypatch.setattr(source_media, "_utc_now", lambda: clock["now"])
    record = source_media._record_evidence
    record(sink.engine, episode_id="passed", enclosure=enclosure, fetched_at=started)
    record(sink.engine, episode_id="rejected", enclosure=enclosure, fetched_at=started,
           kind="too_long", seconds=9000.0)

    clock["now"] = started + dt.timedelta(seconds=source_media.VALIDATED_WATERMARK_SECONDS + 60)
    record(sink.engine, episode_id="other", enclosure=enclosure, fetched_at=clock["now"])
    assert set(_memory(sink)) == {"rejected", "other"}

    clock["now"] = started + dt.timedelta(seconds=source_media.REJECTION_RETENTION_SECONDS + 60)
    record(sink.engine, episode_id="another", enclosure=enclosure, fetched_at=clock["now"])
    assert set(_memory(sink)) == {"another"}


def test_a_failed_memory_write_is_logged_and_never_masks_the_verdict(tmp_path, captured_log):
    from sqlmodel import create_engine

    broken = create_engine("sqlite:////nonexistent-dir-323/memory.db")
    enclosure = EnclosureSnapshot(url="https://x.test/a.wav", mime="audio/wav", declared_bytes=1)
    with captured_log("dorami.podcast_source_media") as records:
        source_media._record_evidence(
            broken, episode_id="ep-x", enclosure=enclosure,
            fetched_at=source_media._utc_now(), kind="too_long", seconds=1.0,
        )  # 不抛
    assert any("没能记下" in record.getMessage() and "ep-x" in record.getMessage() for record in records)


def test_explicit_admin_validation_bypasses_the_memory():
    from api.routers import podcasts as podcasts_router

    source = inspect.getsource(podcasts_router)
    call = source[source.index("return await validate_source_media("):]
    assert "use_rejection_memory=False" in call[: call.index("except SourceMediaNotFound")]
