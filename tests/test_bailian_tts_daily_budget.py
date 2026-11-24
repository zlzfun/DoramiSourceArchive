"""No network: durable daily spend, request deadlines, and paid receipt recovery."""

import asyncio
import configparser
import datetime as dt
import io
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from config_bailian import BailianSpeechConfig, load_bailian_config
from models.db import BailianTtsCallRecord
from services.bailian_speech_client import BailianSpeechError
from services.bailian_tts import BailianPremiumGuideTtsProvider


@pytest.fixture
def case(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    now = [dt.datetime.now(dt.timezone.utc).replace(microsecond=0)]
    cfg = BailianSpeechConfig(
        api_key="fake-key", account_scope="test", tts_price_minor=1, tts_price_units=1,
        tts_monthly_budget_minor=100, tts_per_run_budget_minor=50,
        tts_daily_budget_minor=3, tts_receipt_root=str(tmp_path / "receipts"),
    )
    calls = []

    class Client:
        def __init__(self, config):
            pass

        def close(self):
            pass

        def synthesize(self, narration):
            calls.append(narration)
            return {"request_id": f"req-{len(calls)}", "usage": {"characters": len(narration)},
                    "output": {"audio": {"url": "https://result.oss-ap-southeast-1.aliyuncs.com/a.wav"}}}

    async def download(*args, **kwargs):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\x00\x00" * 100)
        return buffer.getvalue()

    def provider(config=None, **kwargs):
        return BailianPremiumGuideTtsProvider(
            config or cfg, engine=kwargs.pop("engine", engine), episode_id=kwargs.pop("episode_id", "episode"),
            voice_profile="narrator_zh", max_audio_bytes=100000,
            clock=lambda: now[0], client_factory=kwargs.pop("client_factory", Client),
            downloader=kwargs.pop("downloader", download), **kwargs,
        )

    yield engine, cfg, now, calls, Client, provider
    engine.dispose()


def seed(engine, now, *, key="old", status="succeeded", day=None, cost=1, scope="test"):
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    with Session(engine) as session:
        session.add(BailianTtsCallRecord(
            id=key, episode_id="old-episode", account_scope=scope,
            budget_period=local.strftime("%Y-%m"),
            budget_day=local.date().isoformat() if day is None else day,
            created_at=now.isoformat(), status=status, reserved_characters=cost,
            actual_characters=cost if status in {"succeeded", "generated"} else 0,
            cost_minor=cost, price_minor=1, price_units=1, pricing_revision="test",
        ))
        session.commit()


def test_daily_exact_limit_and_one_minor_over_make_no_extra_post(case):
    engine, cfg, now, calls, Client, provider = case
    asyncio.run(provider().synthesize("abc"))
    with pytest.raises(BailianSpeechError, match="tts_daily_budget_exceeded"):
        asyncio.run(provider().synthesize("d"))
    assert calls == ["abc"]
    with Session(engine) as session:
        assert session.exec(select(BailianTtsCallRecord)).one().cost_minor == 3


def test_next_day_releases_only_completed_dated_spend(case):
    engine, cfg, now, calls, Client, provider = case
    asyncio.run(provider().synthesize("abc"))
    now[0] += dt.timedelta(days=1)
    asyncio.run(provider().synthesize("def"))
    assert calls == ["abc", "def"]


@pytest.mark.parametrize("status,unknown_day", [("reserved", False), ("authorized", False), ("generated", False), ("succeeded", True)])
def test_unsettled_and_undated_legacy_receipts_keep_daily_budget(case, status, unknown_day):
    engine, cfg, now, calls, Client, provider = case
    seed(engine, now[0] - dt.timedelta(days=40), status=status, day="" if unknown_day else None, cost=3)
    with pytest.raises(BailianSpeechError, match="tts_daily_budget_exceeded"):
        asyncio.run(provider().synthesize("a"))
    assert calls == []


def test_other_scope_and_rejected_receipts_do_not_use_daily_budget(case):
    engine, cfg, now, calls, Client, provider = case
    seed(engine, now[0], key="other", cost=3, scope="different-account")
    seed(engine, now[0], key="rejected", status="rejected", cost=3)
    asyncio.run(provider().synthesize("abc"))
    assert calls == ["abc"]


def test_midnight_authorization_uses_new_day_and_rechecks_limit(case):
    engine, cfg, now, calls, Client, provider = case
    before = now[0].astimezone(ZoneInfo("Asia/Shanghai")).replace(hour=23, minute=59, second=59)
    after = before + dt.timedelta(seconds=2)
    now[0] = before
    seed(engine, after, cost=3)

    class CrossMidnight(Client):
        def __init__(self, config):
            now[0] = after

    with pytest.raises(BailianSpeechError, match="tts_daily_budget_exceeded"):
        asyncio.run(provider(client_factory=CrossMidnight).synthesize("a"))
    assert calls == []
    with Session(engine) as session:
        row = session.exec(select(BailianTtsCallRecord).where(BailianTtsCallRecord.id != "old")).one()
        assert row.status == "reserved"
    # The pending reservation is not lost. Resume it on the next free day.
    now[0] = after + dt.timedelta(days=1)
    asyncio.run(provider().synthesize("a"))
    with Session(engine) as session:
        row = session.exec(select(BailianTtsCallRecord).where(BailianTtsCallRecord.id != "old")).one()
        assert row.budget_day == now[0].date().isoformat()
        assert row.status == "succeeded"


def test_month_boundary_is_also_rechecked_before_post(case):
    engine, cfg, now, calls, Client, provider = case
    first = now[0].astimezone(ZoneInfo("Asia/Shanghai")).replace(day=1, hour=0, minute=0, second=1)
    now[0] = first - dt.timedelta(seconds=2)
    seed(engine, first, cost=1)

    class CrossMonth(Client):
        def __init__(self, config):
            now[0] = first

    with pytest.raises(BailianSpeechError, match="tts_monthly_budget_exceeded"):
        asyncio.run(provider(replace(cfg, tts_monthly_budget_minor=1), client_factory=CrossMonth).synthesize("a"))
    assert calls == []


def test_expiry_prevents_new_reservation_and_restart_post(case):
    engine, cfg, now, calls, Client, provider = case
    end = now[0] + dt.timedelta(seconds=1)
    bounded = replace(cfg, tts_entitlement_ends_at=end.isoformat())

    class ExpireBeforeAuthorization(Client):
        def __init__(self, config):
            now[0] = end

    with pytest.raises(BailianSpeechError, match="tts_entitlement_expired"):
        asyncio.run(provider(bounded, client_factory=ExpireBeforeAuthorization).synthesize("a"))
    with pytest.raises(BailianSpeechError, match="tts_entitlement_expired"):
        asyncio.run(provider(bounded).synthesize("a"))
    with pytest.raises(BailianSpeechError, match="tts_entitlement_expired"):
        asyncio.run(provider(bounded).synthesize("new"))
    assert calls == []
    with Session(engine) as session:
        assert session.exec(select(BailianTtsCallRecord)).one().status == "reserved"


def test_deadline_is_checked_between_chunks(case):
    engine, cfg, now, calls, Client, provider = case
    end = now[0] + dt.timedelta(seconds=1)
    bounded = replace(cfg, tts_chunk_chars=1, tts_entitlement_ends_at=end.isoformat())

    class ExpireAfterFirstPost(Client):
        def synthesize(self, narration):
            result = super().synthesize(narration)
            now[0] = end
            return result

    with pytest.raises(BailianSpeechError, match="tts_entitlement_expired"):
        asyncio.run(provider(bounded, client_factory=ExpireAfterFirstPost).synthesize("ab"))
    assert calls == ["a"]
    with Session(engine) as session:
        assert sorted(row.status for row in session.exec(select(BailianTtsCallRecord))) == ["reserved", "succeeded"]


def test_generated_download_and_succeeded_replay_work_after_expiry(case):
    engine, cfg, now, calls, Client, provider = case

    async def fail_download(*args, **kwargs):
        raise BailianSpeechError("download_failed")

    with pytest.raises(BailianSpeechError, match="download_failed"):
        asyncio.run(provider(downloader=fail_download).synthesize("abc"))
    expired = replace(cfg, tts_daily_budget_minor=1, tts_per_run_budget_minor=1,
                      tts_monthly_budget_minor=1, tts_entitlement_ends_at=now[0].isoformat())
    restored = asyncio.run(provider(expired).synthesize("abc"))
    replayed = asyncio.run(provider(expired).synthesize("abc"))
    assert restored.data == replayed.data and calls == ["abc"]


def test_reserved_restart_rechecks_lowered_per_run_limit(case):
    engine, cfg, now, calls, Client, provider = case

    class InterruptedBeforePost(Client):
        def __init__(self, config):
            raise RuntimeError("simulated restart after reservation")

    with pytest.raises(RuntimeError, match="simulated restart"):
        asyncio.run(provider(client_factory=InterruptedBeforePost).synthesize("abc"))
    with pytest.raises(BailianSpeechError, match="tts_per_run_budget_exceeded"):
        asyncio.run(provider(replace(cfg, tts_per_run_budget_minor=2)).synthesize("abc"))
    assert calls == []


def test_independent_database_sessions_cannot_overreserve_daily_budget(case):
    engine, cfg, now, calls, Client, provider = case
    gate = threading.Barrier(2)

    def reserve(key):
        separate = create_engine(engine.url, connect_args={"check_same_thread": False})
        try:
            worker = provider(engine=separate, episode_id=key)
            gate.wait(timeout=5)
            try:
                worker._reserve(["ab"], [key], now[0])
                return "reserved"
            except BailianSpeechError as exc:
                return str(exc)
        finally:
            separate.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, ["first", "second"]))
    assert outcomes.count("reserved") == 1
    assert sum("tts_daily_budget_exceeded" in result for result in outcomes) == 1
    with Session(engine) as session:
        assert session.exec(select(BailianTtsCallRecord)).one().cost_minor == 2


