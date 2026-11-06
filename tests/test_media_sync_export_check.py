"""媒体流导出端自检(issue #264)。

manifest 只列接收端装得上的图片:内容认不出的缓存标 failed 并跳过;缺文件 / 大小不符且无 OSS 登记的
只跳过不动(重下即推进 revision 补发;手工恢复文件后靠重置 media 单流);会不经写库自愈的状态
(读盘错误、OSS 登记对不上、媒体库关闭)让整页失败、checkpoint 不动。翻页不漏行、不出「空且未完」页。
"""
import asyncio
import functools
import hashlib
import json
import pathlib
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from models.db import MediaAssetRecord, ObjectBlobRecord
from services import archive_sync_v2 as sync, media_store as ms, remote_sync, sync_console
from services.media_store import MediaStore
from services.object_storage import ObjectStorage
from tests.test_archive_sync_v2 import _V2Remote, _article, _sink, _source
from tests.test_object_storage import FakeBucket, oss_config, remote_store

BASE_URL = "https://remote.test"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body" * 4
# issue #264 实拍:网易订阅 CDN 以 image/png 头返回的 194 字节 data URL 文本
JUNK = b"data:image/png;base64," + b"iVBORw0KGgo" * 16


def _key(url):
    return hashlib.sha256(url.encode()).hexdigest()


def _body(index, kind):
    return JUNK if kind == "junk" else JPEG + bytes([index])


def _path(root, body, ext=".jpg"):
    digest = hashlib.sha256(body).hexdigest()
    return root / digest[:2] / f"{digest}{ext}"


def _seed(engine, root, specs):
    """specs: [(url, kind)];kind ∈ good / junk / missing / short。返回 {kind: [url_hash]}。"""
    by_kind = {}
    with Session(engine) as session:
        session.add(_source())
        session.add(_article(content="\n".join(f"![i]({url})" for url, _ in specs)))
        for index, (url, kind) in enumerate(specs):
            body = _body(index, kind)
            mime, ext = ("image/png", ".png") if kind == "junk" else ("image/jpeg", ".jpg")
            if kind != "missing":
                path = _path(root, body, ext)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body[:-1] if kind == "short" else body)
            session.add(MediaAssetRecord(
                url_hash=_key(url), url=url, status="cached",
                content_hash=hashlib.sha256(body).hexdigest(),
                mime=mime, ext=ext, size_bytes=len(body),
                created_at="2026-07-20T12:04:00", updated_at="2026-07-20T12:04:00",
            ))
            by_kind.setdefault(kind, []).append(_key(url))
        session.commit()
    return by_kind


def _check(root, object_storage=None):
    return functools.partial(sync.media_export_verdict, media_root=root, object_storage=object_storage)


def _page(engine, check, **kwargs):
    raw = sync.export_page(engine, "media", media_check=check, **kwargs)
    lines = [json.loads(line) for line in raw.splitlines()]
    return lines[0], lines[1:]


def _row(engine, key):
    with Session(engine) as session:
        return session.get(MediaAssetRecord, key)


# ── 判定与翻页 ──

