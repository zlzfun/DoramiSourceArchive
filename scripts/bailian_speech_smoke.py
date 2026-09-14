#!/usr/bin/env python3
"""Opt-in paid Singapore speech smoke test, isolated from the application DB.

Run with a protected DORAMI_CONFIG_FILE. Restart with the same output directory
reuses receipts and polls the existing ASR task; never delete it to retry a timeout.
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import io
import json
import time
import wave
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select
from config import settings, PodcastConfig
from models.db import (
    ArticleRecord,
    SourceConfigRecord,
    PodcastSourceMediaSnapshotRecord,
    BailianTtsCallRecord,
    PodcastTextArtifactRecord,
    PodcastCostLedgerRecord,
    PodcastProcessingRecord,
)
from services.bailian_tts import BailianPremiumGuideTtsProvider
from services.bailian_asr import (
    BailianAsrWorkerBundle,
    admission_fingerprint,
    usage_plan,
)
from services.podcast_stage_policy import PodcastStagePolicy
from services.podcast_asr_worker import AsrWorkerConfig
from services.podcast_processing import enqueue_processing
from services.podcast_processing_inputs import processing_input_fingerprint

SAMPLE = (
    "欢迎收听哆啦美科技播客。这是一段用于验证新加坡语音服务的测试内容。"
    "今天我们讨论人工智能如何帮助开发者处理长音频。语音识别把录音转换成文字，"
    "语音合成则把导读稿转换成自然的声音。ASR and TTS are two different tasks. "
    "模型需要正确读出 Qwen、Python 和 API，也需要保留句子的停顿与标点。"
    "接下来进入第二段。我们会检查音频分段合并是否完整，重复运行是否复用已经生成的结果，"
    "并核对每次请求实际消耗的字符和音频秒数。这段内容只用于功能测试，不会发布到正式订阅。"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--wait-seconds", type=int, default=240)
    args = parser.parse_args()
    root = Path(args.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfg = settings.bailian_speech
    if not cfg.tts_configured or not cfg.asr_accounting_ready:
        raise ValueError("Complete explicit sample credentials and budget required")
    engine = create_engine(
        f"sqlite:///{root}/sample.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    provider = BailianPremiumGuideTtsProvider(
        cfg,
        engine=engine,
        episode_id="bailian-sample",
        voice_profile=cfg.voice_profile,
        max_audio_bytes=20 * 1024 * 1024,
    )
    audio = asyncio.run(provider.synthesize(SAMPLE))
    (root / "sample.wav").write_bytes(audio.data)
    (root / "sample-text.txt").write_text(SAMPLE)
    print("TTS saved and validated", flush=True)
    # Same text/voice/episode after restart must generate no additional calls.
    with Session(engine) as session:
        before = len(session.exec(select(BailianTtsCallRecord)).all())
    replay = asyncio.run(provider.synthesize(SAMPLE))
    assert replay.data == audio.data
    with Session(engine) as session:
        rows = session.exec(select(BailianTtsCallRecord)).all()
        assert len(rows) == before
        first = sorted(rows, key=lambda r: r.created_at)[0]
        tts_summary = {
            "requests": len(rows),
            "characters": sum(r.actual_characters for r in rows),
            "cost_minor_upper_bound": sum(r.cost_minor for r in rows),
            "replay_without_new_calls": True,
        }
    receipt_root = Path(cfg.tts_receipt_root)
    payload = json.loads((receipt_root / f"{first.id}.json").read_text())
    url = payload["output"]["audio"]["url"]
    from services.bailian_speech_client import result_url

    url = result_url(url, cfg)
    chunk = (receipt_root / f"{first.id}.wav").read_bytes()
    with wave.open(io.BytesIO(chunk), "rb") as w:
        duration = w.getnframes() / w.getframerate()
    digest = hashlib.sha256(chunk).hexdigest()
    podcast = PodcastConfig(
        installation="external",
        authority_id="bailian-validation",
        allowed_stages=("fetch", "asr"),
        processing_enabled=True,
        provider_ready_targets=("transcript",),
        budget_scope="bailian-sample",
        monthly_budget_cny_minor=100,
        per_run_budget_cny_minor=100,
    )
    policy = PodcastStagePolicy(podcast, bailian_speech=cfg)
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat()
    with Session(engine) as session:
        if session.get(ArticleRecord, "bailian-sample") is None:
            session.add(
                SourceConfigRecord(
                    source_id="bailian-sample",
                    name="Bailian validation",
                    source_type="podcast",
                    url="https://example.com/feed.xml",
                    category="podcast",
                    fetcher_id="generic_podcast_rss",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            session.add(
                ArticleRecord(
                    id="bailian-sample",
                    title="Bailian speech validation",
                    content_type="podcast_episode",
                    source_id="bailian-sample",
                    source_url="https://example.com/sample",
                    publish_date=stamp,
                    fetched_date=stamp,
                    extensions_json=json.dumps(
                        {"audio_url": url, "duration_seconds": duration}
                    ),
                )
            )
            session.add(
                PodcastSourceMediaSnapshotRecord(
                    id="sample-media",
                    episode_id="bailian-sample",
                    content_hash=digest,
                    mime="audio/wav",
                    size_bytes=len(chunk),
                    duration_seconds=duration,
                    locator_hash=hashlib.sha256(url.encode()).hexdigest(),
                    created_at=stamp,
                )
            )
        session.commit()
        duration_ms = int(round(duration * 1000))
        process = session.exec(
            select(PodcastProcessingRecord).where(
                PodcastProcessingRecord.episode_id == "bailian-sample",
                PodcastProcessingRecord.idempotency_key == "bailian-sample-asr-v1",
            )
        ).first()
        if process is not None:
            if process.input_content_hash != digest:
                raise RuntimeError("Sample source changed; inspect the existing task")
            session.expunge(process)
        session.rollback()
        if process is None:
            plan = usage_plan(cfg, audio_duration_ms=duration_ms, now=now)
            fingerprint = processing_input_fingerprint(
                episode_id="bailian-sample",
                entry_stage="asr",
                artifact_id="sample-media",
                content_hash=digest,
                kind="source_media_snapshot",
                language="und",
                audio_duration_ms=duration_ms,
                admission_fingerprint=admission_fingerprint(cfg),
            )
            process = enqueue_processing(
                session,
                episode_id="bailian-sample",
                stage="asr",
                input_fingerprint=fingerprint,
                pipeline_version="bailian-sample-v1",
                policy_version="bailian-sample-v1",
                requested_target="transcript",
                idempotency_key="bailian-sample-asr-v1",
                estimated_cost_minor=plan.estimated_cost_minor,
                input_artifact_id="sample-media",
                input_artifact_kind="source_media_snapshot",
                input_content_hash=digest,
                input_language="und",
                budget_scope="bailian-sample",
                budget_period=now.strftime("%Y-%m"),
                budget_limit_minor=100,
                per_run_budget_minor=100,
                policy=policy,
                now=now,
            )
        process_id = process.id
    worker = BailianAsrWorkerBundle()
    worker_config = AsrWorkerConfig(
        worker_id="bailian-smoke",
        lease_seconds=300,
        fallback_retry_seconds=10,
        next_stage_by_target={"transcript": None},
    )
    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        with Session(engine) as session:
            result = worker(session, config=worker_config, policy=policy)
            print("ASR worker:", result.action, flush=True)
        with Session(engine) as session:
            process = session.get(PodcastProcessingRecord, process_id)
            if process.processing_status == "ready":
                break
            if process.processing_status in {
                "failed",
                "failed_terminal",
                "reconciliation_required",
            }:
                raise RuntimeError("ASR requires inspection; do not resubmit")
        time.sleep(cfg.asr_poll_interval_seconds)
    with Session(engine) as session:
        output = session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.kind == "normalized_transcript"
            )
        ).first()
        if not output:
            raise RuntimeError("ASR pending; rerun with the same output directory")
        (root / "transcript.json").write_text(output.inline_text)
        ledger = session.exec(select(PodcastCostLedgerRecord)).all()
        report = {
            "tts": tts_summary,
            "asr": {
                "ledger_rows": len(ledger),
                "source_audio_seconds": duration,
                "actual_seconds": sum(r.actual_usage_units for r in ledger),
                "cost_minor_upper_bound": sum(r.actual_cost_minor for r in ledger),
            },
            "transcript_characters": len(json.loads(output.inline_text)["text"]),
        }
        (root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
    engine.dispose()


if __name__ == "__main__":
    main()