def test_daily_controls_ini_environment_and_runtime_resolution(case, monkeypatch):
    from types import SimpleNamespace
    from services import bailian_speech_config, credentials

    engine, cfg, now, calls, Client, provider = case
    parser = configparser.ConfigParser()
    parser.read_dict({"bailian_speech": {"tts_daily_budget_minor": "2000"}})
    deadline = (now[0] + dt.timedelta(days=3)).isoformat()
    monkeypatch.setenv("DORAMI_BAILIAN_TTS_ENTITLEMENT_ENDS_AT", deadline)
    loaded = load_bailian_config(parser)
    assert loaded.tts_daily_budget_minor == 2000 and loaded.tts_entitlement_ends_at == deadline
    monkeypatch.setattr(bailian_speech_config.config, "settings", SimpleNamespace(bailian_speech=loaded))
    monkeypatch.setattr(bailian_speech_config.config, "_read_config_file", lambda: parser)
    with Session(engine) as session:
        credentials.save_updates(session, bailian_speech_config.NAMESPACE, {"tts_daily_budget_minor": 1500})
        resolved = bailian_speech_config.resolve_bailian(session)
        assert resolved.tts_daily_budget_minor == 1500
        assert resolved.tts_entitlement_ends_at == deadline


@pytest.mark.parametrize("value", ["not-a-date", "2027-01-01", "2027-01-01T18:00:00"])
def test_invalid_or_timezone_less_tts_deadlines_fail_closed(value):
    with pytest.raises(ValueError, match="timezone-aware"):
        BailianSpeechConfig(tts_entitlement_ends_at=value)


