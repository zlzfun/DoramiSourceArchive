"""issue #263:节点不分角色之后的链式转发(A → B → C)与配套的顺序 / 重放保证。

这里的用例都走真正的拉取循环(``run_pull_v2``),不直接拼页:要验的正是「拉来的也导出」在
检查点、二进制阶段、依赖流重放这些环节上是否成立。
"""
import asyncio
import datetime as dt
import hashlib
import os
import sys

import httpx
import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    MediaAssetRecord,
    PodcastArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
    SourceStateRecord,
)
from services import archive_sync_v2  # noqa: E402
from services import remote_sync as remote_sync_service  # noqa: E402
from services.article_analysis import compute_content_hash, queue_article_analysis  # noqa: E402
from services.podcast_artifacts import PodcastArtifactStore  # noqa: E402
from tests.test_archive_sync_v2 import _V2Remote, _article, _sink, _source  # noqa: E402
from tests.test_podcast_text_sync import (  # noqa: E402
    _artifact,
    _episode,
    _local_digest_audio,
    _publication,
    _wav,
)
from tests.test_podcast_text_sync import _source as _podcast_source  # noqa: E402


def _pull(consumer, handler, tmp_path, label, checkpoints, **kwargs):
    """一次完整拉取;流级检查点随完成写回 ``checkpoints``(与生产的 KV 游标同义)。"""
    root = tmp_path / f"{label}-files"
    store = PodcastArtifactStore(
        consumer.engine, root, max_bytes=1024 * 1024, total_quota_bytes=0,
        minimum_free_bytes=0, staging_ttl_seconds=0, allowed_mime_types=("audio/wav",),
    )
    return asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url=f"https://upstream-of-{label}.test",
        username="admin",
        password="secret",
        media_root=root,
        podcast_artifact_store=store,
        checkpoints=checkpoints,
        on_stream_complete=lambda stream, checkpoint: checkpoints.update({stream: checkpoint}),
        transport=httpx.MockTransport(handler),
        push_candidate_evidence=False,
        **kwargs,
    ))


def _analysis(article, score):
    return ArticleAnalysisRecord(
        article_id=article.id,
        status="succeeded",
        tagging_status="succeeded",
        quality_score=score,
        content_hash=compute_content_hash(article),
        created_at="2026-09-02T02:00:00+00:00",
        updated_at="2026-09-02T02:00:00+00:00",
    )


