"""Published synthetic podcast artifacts for API and browser regression tests."""
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import wave

SOURCE = "e2e-podcast-guides"
STATES = (
    ("none", False, False, "not_started"),
    ("queued", False, False, "queued"),
    ("retry", False, False, "retry_wait"),
    ("failed", False, False, "failed"),
    ("text", True, False, "ready"),
    ("synthesizing", True, False, "synthesizing"),
    ("text-retry", True, False, "retry_wait"),
    ("text-failed", True, False, "failed"),
    ("audio", False, True, "ready"),
    ("both", True, True, "ready"),
)


def text_artifact(session, episode_id, kind, timestamp, *, published=True):
    from models.db import PodcastTextArtifactRecord, PodcastTextPublicationRecord
    body = f"中文导读测试正文 {episode_id}" if kind == "digest_blog_zh" else "合成测试的文字内容。"
    artifact = PodcastTextArtifactRecord(
        id=f"{episode_id}-{kind}", episode_id=episode_id, kind=kind, version=1,
        content_hash=hashlib.sha256(body.encode()).hexdigest(), inline_text=body,
        language="zh-CN", authority_id="", provenance_json='{"fixture":true}', created_at=timestamp,
    )
    session.add(artifact)
    session.flush()
    session.add(PodcastTextPublicationRecord(
        identity=f"{episode_id}:{kind}", episode_id=episode_id, kind=kind, artifact_id=artifact.id,
        status="published" if published else "unpublished", authority_id="",
        published_at=timestamp, unpublished_at=None if published else timestamp, updated_at=timestamp,
    ))
    session.flush()
    return artifact


def seed_podcasts(session, now, *, username=None, audio_root: Path | None = None, fillers=32):
    from models.db import (AppSettingRecord, ArticleRecord, ArticleAnalysisRecord,
                           PodcastArtifactRecord, ReaderSubscriptionRecord, SourceConfigRecord)
    timestamp = now.isoformat()
    session.add(SourceConfigRecord(
        source_id=SOURCE, name="播客导读测试", source_type="podcast", category="podcast",
        fetcher_id="generic_podcast_rss", url="https://example.invalid/podcasts.xml",
        ai_analysis_enabled=False, created_at=timestamp, updated_at=timestamp,
    ))
    session.add(AppSettingRecord(key="podcast_premium_score_threshold", value="7.5"))
    if username:
        session.add(ReaderSubscriptionRecord(
            owner_username=username, name="播客导读测试", filters_json=json.dumps({"source_id": SOURCE}),
            token_hash="unused-podcast-subscription", created_at=timestamp, updated_at=timestamp,
        ))
        # Mobile guest lists use the reserved account's subscriptions after leaving a deep link.
        if username == "e2e-reader":
            session.add(ReaderSubscriptionRecord(
                owner_username="guest:shared", name="播客导读测试",
                filters_json=json.dumps({"source_id": SOURCE}), token_hash="unused-podcast-guest",
                created_at=timestamp, updated_at=timestamp,
            ))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000 * 12)
    wav = buffer.getvalue()
    audio_hash = hashlib.sha256(wav).hexdigest()
    if audio_root:
        target = audio_root / audio_hash[:2] / f"{audio_hash}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(wav)
    rows = [(f"{name}-{int(premium)}", readable, listenable, status, premium)
            for name, readable, listenable, status in STATES for premium in (False, True)]
    rows += [(f"page-{index:02d}", True, True, "ready", False) for index in range(fillers)]
    rows += [("notes", False, False, "not_started", False),
             ("unscored", False, False, "not_started", False),
             ("withdrawn", False, False, "ready", False)]
    expected = {}
    for index, (name, readable, listenable, status, premium) in enumerate(rows):
        aid = f"{SOURCE}-{name}"
        stamp = (now - timedelta(minutes=index)).isoformat()
        session.add(ArticleRecord(
            id=aid, title=f"导读样例 {name}", source_id=SOURCE, content_type="podcast_episode",
            source_url=f"https://example.invalid/{aid}", publish_date=stamp, fetched_date=stamp,
            has_content=True, content=f"节目简介 {name}", extensions_json=json.dumps({
                "show_title": "播客导读测试", "audio_url": "/e2e-silence.wav", "duration_seconds": 1800,
                "premium_guide": {"status": status, "mode": "solo_deep", "error": "internal-secret-error"},
            }),
        ))
        session.flush()
        if name != "unscored":
            score = 8.0 if premium else 6.0
            session.add(ArticleAnalysisRecord(
                article_id=aid, status="succeeded", quality_score=score, content_value=score,
                score_reason="测试评分依据", content_value_reason="测试内容依据",
                summary=f"本期摘要 {name}", analysis_basis="podcast_show_notes" if name == "notes" else "publisher_transcript",
                created_at=timestamp, updated_at=timestamp,
            ))
        if readable or name == "withdrawn":
            text_artifact(session, aid, "digest_blog_zh", timestamp, published=readable)
        if listenable or name == "withdrawn":
            script = text_artifact(session, aid, "narration_script_zh", timestamp)
            session.add(PodcastArtifactRecord(
                id=f"{aid}-audio", episode_id=aid, kind="digest_audio_zh", content_hash=audio_hash,
                mime="audio/wav", ext=".wav", size_bytes=len(wav), duration_seconds=12,
                status="published" if listenable else "withdrawn", provenance="manual_upload",
                narration_artifact_id=script.id, narration_content_hash=script.content_hash,
                created_at=timestamp, updated_at=timestamp, published_at=timestamp,
                withdrawn_at=None if listenable else timestamp,
            ))
        expected[aid] = {"readable": readable, "listenable": listenable, "premium": premium, "status": status}
    session.commit()
    return expected


if __name__ == "__main__":
    import os
    import sys
    from datetime import datetime, timezone
    sandbox = Path(sys.argv[1]).resolve()
    assert (sandbox / '.dorami-e2e').is_file(), 'owned E2E sandbox required'
    assert Path(os.environ['DORAMI_CONFIG_FILE']).resolve() == sandbox / 'backend.ini'
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from config import settings
    from sqlalchemy import create_engine
    from sqlmodel import Session
    assert settings.storage.database_url == f"sqlite:///{sandbox / 'reader.db'}"
    assert (sandbox / 'reader.db').is_file()
    with Session(create_engine(settings.storage.database_url)) as session:
        rows = seed_podcasts(session, datetime.now(timezone.utc).replace(tzinfo=None),
                             username='e2e-reader', audio_root=sandbox / 'podcast')
    (sandbox / 'podcast-cases.json').write_text(json.dumps(rows, ensure_ascii=False))
    print(f'Seeded {len(rows)} synthetic podcast episodes; no provider calls')