def test_default_controls_preserve_existing_behavior(case):
    engine, cfg, now, calls, Client, provider = case
    assert BailianSpeechConfig().tts_daily_budget_minor == 0
    assert BailianSpeechConfig().tts_entitlement_ends_at == ""
    seed(engine, now[0], cost=4)
    asyncio.run(provider(replace(cfg, tts_daily_budget_minor=0)).synthesize("abc"))
    assert calls == ["abc"]


def test_migration_preserves_historical_spend_without_inventing_day(tmp_path):
    from alembic import command
    from storage.migrations import make_alembic_config

    url = f"sqlite:///{tmp_path / 'old.db'}"
    migration_config = make_alembic_config(url)
    command.upgrade(migration_config, "a63f1d2c9b70")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("""INSERT INTO bailian_tts_calls
                (id, episode_id, account_scope, budget_period, created_at, status,
                 reserved_characters, actual_characters, cost_minor, price_minor,
                 price_units, pricing_revision, request_id, audio_hash)
                VALUES ('old', 'episode', 'scope', '2026-01', '2026-01-01T00:00:00Z',
                        'generated', 3, 2, 2, 1, 1, 'test', 'req-1', '')"""))
        command.upgrade(migration_config, "b8d2f4a6c901")
        with Session(engine) as session:
            row = session.get(BailianTtsCallRecord, "old")
            assert row.budget_day == ""
            assert (row.status, row.cost_minor, row.request_id) == ("generated", 2, "req-1")
    finally:
        engine.dispose()
