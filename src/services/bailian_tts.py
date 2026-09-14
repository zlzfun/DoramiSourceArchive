"""Bounded Qwen3 TTS with durable per-chunk receipts and a local CNY budget.

The premium-guide workflow is synchronous, not the async ASR worker. Its paid
requests therefore have their own journal. Authorization is committed before
POST. Neither a crash nor an ambiguous response can silently repeat that POST.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import struct
import wave
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import or_, text as sql_text
from sqlmodel import Session, select
from config_bailian import BailianSpeechConfig
from models.db import BailianTtsCallRecord
from services.bailian_speech_client import (
    BailianSpeechClient,
    BailianSpeechError,
    download_result,
    result_url,
    task_id,
)
from services.podcast_premium_guides import SynthesizedAudio


def split_narration(text, limit):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("TTS narration must be nonempty")
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        ends = [m.end() for m in re.finditer(r"[。！？!?；;\n]|[.!?]\s", text[:limit])]
        cut = ends[-1] if ends and ends[-1] >= limit // 3 else limit
        chunks.append(text[:cut])
        text = text[cut:]
    # Preserve every character, including spaces and punctuation.
    return chunks


def join_wav(parts, max_bytes):
    out = io.BytesIO()
    params = None
    frames = []
    size = 44
    for data in parts:
        with wave.open(io.BytesIO(data), "rb") as source:
            current = (
                source.getnchannels(),
                source.getsampwidth(),
                source.getframerate(),
                source.getcomptype(),
            )
            if (
                current[3] != "NONE"
                or current[0] != 1
                or current[1] != 2
                or current[2] != 24000
            ):
                raise ValueError("Unexpected Qwen TTS WAV format")
            if params and current != params:
                raise ValueError("TTS chunks have inconsistent WAV formats")
            params = current
            expected = source.getnframes() * current[0] * current[1]
            chunk = source.readframes(source.getnframes())
            # Qwen3 currently returns this streaming WAV header with an
            # intentionally unspecified ~2GB length. Accept only the observed
            # canonical PCM header; ordinary length mismatches stay errors.
            streaming_header = (
                data[:4] == b"RIFF"
                and data[8:16] == b"WAVEfmt "
                and data[36:40] == b"data"
                and struct.unpack_from("<I", data, 4)[0] == 0x7FFFFFBF
                and struct.unpack_from("<I", data, 40)[0] == 0x7FFFFF9B
            )
            if (
                not chunk
                or (len(chunk) != expected and not streaming_header)
                or len(chunk) % (current[0] * current[1])
            ):
                raise ValueError("TTS WAV chunk is truncated or empty")
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("TTS audio exceeds configured size limit")
            frames.append(chunk)
    with wave.open(out, "wb") as dest:
        dest.setnchannels(params[0])
        dest.setsampwidth(params[1])
        dest.setframerate(params[2])
        for chunk in frames:
            dest.writeframesraw(chunk)
    return out.getvalue()


def _atomic_write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class BailianPremiumGuideTtsProvider:
    def __init__(
        self,
        config,
        *,
        engine,
        episode_id,
        voice_profile,
        max_audio_bytes,
        client_factory=BailianSpeechClient,
        downloader=download_result,
        clock=None,
    ):
        if not config.tts_configured or voice_profile != config.voice_profile:
            raise ValueError(
                "Bailian TTS credentials, budget or voice alias unavailable"
            )
        self.config, self.engine, self.episode_id = config, engine, episode_id
        self.max_audio_bytes, self.client_factory, self.downloader = (
            max_audio_bytes,
            client_factory,
            downloader,
        )
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))

    async def synthesize(self, narration):
        return await asyncio.to_thread(self._synthesize, narration)

    def _reserve(self, chunks, keys, now):
        cfg = self.config
        period = now.astimezone(ZoneInfo("Asia/Singapore")).strftime("%Y-%m")
        with Session(self.engine) as session:
            session.exec(sql_text("BEGIN IMMEDIATE"))
            ambiguous = session.exec(
                select(BailianTtsCallRecord).where(
                    BailianTtsCallRecord.account_scope == cfg.account_scope,
                    BailianTtsCallRecord.episode_id == self.episode_id,
                    BailianTtsCallRecord.status == "authorized",
                )
            ).first()
            if ambiguous:
                raise BailianSpeechError("tts_reconciliation_required", unknown=True)
            existing = [session.get(BailianTtsCallRecord, key) for key in keys]
            new = []
            for key, chunk, row in zip(keys, chunks, existing):
                if row:
                    if row.status == "rejected":
                        raise BailianSpeechError("tts_previously_rejected")
                    continue
                # UTF-8 length is an explicit conservative upper bound; settle
                # using the provider's actual `usage.characters`, not this count.
                units = len(chunk.encode("utf-8"))
                new.append(
                    BailianTtsCallRecord(
                        id=key,
                        episode_id=self.episode_id,
                        account_scope=cfg.account_scope,
                        budget_period=period,
                        created_at=now.isoformat(),
                        reserved_characters=units,
                        cost_minor=(
                            units * cfg.tts_price_minor + cfg.tts_price_units - 1
                        )
                        // cfg.tts_price_units,
                        price_minor=cfg.tts_price_minor,
                        price_units=cfg.tts_price_units,
                        pricing_revision=cfg.pricing_revision,
                    )
                )
            total_run = sum(r.cost_minor for r in existing if r) + sum(
                r.cost_minor for r in new
            )
            if total_run > cfg.tts_per_run_budget_minor:
                raise BailianSpeechError("tts_per_run_budget_exceeded")
            charged = session.exec(
                select(BailianTtsCallRecord).where(
                    BailianTtsCallRecord.account_scope == cfg.account_scope,
                    or_(
                        BailianTtsCallRecord.budget_period == period,
                        BailianTtsCallRecord.status.in_(
                            ["reserved", "authorized", "generated"]
                        ),
                    ),
                )
            ).all()
            if (
                sum(r.cost_minor for r in charged) + sum(r.cost_minor for r in new)
                > cfg.tts_monthly_budget_minor
            ):
                raise BailianSpeechError("tts_monthly_budget_exceeded")
            session.add_all(new)
            session.commit()

    def _row(self, key):
        with Session(self.engine) as session:
            row = session.get(BailianTtsCallRecord, key)
            session.expunge(row)
            return row

    def _update(self, key, expected, **values):
        with Session(self.engine) as session:
            session.exec(sql_text("BEGIN IMMEDIATE"))
            row = session.get(BailianTtsCallRecord, key)
            if not row or row.status != expected:
                raise BailianSpeechError("tts_receipt_conflict")
            for name, value in values.items():
                setattr(row, name, value)
            session.add(row)
            session.commit()

    def _synthesize(self, narration):
        cfg = self.config
        if len(narration) > cfg.tts_max_chars:
            raise ValueError("Bailian narration exceeds configured character limit")
        chunks = split_narration(narration, cfg.tts_chunk_chars)
        binding = json.dumps(
            [
                cfg.account_scope,
                cfg.base_url,
                cfg.tts_model,
                cfg.tts_voice,
                self.episode_id,
                narration,
                cfg.tts_chunk_chars,
                "qwen-wav-v1",
            ],
            ensure_ascii=False,
        )
        run_id = hashlib.sha256(binding.encode()).hexdigest()
        keys = [
            hashlib.sha256(f"{run_id}:{i}".encode()).hexdigest()
            for i in range(len(chunks))
        ]
        root = Path(cfg.tts_receipt_root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # One bounded local spool serializes downloads/capacity across processes.
        # No database transaction is held over provider I/O.
        fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            total = sum(p.stat().st_size for p in root.iterdir() if p.is_file())
            with Session(self.engine) as session:
                needs_storage = any(
                    (row := session.get(BailianTtsCallRecord, key)) is None
                    or row.status != "succeeded"
                    for key in keys
                )
            if needs_storage and (
                total + self.max_audio_bytes > cfg.tts_cache_max_bytes
                or shutil.disk_usage(root).free < self.max_audio_bytes + 1024**3
            ):
                raise BailianSpeechError("tts_receipt_storage_full")
            self._reserve(chunks, keys, self.clock())
            parts = []
            client = self.client_factory(cfg)
            try:
                for key, chunk in zip(keys, chunks):
                    row = self._row(key)
                    receipt, audio_path = root / f"{key}.json", root / f"{key}.wav"
                    if row.status == "reserved":
                        self._update(key, "reserved", status="authorized")
                        try:
                            payload = client.synthesize(chunk)
                        except BailianSpeechError as exc:
                            if not exc.unknown:
                                self._update(
                                    key, "authorized", status="rejected", cost_minor=0
                                )
                            raise
                        # Save the raw paid response before parsing/downloading;
                        # it is private provider state, never synced or returned.
                        _atomic_write(receipt, json.dumps(payload).encode())
                        chars = payload.get("usage", {}).get("characters")
                        if (
                            type(chars) is not int
                            or not 0 < chars <= row.reserved_characters
                        ):
                            raise BailianSpeechError("tts_usage_unknown", unknown=True)
                        request = task_id(payload.get("request_id"))
                        result_url(payload["output"]["audio"]["url"], cfg)
                        self._update(
                            key,
                            "authorized",
                            status="generated",
                            actual_characters=chars,
                            cost_minor=(chars * row.price_minor + row.price_units - 1)
                            // row.price_units,
                            request_id=request,
                        )
                        row = self._row(key)
                    if row.status == "generated":
                        payload = json.loads(receipt.read_text())
                        data = asyncio.run(
                            self.downloader(
                                payload["output"]["audio"]["url"],
                                cfg,
                                max_bytes=self.max_audio_bytes - sum(map(len, parts)),
                            )
                        )
                        data = join_wav([data], self.max_audio_bytes)
                        _atomic_write(audio_path, data)
                        self._update(
                            key,
                            "generated",
                            status="succeeded",
                            audio_hash=hashlib.sha256(data).hexdigest(),
                        )
                        row = self._row(key)
                    if row.status != "succeeded":
                        raise BailianSpeechError(
                            "tts_reconciliation_required", unknown=True
                        )
                    data = audio_path.read_bytes()
                    if hashlib.sha256(data).hexdigest() != row.audio_hash:
                        raise BailianSpeechError("tts_cached_audio_corrupt")
                    parts.append(data)
                return SynthesizedAudio(
                    data=join_wav(parts, self.max_audio_bytes),
                    mime="audio/wav",
                    provider_task_id=run_id,
                )
            finally:
                client.close()


def make_premium_tts_provider(
    config, *, engine, episode_id, voice_profile, max_audio_bytes
):
    if isinstance(config, BailianSpeechConfig):
        return BailianPremiumGuideTtsProvider(
            config,
            engine=engine,
            episode_id=episode_id,
            voice_profile=voice_profile,
            max_audio_bytes=max_audio_bytes,
        )
    from services.podcast_premium_guide_providers import (
        AliyunIsiPremiumGuideTtsProvider,
    )

    return AliyunIsiPremiumGuideTtsProvider(
        config, voice_profile=voice_profile, max_audio_bytes=max_audio_bytes
    )