def test_unrecognizable_cache_is_marked_failed_and_never_listed(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    kinds = _seed(producer.engine, root, [
        ("https://img.test/good.jpg", "good"),
        ("http://dingyue.ws.126.net/2026/0710/a85abdb0j00thyd5g001qd000u000bwm.jpg", "junk"),
        ("https://img.test/missing.jpg", "missing"),
        ("https://img.test/short.jpg", "short"),
    ])

    manifest, rows = _page(producer.engine, _check(root))

    assert [row["identity"] for row in rows] == kinds["good"]
    assert manifest["complete"] is True
    assert manifest["pending"] == 4  # 首页分母不逐个读盘,含将被跳过的行
    junk = _row(producer.engine, kinds["junk"][0])
    assert junk.status == "failed" and "自检" in junk.last_error and junk.fail_count == 1
    assert junk.updated_at == junk.updated_at.split("+")[0]  # 与 media_store 其它写入同一无时区格式
    # 缺文件 / 大小不符且无 OSS 登记:只跳过、不改行
    for key in kinds["missing"] + kinds["short"]:
        assert _row(producer.engine, key).status == "cached"
    manifest, rows = _page(producer.engine, _check(root))
    assert manifest["pending"] == 3  # 已标 failed 的坏图不再是候选


def test_unchecked_listing_is_unchanged_for_callers_without_media_store(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    kinds = _seed(producer.engine, root, [("https://img.test/j.jpg", "junk")])
    _, rows = _page(producer.engine, None)
    assert [row["identity"] for row in rows] == kinds["junk"]
    assert _row(producer.engine, kinds["junk"][0]).status == "cached"


# 扩展名是 CAS 文件名的一部分,改扩展名等于找不到文件(归缺文件);这里只测 MIME 与不安全扩展名。
@pytest.mark.parametrize("field,value", [("mime", "image/png"), ("mime", ""), ("ext", "../x")])
def test_declaration_must_agree_with_content(tmp_path, field, value):
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    kinds = _seed(producer.engine, root, [("https://img.test/a.jpg", "good")])
    with Session(producer.engine) as session:
        row = session.get(MediaAssetRecord, kinds["good"][0])
        setattr(row, field, value)
        session.add(row)
        session.commit()
    _, rows = _page(producer.engine, _check(root))
    assert rows == []
    assert _row(producer.engine, kinds["good"][0]).status == "failed"


def test_paging_skips_bad_rows_without_losing_or_stalling(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    specs = [(f"https://img.test/{i}.jpg", "junk" if i % 2 else "good") for i in range(8)]
    kinds = _seed(producer.engine, root, specs)

    exported, snapshot, after = [], "", ""
    for _ in range(20):
        manifest, rows = _page(producer.engine, _check(root), snapshot=snapshot, after=after, limit=1)
        assert rows or manifest["complete"], "空且未完的页会让接收端原地打转"
        exported += [row["identity"] for row in rows]
        snapshot, after = manifest["snapshot"], manifest["next_cursor"]
        if manifest["complete"]:
            break
    assert sorted(exported) == sorted(kinds["good"])
    assert all(_row(producer.engine, key).status == "failed" for key in kinds["junk"])


def test_filesystem_errors_fail_the_page_instead_of_skipping(tmp_path, monkeypatch):
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    kinds = _seed(producer.engine, root, [("https://img.test/a.jpg", "good")])
    original = pathlib.Path.open

    def denied(self, *args, **kwargs):
        if self.parent.parent == root:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", denied)
    with pytest.raises(sync.SyncV2Error, match="unreadable"):
        _page(producer.engine, _check(root))
    assert _row(producer.engine, kinds["good"][0]).status == "cached"


# ── OSS 冷副本:只认有效登记 ──

def _registered(engine, root, key):
    """真实 ObjectStorage(FakeBucket)上传登记该行字节,再删掉本地工作副本。"""
    storage = remote_store(engine, root, "media", FakeBucket())
    row = _row(engine, key)
    path = root / row.content_hash[:2] / f"{row.content_hash}{row.ext}"
    storage.persist(path, row.content_hash, row.ext, row.size_bytes, row.mime)
    return storage, path


def test_cold_copy_is_trusted_only_with_a_matching_registration(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    kinds = _seed(producer.engine, root, [
        ("https://img.test/cold.jpg", "good"), ("https://img.test/none.jpg", "missing"),
    ])
    cold, missing = kinds["good"][0], kinds["missing"][0]
    storage, path = _registered(producer.engine, root, cold)
    path.unlink()

    _, rows = _page(producer.engine, _check(root, storage))
    assert [row["identity"] for row in rows] == [cold]  # 无登记的缺文件行跳过、不改行
    assert _row(producer.engine, missing).status == "cached"
    # 本地被截断 + 有效登记:二进制端点会从 OSS 恢复,照常列入,且真能读出完整字节
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8")
    _, rows = _page(producer.engine, _check(root, storage))
    assert [row["identity"] for row in rows] == [cold]
    store = MediaStore(producer.engine, root, object_storage=storage)
    assert store.readable_path(_row(producer.engine, cold)).read_bytes() == _body(0, "good")


@pytest.mark.parametrize("damage", ["size", "bucket", "disabled"])
def test_unusable_registration_fails_the_page(tmp_path, damage):
    """登记在但对不上:读者路径因登记存在而不回源,修好配置也不写库——不能静默跳过。"""
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    kinds = _seed(producer.engine, root, [("https://img.test/cold.jpg", "good")])
    storage, path = _registered(producer.engine, root, kinds["good"][0])
    path.unlink()
    if damage == "disabled":
        storage = ObjectStorage(producer.engine, root, "media",
                                replace(oss_config(), media_backend="local"))
    else:
        with Session(producer.engine) as session:
            blob = session.exec(select(ObjectBlobRecord)).one()
            if damage == "size":
                blob.size_bytes += 1
            else:
                blob.bucket = "another-bucket"
            session.add(blob)
            session.commit()
    with pytest.raises(sync.SyncV2Error, match="registration"):
        _page(producer.engine, _check(root, storage))


# ── 端到端:接收端整流跑完 + 各恢复路径 ──

class _CheckedRemote(_V2Remote):
    """新版导出端:media 页带自检(导出失败按真实端点转 400),二进制按端点同口径经 readable_path 供出。"""

    def __init__(self, producer, store):
        super().__init__(producer)
        self.store = store
        self.check = _check(store.root, store.object_storage)
        self.binary_requests = []

    def handler(self, request):
        path = request.url.path
        if path == "/api/archive/v2/export/media.jsonl":
            params = dict(request.url.params)
            try:
                text = sync.export_page(
                    self.producer.engine, "media", snapshot=params.get("snapshot", ""),
                    since=params.get("since", ""), after=params.get("after", ""),
                    limit=int(params.get("limit", 1000)), media_check=self.check,
                )
            except sync.SyncV2Error as exc:
                return httpx.Response(400, json={"detail": str(exc)})
            return httpx.Response(200, text=text)
        if path.startswith("/api/archive/v2/media/"):
            key = path.rsplit("/", 1)[-1]
            self.binary_requests.append(key)
            return httpx.Response(200, content=self.store.readable_path(_row(self.producer.engine, key)).read_bytes())
        return super().handler(request)


def _pull(consumer, remote, media_root):
    """与生产同形:checkpoint 落 KV(`record_v2_stream_success`),sync_console.reset_stream 可直接作用。"""
    with Session(consumer.engine) as session:
        remote_sync.prepare_transaction_revision_consumer(
            session, base_url=BASE_URL, username="admin",
            authority_id=sync.producer_authority_id(remote.producer.engine),
            schema_version=sync.SCHEMA_VERSION, prepared_at="2026-10-12T00:00:00+00:00",
            capabilities=list(sync.ADVERTISED_CAPABILITIES),
        )
        session.commit()
    target = remote_sync.load_sync_state(consumer.engine)["targets"][BASE_URL]
    return asyncio.run(remote_sync.run_pull_v2(
        engine=consumer.engine, base_url=BASE_URL, username="admin", password="secret",
        media_root=media_root, checkpoints=target.get("v2_streams") or {},
        on_stream_complete=lambda stream, cp: remote_sync.record_v2_stream_success(
            consumer.engine, base_url=BASE_URL, username="admin", stream=stream,
            checkpoint=cp, synced_at="2026-10-12T00:00:00+00:00",
        ),
        transport=httpx.MockTransport(remote.handler), push_candidate_evidence=False,
    ))


def _done_streams(consumer):
    return set(remote_sync.load_sync_state(consumer.engine)["targets"][BASE_URL].get("v2_streams") or {})


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_sync, "_MAX_RETRIES", 1)
    monkeypatch.setattr(ms, "_resolve_is_public", lambda host: asyncio.sleep(0, result=True))
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    root = tmp_path / "producer-media"
    origin = {}  # 源站当前返回的字节(回填 / 读者取图重下用)
    transport = httpx.MockTransport(lambda req: httpx.Response(
        200, content=origin[str(req.url)], headers={"content-type": "image/png"}))
    store = MediaStore(producer.engine, root, transport=transport)
    remote = _CheckedRemote(producer, store)
    return producer, consumer, root, origin, store, remote, tmp_path / "consumer-media"


def test_checked_exporter_lets_receiver_finish_every_stream(pair):
    producer, consumer, root, _, _, remote, consumer_media = pair
    kinds = _seed(producer.engine, root, [
        ("https://img.test/good.jpg", "good"),
        ("http://dingyue.ws.126.net/2026/0710/junk.jpg", "junk"),
    ])
    result = _pull(consumer, remote, consumer_media)
    assert {"media", "source_states"} <= _done_streams(consumer)
    assert result["streams"]["media"]["skipped"] == 0  # 坏图根本没出权威,接收端无需拒收
    assert remote.binary_requests == kinds["good"]
    assert _row(consumer.engine, kinds["good"][0]).status == "cached"
    assert _row(consumer.engine, kinds["junk"][0]) is None


def test_disabled_media_store_holds_checkpoint_until_reenabled(pair):
    producer, consumer, root, _, store, remote, consumer_media = pair
    kinds = _seed(producer.engine, root, [("https://img.test/good.jpg", "good")])
    from api.routers.archive_sync import _media_store_disabled
    remote.check = _media_store_disabled
    with pytest.raises(remote_sync.RemoteSyncError):
        _pull(consumer, remote, consumer_media)
    assert "articles" in _done_streams(consumer) and "media" not in _done_streams(consumer)
    remote.check = _check(root)  # 重开媒体库:同一 since 补齐
    _pull(consumer, remote, consumer_media)
    assert "source_states" in _done_streams(consumer)
    assert _row(consumer.engine, kinds["good"][0]).status == "cached"


@pytest.mark.parametrize("kind", ["missing", "short"])
def test_skipped_cache_is_resent_after_backfill_redownload(pair, kind):
    producer, consumer, root, origin, store, remote, consumer_media = pair
    url = f"https://img.test/{kind}.jpg"
    kinds = _seed(producer.engine, root, [(url, kind)])
    key = kinds[kind][0]
    _pull(consumer, remote, consumer_media)
    assert "media" in _done_streams(consumer) and _row(consumer.engine, key) is None

    origin[url] = _body(0, kind)  # 回填 / 读者取图:缺文件或截断都当未命中,重下并覆写坏 CAS 文件
    assert asyncio.run(store.prefetch_urls([url])) == {"cached": 1, "failed": 0}
    assert _path(root, origin[url]).read_bytes() == origin[url]
    _pull(consumer, remote, consumer_media)  # 增量:重下推进了 revision
    assert remote.binary_requests == [key]
    assert _row(consumer.engine, key).status == "cached"
    assert _path(consumer_media, origin[url]).read_bytes() == origin[url]


def test_hand_restored_file_is_resent_after_media_stream_reset(pair):
    producer, consumer, root, _, _, remote, consumer_media = pair
    url = "https://img.test/restored.jpg"
    kinds = _seed(producer.engine, root, [(url, "missing")])
    key = kinds["missing"][0]
    _pull(consumer, remote, consumer_media)
    path = _path(root, _body(0, "missing"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_body(0, "missing"))  # 手工恢复文件:不经数据库写
    _pull(consumer, remote, consumer_media)
    assert _row(consumer.engine, key) is None  # 增量看不到,符合方案:靠重置单流
    sync_console.reset_stream(consumer.engine, "media", by="admin", at="2026-11-06T00:00:00+00:00")
    _pull(consumer, remote, consumer_media)
    assert _row(consumer.engine, key).status == "cached"


def test_export_route_passes_media_store_check(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from tests.conftest import seed_default_accounts

    sink = _sink(tmp_path, "route.db")
    seed_default_accounts(sink.engine)
    root = tmp_path / "media"
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "settings",
                        replace(app_module.settings, runtime=RuntimeConfig(role="all")))
    monkeypatch.setattr(app_module, "media_store", None)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        # 媒体库关闭且没有候选:空 complete 页,不卡后续流
        response = client.get("/api/archive/v2/export/media.jsonl")
        assert response.status_code == 200 and len(response.text.splitlines()) == 1
        kinds = _seed(sink.engine, root, [
            ("https://img.test/good.jpg", "good"), ("https://img.test/junk.jpg", "junk"),
        ])
        # 媒体库关闭但有候选:整页失败,checkpoint 不前进
        assert client.get("/api/archive/v2/export/media.jsonl").status_code == 400
        monkeypatch.setattr(app_module, "media_store", MediaStore(sink.engine, root))
        response = client.get("/api/archive/v2/export/media.jsonl")
        assert response.status_code == 200
        assert [json.loads(line)["identity"] for line in response.text.splitlines()[1:]] == kinds["good"]
        assert _row(sink.engine, kinds["junk"][0]).status == "failed"
