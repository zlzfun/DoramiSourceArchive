"""媒体流导出端自检(issue #264)。

manifest 只列接收端装得上的图片:内容认不出的缓存标 failed 并跳过,字节供不出的跳过不动;
翻页不漏行、不出「空且未完」的页;新导出端让(任意版本的)接收端整流跑完。
"""
import asyncio
import functools
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from models.db import MediaAssetRecord
from services import archive_sync_v2 as sync, remote_sync
from tests.test_archive_sync_v2 import _V2Remote, _article, _sink, _source

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body" * 4
# issue #264 实拍:网易订阅 CDN 以 image/png 头返回的 194 字节 data URL 文本
JUNK = b"data:image/png;base64," + b"iVBORw0KGgo" * 16


def _key(url):
    return hashlib.sha256(url.encode()).hexdigest()


def _seed(engine, root, specs):
    """specs: [(url, kind)];kind ∈ good / junk / missing / short。返回 {kind: [url_hash]}。"""
    by_kind = {}
    with Session(engine) as session:
        session.add(_source())
        session.add(_article(content="\n".join(f"![i]({url})" for url, _ in specs)))
        for index, (url, kind) in enumerate(specs):
            body = JUNK if kind == "junk" else JPEG + bytes([index])
            digest = hashlib.sha256(body).hexdigest()
            mime, ext = ("image/png", ".png") if kind == "junk" else ("image/jpeg", ".jpg")
            if kind != "missing":
                path = root / digest[:2] / f"{digest}{ext}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body[:-1] if kind == "short" else body)
            session.add(MediaAssetRecord(
                url_hash=_key(url), url=url, status="cached", content_hash=digest,
                mime=mime, ext=ext, size_bytes=len(body),
                created_at="2026-07-20", updated_at="2026-07-20",
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


def _status(engine, key):
    with Session(engine) as session:
        return session.get(MediaAssetRecord, key)


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
    junk = _status(producer.engine, kinds["junk"][0])
    assert junk.status == "failed" and "自检" in junk.last_error and junk.fail_count == 1
    # 字节供不出(缺文件且无 OSS / 大小不符)只跳过、不改行:修好后行变更会自然重发
    for key in kinds["missing"] + kinds["short"]:
        assert _status(producer.engine, key).status == "cached"
    # 已标 failed 的坏图不再是候选
    manifest, rows = _page(producer.engine, _check(root))
    assert manifest["pending"] == 3


def test_unchecked_listing_is_unchanged_for_callers_without_media_store(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    kinds = _seed(producer.engine, root, [("https://img.test/j.jpg", "junk")])
    _, rows = _page(producer.engine, None)
    assert [row["identity"] for row in rows] == kinds["junk"]
    assert _status(producer.engine, kinds["junk"][0]).status == "cached"


def test_missing_working_copy_is_trusted_when_oss_serves_cold_copy(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    root = tmp_path / "media"
    kinds = _seed(producer.engine, root, [("https://img.test/cold.jpg", "missing")])
    _, rows = _page(producer.engine, _check(root, SimpleNamespace(enabled=True)))
    assert [row["identity"] for row in rows] == kinds["missing"]
    _, rows = _page(producer.engine, _check(root, SimpleNamespace(enabled=False)))
    assert rows == []


# 扩展名是 CAS 文件名的一部分,改扩展名等于找不到文件(归「不可用」);这里只测 MIME 与不安全扩展名。
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
    assert _status(producer.engine, kinds["good"][0]).status == "failed"


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
    assert all(_status(producer.engine, key).status == "failed" for key in kinds["junk"])


class _CheckedRemote(_V2Remote):
    """新版导出端:media 页带自检,二进制从权威媒体根读。"""

    def __init__(self, producer, root):
        super().__init__(producer)
        self.root = root
        self.binary_requests = []

    def handler(self, request):
        path = request.url.path
        if path == "/api/archive/v2/export/media.jsonl":
            params = dict(request.url.params)
            return httpx.Response(200, text=sync.export_page(
                self.producer.engine, "media", snapshot=params.get("snapshot", ""),
                since=params.get("since", ""), after=params.get("after", ""),
                limit=int(params.get("limit", 1000)), media_check=_check(self.root),
            ))
        if path.startswith("/api/archive/v2/media/"):
            key = path.rsplit("/", 1)[-1]
            self.binary_requests.append(key)
            record = _status(self.producer.engine, key)
            return httpx.Response(200, content=(
                self.root / record.content_hash[:2] / f"{record.content_hash}{record.ext}"
            ).read_bytes())
        return super().handler(request)


def test_checked_exporter_lets_receiver_finish_every_stream(tmp_path, monkeypatch):
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    root = tmp_path / "producer-media"
    kinds = _seed(producer.engine, root, [
        ("https://img.test/good.jpg", "good"),
        ("http://dingyue.ws.126.net/2026/0710/junk.jpg", "junk"),
    ])
    remote = _CheckedRemote(producer, root)
    checkpoints = {}
    monkeypatch.setattr(remote_sync, "_MAX_RETRIES", 1)
    asyncio.run(remote_sync.run_pull_v2(
        engine=consumer.engine, base_url="https://remote.test", username="admin",
        password="test", media_root=tmp_path / "consumer-media",
        transport=httpx.MockTransport(remote.handler), push_candidate_evidence=False,
        checkpoints=checkpoints,
        on_stream_complete=lambda stream, cp: checkpoints.update({stream: cp}),
    ))
    assert {"media", "source_states"} <= set(checkpoints)
    assert checkpoints["media"]["skipped"] == 0  # 坏图根本没出权威,接收端无需拒收
    assert remote.binary_requests == kinds["good"]
    assert _status(consumer.engine, kinds["good"][0]).status == "cached"
    assert _status(consumer.engine, kinds["junk"][0]) is None


def test_export_route_passes_media_store_check(monkeypatch, tmp_path):
    import api.app as app_module
    from dataclasses import replace
    from config import RuntimeConfig
    from services.media_store import MediaStore
    from tests.conftest import seed_default_accounts

    sink = _sink(tmp_path, "route.db")
    seed_default_accounts(sink.engine)
    root = tmp_path / "media"
    kinds = _seed(sink.engine, root, [
        ("https://img.test/good.jpg", "good"), ("https://img.test/junk.jpg", "junk"),
    ])
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "settings",
                        replace(app_module.settings, runtime=RuntimeConfig(role="all")))
    monkeypatch.setattr(app_module, "media_store", MediaStore(sink.engine, root))

    def identities(client):
        response = client.get("/api/archive/v2/export/media.jsonl")
        assert response.status_code == 200
        return [json.loads(line)["identity"] for line in response.text.splitlines()[1:]]

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert identities(client) == kinds["good"]
        assert _status(sink.engine, kinds["junk"][0]).status == "failed"
        # 媒体库关闭:二进制端点一律 404,manifest 也一条不列
        monkeypatch.setattr(app_module, "media_store", None)
        assert identities(client) == []
