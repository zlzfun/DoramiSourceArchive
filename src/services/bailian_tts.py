"""Bounded Qwen3 TTS with durable per-chunk receipts and a local CNY budget.

The premium-guide workflow is synchronous, not the async ASR worker. Its paid
requests therefore have their own journal. Authorization is committed before
POST. Neither a crash nor an ambiguous response can silently repeat that POST.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from services.file_lock import LOCK_EX, LOCK_NB, flock
import hashlib
import io
import json
import logging
import os
import re
import shutil
import stat
import struct
import wave
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import or_, text as sql_text, update
from sqlmodel import Session, select
from config_bailian import BailianSpeechConfig
from models.db import ArticleRecord, BailianTtsCallRecord, PodcastArtifactRecord
from services.bailian_speech_client import (
    BailianSpeechClient,
    BailianSpeechError,
    download_result,
    result_url,
    task_id,
)
from services.podcast_premium_guides import SynthesizedAudio

logger = logging.getLogger("dorami.bailian_tts")

# 已成功的分段本地没有音频、供应商的临时地址也取不回,而它是被有意逐出的。
EVICTED_AUDIO_CODE = "tts_cached_audio_evicted"


def _cost_minor(units: int, price_minor: int, price_units: int) -> int:
    """``units`` characters at ``price_minor`` per ``price_units``, rounded up."""

    return (units * price_minor + price_units - 1) // price_units


def _reservation_cost_minor(units: int, cfg) -> int:
    """Budget hold for ``units`` reserved characters at the configured price."""

    return _cost_minor(units, cfg.tts_price_minor, cfg.tts_price_units)


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
    try:  # 目录 fsync:Windows 打不开目录句柄(PermissionError),跳过即可,rename 本身已落盘
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def receipt_cache_overview(config: BailianSpeechConfig, engine, *, max_audio_bytes: int) -> dict:
    """Receipt cache usage for the admin page.

    The two shortfalls are what is still missing after the capacity path has
    evicted everything it may (``evictable_bytes``). A cache that sits near its
    limit but is full of evictable audio therefore reports no shortfall: the
    next synthesis makes its own room.
    """
    root = Path(config.tts_receipt_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    used = _cache_used_bytes(root)
    free = shutil.disk_usage(root).free
    reservation = min(max_audio_bytes, config.tts_max_chars * 12_000 + 44)
    with Session(engine) as session:
        rows, settled = _reclaim_scope(session, config)
        evictable_bytes = sum(size for *_rest, size in _evictable_files(root, rows, settled))
    return {
        "used_bytes": used, "limit_bytes": config.tts_cache_max_bytes,
        "estimated_max_narration_reservation_bytes": reservation,
        "evictable_bytes": evictable_bytes,
        "cache_shortfall_bytes": max(
            0, used - evictable_bytes + reservation - config.tts_cache_max_bytes),
        "disk_free_bytes": free, "disk_minimum_free_bytes": 1024**3,
        "disk_shortfall_bytes": max(0, reservation + 1024**3 - free - evictable_bytes),
        "max_audio_bytes": max_audio_bytes,
    }


def _cache_used_bytes(root: Path) -> int:
    """Bytes of the regular files in ``root``.

    One ``stat`` per file, and a file that disappears meanwhile is skipped: the
    admin overview reads the directory without the receipt lock while a reclaim
    may be compressing or evicting.
    """
    total = 0
    for path in root.iterdir():
        try:
            info = path.stat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def _room_shortfall(root: Path, config: BailianSpeechConfig, nbytes: int) -> int:
    """Bytes that must leave ``root`` before ``nbytes`` more fit the cache and the disk."""
    free = shutil.disk_usage(root).free
    return max(
        0,
        _cache_used_bytes(root) + nbytes - config.tts_cache_max_bytes,
        nbytes + 1024**3 - free,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_RECLAIM_ARTICLE_CHUNK = 200


def _in_flight(row: BailianTtsCallRecord) -> bool:
    """Whether this call may still cost money or still has audio to fetch.

    ``authorized`` (sent, outcome unknown) and ``generated`` (paid, audio not
    downloaded yet) always count. ``reserved`` counts only while it holds
    budget. A released reservation keeps the status with ``cost_minor`` 0
    (issue #322); prices are validated positive, so one that still holds budget
    never costs 0.

    A zero-cost ``reserved`` row does not prove that no synthesis is running:
    ``_reserve`` on another receipt directory that shares this ledger releases
    reservations it does not own. That is fine here. The files this reclaim can
    reach are in its own directory, whose lock the caller holds, so the only
    synthesis that can be running on them is the caller itself, and that one
    passes its own chunks in ``protect`` (before reserving) or has a
    ``generated`` row (while storing). Sending is guarded separately by
    ``_authorize``.
    """
    return row.status in {"authorized", "generated"} or (
        row.status == "reserved" and row.cost_minor > 0
    )


def _created_at(row: BailianTtsCallRecord) -> dt.datetime | None:
    try:
        created = dt.datetime.fromisoformat(row.created_at)
    except (TypeError, ValueError):
        return None
    return created if created.tzinfo is not None else None


def _reclaim_scope(session: Session, config: BailianSpeechConfig):
    """Ledger rows of this account and the episodes whose cached audio is spare.

    Returns ``(rows, settled)``. An episode is settled when its audio is
    published and none of its calls is in flight:

    - a podcast guide has a published ``digest_audio_zh``. The database only
      lets that status stand for the episode's *current* narration: moving the
      narration pointer withdraws the audio (``podcast_script_audio_invalidate_*``
      triggers). The guide flow reuses a published audio of the current
      narration instead of synthesizing
      (``podcast_premium_guides._reusable_published_audio``), so these receipts
      are not read again. An episode whose new narration is synthesized but not
      published yet is not settled: its retry replays from this cache;
    - an article listen guide is ready. It writes a new narration on every run.
    """
    published = set(session.exec(select(PodcastArtifactRecord.episode_id).where(
        PodcastArtifactRecord.kind == "digest_audio_zh",
        PodcastArtifactRecord.status == "published",
    )).all())
    rows = session.exec(select(BailianTtsCallRecord).where(
        BailianTtsCallRecord.account_scope == config.account_scope,
    )).all()
    # 只有「本账号有成功回执、且还没被成品音频判为已发布」的文章才需要看
    # ``listen_guide``。此前这里整表加载全部文章(连正文),只为读这一个字段
    # (issue #318)。
    undecided = sorted({
        row.episode_id for row in rows
        if row.status == "succeeded" and row.episode_id not in published
    })
    for offset in range(0, len(undecided), _RECLAIM_ARTICLE_CHUNK):
        chunk = undecided[offset:offset + _RECLAIM_ARTICLE_CHUNK]
        for article_id, raw_extensions in session.exec(
            select(ArticleRecord.id, ArticleRecord.extensions_json)
            .where(ArticleRecord.id.in_(chunk))
        ).all():
            try:
                extensions = json.loads(raw_extensions or "{}")
            except (TypeError, ValueError):
                continue
            guide = extensions.get("listen_guide") if isinstance(extensions, dict) else None
            if not isinstance(guide, dict) or guide.get("status") != "ready":
                continue
            content_hash = str(guide.get("content_hash") or "").strip().lower()
            mime = str(guide.get("mime") or "").strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", content_hash) and mime:
                published.add(article_id)
    return rows, published - {row.episode_id for row in rows if _in_flight(row)}


def _drop_audio(root: Path, row_id: str, path: Path) -> None:
    """Delete cached audio on purpose and leave ``<row id>.evicted`` behind.

    The daily backup requires verified audio for every ``succeeded`` row and
    fails without it (``storage_backup``: the receipt directory holds paid
    audio that may not exist anywhere else). The marker tells it this audio was
    dropped deliberately, after its episode was published. It is written
    durably before the audio goes: a crash in between leaves both, which is
    harmless. The backup holds the same receipt lock, so it never sees a row
    with neither.
    """
    _atomic_write(root / f"{row_id}.evicted", b"")
    path.unlink()


def _evictable_files(root: Path, rows, settled, protect=frozenset()):
    """Cached audio of settled episodes as ``(created, row id, path, size)``."""
    found = []
    for row in rows:
        if row.status != "succeeded" or row.id in protect or row.episode_id not in settled:
            continue
        created = _created_at(row)
        if created is None:
            continue
        for suffix in (".wav.zlib", ".wav"):
            path = root / f"{row.id}{suffix}"
            try:
                found.append((created, row.id, path, path.stat().st_size))
            except OSError:  # 不存在,或在无锁的概览读取中途被回收删掉
                continue
    return found


def _reclaim_completed_receipts_unlocked(
    config: BailianSpeechConfig, engine, *, retention_days: int | None = 7,
    now: dt.datetime | None = None, drop_incompressible: bool = False,
    make_room_bytes: int | None = None, protect: frozenset = frozenset(),
) -> dict:
    """Free verified published WAVs. Caller must already hold the receipt ``.lock``.

    JSON provider receipts and database billing rows stay. The row remains
    ``succeeded``, so a later synthesize of the same narration does not POST
    again. Audio that is deleted rather than archived leaves an ``.evicted``
    marker (``_drop_audio``). In-flight episodes and episodes without a
    published replacement stay untouched, and so does every row in ``protect``
    (the chunks of the synthesis that is asking for room).

    The daily job keeps ``retention_days`` and only replaces a WAV with a
    smaller zlib archive. A capacity shortfall passes ``retention_days=None``
    and ``drop_incompressible=True``: age is ignored, and a hash-verified WAV
    is deleted even when zlib does not shrink and even when the file is larger
    than the cache limit (that file is what filled the cache).

    The capacity path also passes ``make_room_bytes``. Archives are never
    removed by compression, so without this the cache only grows until every
    synthesis fails the capacity check (issue #326). When that many bytes still
    do not fit after compressing, cached audio of settled episodes is deleted
    oldest first, and only as much as the shortfall needs. The published audio
    is the durable copy (see ``_reclaim_scope``). If evicting all of it would
    still not be enough, nothing is evicted: the request fails either way, and
    the cache keeps its replays.
    """
    root = Path(config.tts_receipt_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = now or dt.datetime.now(dt.timezone.utc)
    cutoff = None if retention_days is None else current - dt.timedelta(days=retention_days)
    compressed = 0
    removed = 0
    evicted = 0
    saved = 0
    with Session(engine) as session:
        rows, settled = _reclaim_scope(session, config)
        for row in rows:
            if row.status != "succeeded" or row.id in protect or row.episode_id not in settled:
                continue
            created = _created_at(row)
            if created is None or (cutoff is not None and created > cutoff):
                continue
            source = root / f"{row.id}.wav"
            receipt = root / f"{row.id}.json"
            archive = root / f"{row.id}.wav.zlib"
            if not source.is_file() or not receipt.is_file() or archive.exists():
                continue
            size = source.stat().st_size
            # Compressing a file larger than the cache reads it into memory for
            # no gain. Urgent reclaim still hash-checks and deletes it.
            if size > config.tts_cache_max_bytes:
                if not drop_incompressible or _file_sha256(source) != row.audio_hash:
                    continue
                _drop_audio(root, row.id, source)
                removed += 1
                saved += size
                continue
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != row.audio_hash:
                continue
            packed = zlib.compress(data, level=6)
            if len(packed) < len(data):
                _atomic_write(archive, packed)
                source.unlink()
                compressed += 1
                saved += len(data) - len(packed)
            elif drop_incompressible:
                _drop_audio(root, row.id, source)
                removed += 1
                saved += len(data)
        shortfall = 0 if make_room_bytes is None else _room_shortfall(root, config, make_room_bytes)
        if shortfall:
            candidates = _evictable_files(root, rows, settled, protect)
            if sum(size for *_rest, size in candidates) >= shortfall:
                for _created, row_id, path, size in sorted(candidates, key=lambda c: c[:2]):
                    _drop_audio(root, row_id, path)
                    evicted += 1
                    saved += size
                    shortfall -= size
                    if shortfall <= 0:
                        break
    if evicted:
        logger.info(
            "语音回执缓存不足,已逐出 %d 个已有成品的回执音频文件(连同压缩共腾出 %d 字节)",
            evicted, saved,
        )
    return {
        "compressed_wavs": compressed, "removed_wavs": removed,
        "evicted_files": evicted, "saved_bytes": saved,
    }


def reclaim_completed_receipts(
    config: BailianSpeechConfig, engine, *, retention_days: int = 7,
    now: dt.datetime | None = None, blocking: bool = True,
    urgent: bool = False,
) -> dict:
    """Reclaim under the receipt lock.

    ``blocking=False`` is the multi-worker maintenance path: if synthesis or
    another worker already holds ``.lock``, skip instead of waiting. Callers
    that already hold that lock must use ``_reclaim_completed_receipts_unlocked``;
    locking it again on a second file descriptor deadlocks.

    ``urgent=True`` is the capacity path: no age cutoff, and incompressible
    verified WAVs are deleted. The daily job leaves both at the conservative
    default so a compress-only pass cannot drop a WAV whose signed URL may
    already have expired.
    """
    root = Path(config.tts_receipt_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "wb") as lock:
        try:
            flock(lock, LOCK_EX if blocking else LOCK_EX | LOCK_NB)
        except BlockingIOError:
            return {
                "compressed_wavs": 0, "removed_wavs": 0, "evicted_files": 0,
                "saved_bytes": 0, "skipped": True,
            }
        return _reclaim_completed_receipts_unlocked(
            config, engine,
            retention_days=None if urgent else retention_days,
            now=now,
            drop_incompressible=urgent,
        )


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

    def capacity_snapshot(self, characters: int) -> dict:
        """Conservative PCM estimate, bounded by the final file limit."""
        root = Path(self.config.tts_receipt_root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # 24 kHz mono PCM is 48 KB/s; 12 KB/character allows 4 seconds per
        # character, above ordinary Chinese narration, without reserving 512 MiB.
        estimate = min(self.max_audio_bytes, max(1, characters) * 12_000 + 44)
        used = _cache_used_bytes(root)
        free = shutil.disk_usage(root).free
        return {
            "used_bytes": used, "limit_bytes": self.config.tts_cache_max_bytes,
            "reservation_bytes": estimate,
            "cache_shortfall_bytes": max(0, used + estimate - self.config.tts_cache_max_bytes),
            "disk_free_bytes": free, "disk_minimum_free_bytes": 1024**3,
            "disk_shortfall_bytes": max(0, estimate + 1024**3 - free),
        }

    def ensure_capacity(self, characters: int) -> dict:
        """Take the receipt lock, reclaim once if short, then recheck.

        Do not call this while the same ``.lock`` is already held. Synthesis
        uses ``_ensure_capacity_locked`` instead.
        """
        root = Path(self.config.tts_receipt_root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "wb") as lock:
            flock(lock, LOCK_EX)
            return self._ensure_capacity_locked(characters)

    def _ensure_capacity_locked(self, characters: int, protect: frozenset = frozenset()) -> dict:
        snapshot = self._capacity_after_one_reclaim(characters, protect)
        if snapshot["cache_shortfall_bytes"]:
            raise BailianSpeechError("tts_receipt_cache_full")
        if snapshot["disk_shortfall_bytes"]:
            raise BailianSpeechError("tts_receipt_disk_full")
        return snapshot

    def _capacity_after_one_reclaim(
        self, characters: int, protect: frozenset = frozenset(),
    ) -> dict:
        snapshot = self.capacity_snapshot(characters)
        if snapshot["cache_shortfall_bytes"] or snapshot["disk_shortfall_bytes"]:
            _reclaim_completed_receipts_unlocked(
                self.config, self.engine, now=self.clock(),
                retention_days=None, drop_incompressible=True,
                make_room_bytes=snapshot["reservation_bytes"], protect=protect,
            )
            snapshot = self.capacity_snapshot(characters)
        return snapshot

    def _require_bytes_locked(self, nbytes: int) -> None:
        """Post-download guard. One reclaim, then the same cache/disk checks.

        The chunk being stored is ``generated`` at this point, so its episode is
        in flight and the reclaim cannot touch this run's earlier chunks.
        """

        def room() -> tuple[bool, bool]:
            snapshot = self.capacity_snapshot(0)
            return (
                snapshot["used_bytes"] + nbytes <= self.config.tts_cache_max_bytes,
                snapshot["disk_free_bytes"] >= nbytes + 1024**3,
            )

        cache_ok, disk_ok = room()
        if not cache_ok or not disk_ok:
            _reclaim_completed_receipts_unlocked(
                self.config, self.engine, now=self.clock(),
                retention_days=None, drop_incompressible=True,
                make_room_bytes=nbytes,
            )
            cache_ok, disk_ok = room()
        if not cache_ok:
            raise BailianSpeechError("tts_receipt_cache_full")
        if not disk_ok:
            raise BailianSpeechError("tts_receipt_disk_full")

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
            # 从未发出的预留不占预算(issue #322)。``reserved`` 写在 ``authorized`` 之前,
            # 而 ``authorized`` 写在 POST 之前,所以停在 ``reserved`` 的分段一定没有
            # 远端副作用。调用方此刻持有回执目录锁,整个合成过程都在这把锁里,别的
            # 合成不可能在途——同账号下不属于本次合成的 ``reserved`` 都是被丢下的
            # (某段被拒后没发出去的后续分段、进程中途被杀)。解除它们的占用:行留着,
            # 占用额记 0;同一份口播稿以后再合成时在下面重新计价。``authorized`` /
            # ``generated`` 结果未知或已计费,不动。
            session.exec(
                update(BailianTtsCallRecord)
                .where(
                    BailianTtsCallRecord.account_scope == cfg.account_scope,
                    BailianTtsCallRecord.status == "reserved",
                    BailianTtsCallRecord.cost_minor > 0,
                    BailianTtsCallRecord.id.notin_(keys),
                )
                .values(cost_minor=0)
                .execution_options(synchronize_session=False)
            )
            existing = [session.get(BailianTtsCallRecord, key) for key in keys]
            new = []
            for key, chunk, row in zip(keys, chunks, existing):
                if row:
                    if row.status == "rejected":
                        raise BailianSpeechError("tts_previously_rejected")
                    if row.status == "reserved":
                        # 本次合成要发出的旧预留:按现在的账期与价格重新占用。它可能是
                        # 上面那种被解除过的(占用额 0),也可能是上个月留下的——钱是
                        # 现在花的,记在现在的账期。
                        row.budget_period = period
                        row.cost_minor = _reservation_cost_minor(
                            row.reserved_characters, cfg
                        )
                        row.price_minor = cfg.tts_price_minor
                        row.price_units = cfg.tts_price_units
                        row.pricing_revision = cfg.pricing_revision
                        session.add(row)
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
                        cost_minor=_reservation_cost_minor(units, cfg),
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

    def _release_unsent(self, keys) -> None:
        """本次合成没走完:解除其中从未发出的分段的预算占用(行保留,占用额记 0)。

        尽力而为——这一步写不进库(比如库正忙)也不该盖住合成本身的错误;下一次
        ``_reserve`` 会把它们当作被丢下的预留一并解除。
        """

        try:
            with Session(self.engine) as session:
                session.exec(sql_text("BEGIN IMMEDIATE"))
                session.exec(
                    update(BailianTtsCallRecord)
                    .where(
                        BailianTtsCallRecord.id.in_(keys),
                        BailianTtsCallRecord.status == "reserved",
                        BailianTtsCallRecord.cost_minor > 0,
                    )
                    .values(cost_minor=0)
                    .execution_options(synchronize_session=False)
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 - never mask the synthesis error
            logger.warning(
                "TTS 未发出的预留没能解除 episode=%s:%s: %s",
                self.episode_id, type(exc).__name__, exc,
            )

    def _row(self, key):
        with Session(self.engine) as session:
            row = session.get(BailianTtsCallRecord, key)
            session.expunge(row)
            return row

    def _authorize(self, key):
        """``reserved → authorized``,并核对这条预留的占用此刻仍然有效。

        发出请求这一步不依赖回执目录锁:占用额只在 ``_reserve`` 那个同时检查预算的
        事务里建立或恢复,这里在同一个写事务里确认它没有被解除过(占用额仍等于按该行
        自己的价格快照算出的数),不成立就失败关闭、不发请求。返回事务里核验过的那
        一行——结算必须用它的价格快照,而不是授权之前读到的旧值:别的合成可能在这
        两步之间按新价格重新占用过同一行。
        """

        with Session(self.engine) as session:
            session.exec(sql_text("BEGIN IMMEDIATE"))
            row = session.get(BailianTtsCallRecord, key)
            if (
                not row
                or row.status != "reserved"
                or row.cost_minor
                != _cost_minor(row.reserved_characters, row.price_minor, row.price_units)
            ):
                raise BailianSpeechError("tts_receipt_conflict")
            row.status = "authorized"
            session.add(row)
            session.commit()
            session.refresh(row)
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

    def _audio_from_receipt_url(self, receipt: Path, cfg, max_bytes: int) -> bytes:
        """Rebuild one succeeded chunk from its saved provider URL. No POST."""

        try:
            payload = json.loads(receipt.read_text())
        except (OSError, ValueError):
            raise BailianSpeechError("tts_cached_audio_missing", unknown=True) from None
        url = None
        if isinstance(payload, dict):
            output = payload.get("output")
            audio = output.get("audio") if isinstance(output, dict) else None
            if isinstance(audio, dict):
                url = audio.get("url")
        if not isinstance(url, str) or not url.strip():
            raise BailianSpeechError("tts_cached_audio_missing", unknown=True)
        data = asyncio.run(self.downloader(url, cfg, max_bytes=max_bytes))
        return join_wav([data], self.max_audio_bytes)

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
            flock(lock, LOCK_EX)
            with Session(self.engine) as session:
                needs_storage_chars = sum(len(chunk) for chunk, key in zip(chunks, keys)
                    if (row := session.get(BailianTtsCallRecord, key)) is None or row.status != "succeeded")
            if needs_storage_chars:
                # 此刻还没重新预留:上一次没走完的同一份口播稿,名下可能只剩「已成功」
                # 和「占用已解除」的行,账本上看不出在途。自己已有的分段明确排除在外。
                self._ensure_capacity_locked(needs_storage_chars, frozenset(keys))
            self._reserve(chunks, keys, self.clock())
            parts = []
            client = None
            try:
                client = self.client_factory(cfg)
                for key, chunk in zip(keys, chunks):
                    row = self._row(key)
                    receipt, audio_path = root / f"{key}.json", root / f"{key}.wav"
                    if row.status == "reserved":
                        row = self._authorize(key)
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
                            cost_minor=_cost_minor(
                                chars, row.price_minor, row.price_units
                            ),
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
                        self._require_bytes_locked(len(data))
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
                    archive = root / f"{key}.wav.zlib"
                    if audio_path.exists():
                        data = audio_path.read_bytes()
                    elif archive.exists():
                        decoder = zlib.decompressobj()
                        data = decoder.decompress(archive.read_bytes(), self.max_audio_bytes + 1)
                        if len(data) > self.max_audio_bytes or not decoder.eof or decoder.unused_data:
                            raise BailianSpeechError("tts_cached_audio_corrupt")
                    else:
                        # Urgent reclaim deletes a WAV that zlib cannot shrink
                        # and evicts old audio when the cache is still short.
                        # The published artifact is the durable copy. Replay
                        # fetches the receipt URL once, checks audio_hash, and
                        # does not POST or rewrite the WAV (rewriting would
                        # refill the cache just freed). An expired signed URL
                        # fails closed with no second charge.
                        try:
                            data = self._audio_from_receipt_url(
                                receipt, cfg, self.max_audio_bytes - sum(map(len, parts)),
                            )
                        except BailianSpeechError as exc:
                            # 这一段是被有意逐出的(issue #326):音频在已发布的成品里,
                            # 导读流程据此改用成品。没有标记的缺失是别的问题,原样上报。
                            if (root / f"{key}.evicted").is_file():
                                raise BailianSpeechError(EVICTED_AUDIO_CODE) from exc
                            raise
                    if hashlib.sha256(data).hexdigest() != row.audio_hash:
                        raise BailianSpeechError("tts_cached_audio_corrupt")
                    parts.append(data)
                return SynthesizedAudio(
                    data=join_wav(parts, self.max_audio_bytes),
                    mime="audio/wav",
                    provider_task_id=run_id,
                )
            except BaseException:
                # 某一段失败后,后面的分段不会再发出;不解除的话它们会永远停在
                # ``reserved`` 并跨月占着预算(issue #322)。
                self._release_unsent(keys)
                raise
            finally:
                if client is not None:
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