def test_three_node_chain_forwards_content_edits_and_deletes(tmp_path):
    """A → B → C:B 把从 A 拉来的内容连同自己的内容一起给 C;A 上的修改与删除经 B 传到 C;
    B 首次同步不动自己在用的源;全程不再发存在性查询;没有变化时增量为零(不回声)。"""
    a = _sink(tmp_path, "chain-a.db")
    b = _sink(tmp_path, "chain-b.db")
    c = _sink(tmp_path, "chain-c.db")
    with Session(a.engine) as session:
        session.add(_source())
        article = _article(content="Body from A")
        doomed = _article(
            "article-doomed", source_url="https://example.test/doomed", content="Will be deleted on A"
        )
        session.add_all([article, doomed])
        session.flush()
        session.add(_analysis(article, 7.0))
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00+00:00", last_success_at="2026-09-02T03:00:00+00:00",
            total_runs=1, success_runs=1, updated_at="2026-09-02T03:00:00+00:00",
        ))
        session.commit()
    with Session(b.engine) as session:
        session.add(_source("rss_b_local", name="B local", url="https://b.test/feed.xml"))
        session.add(_article(
            "article-b", source_id="rss_b_local", source_url="https://b.test/1", content="Body from B"
        ))
        session.commit()
    authority_a = archive_sync_v2.producer_authority_id(a.engine)
    authority_b = archive_sync_v2.producer_authority_id(b.engine)
    remote_a, remote_b = _V2Remote(a), _V2Remote(b)
    b_checkpoints, c_checkpoints = {}, {}

    _pull(b, remote_a.handler, tmp_path, "b", b_checkpoints)
    _pull(c, remote_b.handler, tmp_path, "c", c_checkpoints)

    with Session(b.engine) as session:
        local = session.get(SourceConfigRecord, "rss_b_local")
        assert (local.is_active, local.ai_analysis_enabled, local.collection_authority_id) == (True, True, "")
        synced = session.get(SourceConfigRecord, "rss_platform")
        # 同步建出来的源:分析默认关;印记只是「上游也有」的簿记
        assert (synced.ai_analysis_enabled, synced.collection_authority_id) == (False, authority_a)
        assert session.get(ArticleRecord, "article-1").analysis_authority_id == authority_a
        assert session.get(ArticleRecord, "article-b").analysis_authority_id == ""
    with Session(c.engine) as session:
        articles = {row.id: row for row in session.exec(select(ArticleRecord)).all()}
        assert set(articles) == {"article-1", "article-doomed", "article-b"}
        # C 只认识 B:A 的内容与 B 自己的内容都记在 B 名下
        assert {row.analysis_authority_id for row in articles.values()} == {authority_b}
        assert articles["article-1"].content == "Body from A"
        assert session.get(ArticleAnalysisRecord, "article-1").quality_score == 7.0
        sources = {row.source_id: row for row in session.exec(select(SourceConfigRecord)).all()}
        assert {"rss_platform", "rss_b_local"} <= set(sources)
        assert all(not sources[key].ai_analysis_enabled for key in ("rss_platform", "rss_b_local"))
        assert session.get(SourceStateRecord, "rss_platform").status == "healthy"
    assert remote_a.presence_requests == remote_b.presence_requests == 0

    # ── A 上改正文并重新分析、删一篇;B 上删自己的一篇 ──
    with Session(a.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.content = "Body from A, edited"
        session.add(article)
        assert queue_article_analysis(
            session, article.id, force=True, now=dt.datetime.now(dt.timezone.utc)
        ) == "invalidated"
        session.delete(session.get(ArticleRecord, "article-doomed"))
        session.commit()
    with Session(a.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, article.id)
        analysis.status = analysis.tagging_status = "succeeded"
        analysis.quality_score = 9.0
        analysis.content_hash = compute_content_hash(article)
        analysis.updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        session.add(analysis)
        session.commit()
    with Session(b.engine) as session:
        session.delete(session.get(ArticleRecord, "article-b"))
        session.commit()

    second_b = _pull(b, remote_a.handler, tmp_path, "b", b_checkpoints)
    second_c = _pull(c, remote_b.handler, tmp_path, "c", c_checkpoints)
    assert second_b["streams"]["articles"]["count"] == 2      # 一改一删
    assert second_c["streams"]["articles"]["count"] == 3      # 再加 B 自己删的那篇
    for sink in (b, c):
        with Session(sink.engine) as session:
            assert session.get(ArticleRecord, "article-doomed") is None
            assert session.get(ArticleRecord, "article-b") is None
            assert session.get(ArticleRecord, "article-1").content == "Body from A, edited"
            assert session.get(ArticleAnalysisRecord, "article-1").quality_score == 9.0

    # ── 没有新变化:两跳都是零增量 ──
    idle_b = _pull(b, remote_a.handler, tmp_path, "b", b_checkpoints)
    idle_c = _pull(c, remote_b.handler, tmp_path, "c", c_checkpoints)
    assert all(item["count"] == 0 for item in idle_b["streams"].values())
    assert all(item["count"] == 0 for item in idle_c["streams"].values())
    assert remote_a.presence_requests == remote_b.presence_requests == 0


def _seed_binary_origin(origin, stream):
    """在源头节点上摆两份带文件的公共内容;返回 {身份: 字节}。"""
    bodies = {}
    with Session(origin.engine) as session:
        if stream == "media":
            session.add(_source())
            urls = [f"https://img.test/{index}.jpg" for index in range(2)]
            session.add(_article(content="\n".join(f"![image]({url})" for url in urls)))
            for index, url in enumerate(urls):
                body = b"\xff\xd8\xffimage" + bytes([index])
                key = hashlib.sha256(url.encode()).hexdigest()
                bodies[key] = body
                session.add(MediaAssetRecord(
                    url_hash=key, url=url, status="cached",
                    content_hash=hashlib.sha256(body).hexdigest(),
                    mime="image/jpeg", ext=".jpg", size_bytes=len(body),
                    created_at="2026-09-01", updated_at="2026-09-01",
                ))
        else:
            session.add(_podcast_source())
            session.commit()
            for index in range(2):
                episode = _episode(f"episode-{index}")
                script = _artifact(f"script-{index}", episode_id=episode.id, kind="narration_script_zh")
                session.add(episode)
                session.commit()
                session.add(script)
                session.add(_publication(script.id, episode_id=episode.id, kind="narration_script_zh"))
                session.commit()
                record = _local_digest_audio(episode_id=episode.id, script=script)
                body = _wav()[:-1] + bytes([index])
                bodies[record.id] = body
                record.content_hash = hashlib.sha256(body).hexdigest()
                record.size_bytes = len(body)
                record.mime, record.ext = "audio/wav", ".wav"
                session.add(record)
        session.commit()
    return bodies


def _binary_prefix(stream):
    return "/api/archive/v2/" + ("media/" if stream == "media" else "podcast-audio/")


def _binary_rows(sink, stream, keys):
    model = MediaAssetRecord if stream == "media" else PodcastArtifactRecord
    with Session(sink.engine) as session:
        return {key: getattr(session.get(model, key), "status", None) for key in keys}


@pytest.mark.parametrize("stream", ["media", "podcast_audio"])
def test_file_installed_after_the_downstream_checkpoint_is_still_forwarded(tmp_path, monkeypatch, stream):
    """顺序陷阱:B 先收到元数据、文件还没装上时 C 来拉——没就绪的文件不导出,C 的检查点照常越过
    这条的修订;之后 B 把文件装上,必须让 C 的增量还能拿到它(文件就绪推进导出修订)。"""
    a = _sink(tmp_path, f"ready-a-{stream}.db")
    b = _sink(tmp_path, f"ready-b-{stream}.db")
    c = _sink(tmp_path, f"ready-c-{stream}.db")
    bodies = _seed_binary_origin(a, stream)
    monkeypatch.setattr(remote_sync_service, "_MAX_RETRIES", 1)
    remote_a, remote_b = _V2Remote(a), _V2Remote(b)
    prefix = _binary_prefix(stream)
    upstream_files = {"available": False}
    downloads = {"b": [], "c": []}

    def serve_a(request):
        if request.url.path.startswith(prefix):
            key = request.url.path.rsplit("/", 1)[-1]
            downloads["b"].append(key)
            if not upstream_files["available"]:
                return httpx.Response(500)
            return httpx.Response(200, content=bodies[key])
        return remote_a.handler(request)

    def serve_b(request):
        if request.url.path.startswith(prefix):
            key = request.url.path.rsplit("/", 1)[-1]
            downloads["c"].append(key)
            return httpx.Response(200, content=bodies[key])
        return remote_b.handler(request)

    b_checkpoints, c_checkpoints = {}, {}
    ready = "cached" if stream == "media" else "published"

    # ① B 收到元数据,文件下载失败
    with pytest.raises(remote_sync_service.RemoteSyncError):
        _pull(b, serve_a, tmp_path, f"b-{stream}", b_checkpoints)
    assert stream not in b_checkpoints
    assert all(status not in (None, ready) for status in _binary_rows(b, stream, bodies).values())

    # ② C 这时来拉 B:拿不到这些文件,检查点照常前进
    _pull(c, serve_b, tmp_path, f"c-{stream}", c_checkpoints)
    assert downloads["c"] == []
    assert stream in c_checkpoints
    assert set(_binary_rows(c, stream, bodies).values()) == {None}

    # ③ B 补齐文件
    upstream_files["available"] = True
    _pull(b, serve_a, tmp_path, f"b-{stream}", b_checkpoints)
    assert set(_binary_rows(b, stream, bodies).values()) == {ready}

    # ④ C 增量拉取:仍然拿得到
    _pull(c, serve_b, tmp_path, f"c-{stream}", c_checkpoints)
    assert sorted(downloads["c"]) == sorted(bodies)
    assert set(_binary_rows(c, stream, bodies).values()) == {ready}


def test_takeover_replays_dependency_streams_even_after_an_interrupted_pull(tmp_path):
    """本机版本被上游接管时,分析 / 文稿 / 音频的流级检查点可能早已越过这篇的那几行(此前因为
    「文章是本机版本」被跳过)。接管要把依赖流记为待重放,标记落库:拉取中断后下一轮仍会重放,
    重放完成才清。"""
    a = _sink(tmp_path, "replay-a.db")
    b = _sink(tmp_path, "replay-b.db")
    with Session(a.engine) as session:
        session.add(_source())
        article = _article()
        session.add(article)
        session.flush()
        session.add(_analysis(article, 7.0))
        session.commit()
    with Session(b.engine) as session:
        session.add(_source())
        # B 自己抓到的版本,比上游的新
        session.add(_article(content="B local body", archive_updated_at="2026-09-03T00:00:00+00:00"))
        session.commit()
    authority_a = archive_sync_v2.producer_authority_id(a.engine)
    checkpoints = {}

    first = _pull(b, _V2Remote(a).handler, tmp_path, "replay-b", checkpoints)
    assert first["streams"]["articles"]["skipped"] == 1
    with Session(b.engine) as session:
        kept = session.get(ArticleRecord, "article-1")
        assert (kept.analysis_authority_id, kept.content) == ("", "B local body")
        assert session.get(ArticleAnalysisRecord, "article-1") is None
    assert archive_sync_v2.pending_dependency_replay(b.engine) == set()
    analyses_watermark = checkpoints["analyses"]["snapshot"]

    # 上游这篇有了更新的修订(正文与分析都没变:分析行在上游没有新修订)
    with Session(a.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.extensions_json = '{"touched": true}'
        session.add(article)
        session.commit()

    # 拉取在 analyses 流上中断:文章已被接管,待重放标记已经落库
    with pytest.raises(remote_sync_service.RemoteSyncError, match="analyses"):
        _pull(b, _V2Remote(a, fail_stream="analyses").handler, tmp_path, "replay-b", checkpoints)
    with Session(b.engine) as session:
        taken = session.get(ArticleRecord, "article-1")
        assert (taken.analysis_authority_id, taken.content) == (authority_a, "Full body")
        assert session.get(ArticleAnalysisRecord, "article-1") is None
    assert archive_sync_v2.pending_dependency_replay(b.engine) == set(
        archive_sync_v2.DEPENDENCY_REPLAY_STREAMS
    )
    assert checkpoints["analyses"]["snapshot"] == analyses_watermark

    # 下一轮:依赖流从头重放(不带 since),分析补齐,标记清掉
    resumed = _V2Remote(a)
    _pull(b, resumed.handler, tmp_path, "replay-b", checkpoints)
    first_request = {}
    for stream, params in resumed.requested_params:
        first_request.setdefault(stream, params)
    assert not first_request["analyses"].get("since")
    assert first_request["articles"].get("since")              # 其它流仍是增量
    with Session(b.engine) as session:
        assert session.get(ArticleAnalysisRecord, "article-1").quality_score == 7.0
    assert archive_sync_v2.pending_dependency_replay(b.engine) == set()

    # 再下一轮恢复增量
    steady = _V2Remote(a)
    idle = _pull(b, steady.handler, tmp_path, "replay-b", checkpoints)
    first_request = {}
    for stream, params in steady.requested_params:
        first_request.setdefault(stream, params)
    assert first_request["analyses"].get("since")
    assert all(item["count"] == 0 for item in idle["streams"].values())


def test_audio_of_a_locally_owned_episode_is_skipped_through_the_binary_stage(tmp_path, monkeypatch):
    """单集是本机版本时,上游给这一集的文稿与音频都跳过;二进制阶段不能再去找这条被跳过的记录
    (否则报「记录缺失」把整条流卡死)。别的单集照常装上。"""
    a = _sink(tmp_path, "audio-a.db")
    b = _sink(tmp_path, "audio-b.db")
    bodies = _seed_binary_origin(a, "podcast_audio")
    monkeypatch.setattr(remote_sync_service, "_MAX_RETRIES", 1)
    with Session(a.engine) as session:
        audio_by_episode = {
            row.episode_id: row.id for row in session.exec(select(PodcastArtifactRecord)).all()
        }
    with Session(b.engine) as session:
        session.add(_podcast_source())
        local_episode = _episode("episode-0")
        local_episode.archive_updated_at = "2026-10-01T00:00:00+00:00"   # 比上游的新
        local_episode.content = "B's own show notes"
        session.add(local_episode)
        session.commit()
    remote_a = _V2Remote(a)
    downloads = []

    def serve_a(request):
        if request.url.path.startswith(_binary_prefix("podcast_audio")):
            key = request.url.path.rsplit("/", 1)[-1]
            downloads.append(key)
            return httpx.Response(200, content=bodies[key])
        return remote_a.handler(request)

    checkpoints = {}
    result = _pull(b, serve_a, tmp_path, "audio-b", checkpoints)

    assert downloads == [audio_by_episode["episode-1"]]
    assert "source_states" in checkpoints                    # 后面的流没有被饿死
    assert result["streams"]["podcast_audio"]["skipped"] == 1
    with Session(b.engine) as session:
        mine = session.get(ArticleRecord, "episode-0")
        assert (mine.analysis_authority_id, mine.content) == ("", "B's own show notes")
        assert session.get(PodcastArtifactRecord, audio_by_episode["episode-0"]) is None
        assert session.get(PodcastTextPublicationRecord, "episode-0:narration_script_zh") is None
        assert session.get(PodcastArtifactRecord, audio_by_episode["episode-1"]).status == "published"
        assert session.get(PodcastTextPublicationRecord, "episode-1:narration_script_zh").status == "published"
