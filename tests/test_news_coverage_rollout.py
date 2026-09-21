"""Rollout changes are offline, explicit, idempotent and reversible."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest

spec = importlib.util.spec_from_file_location('coverage_rollout', Path(__file__).resolve().parents[1] / 'scripts/configure_news_coverage.py')
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)


@pytest.fixture
def database(tmp_path):
    from storage.impl.db_storage import DatabaseStorage
    from models.db import CollectionJobRecord, SourceConfigRecord
    from sqlmodel import Session
    path = tmp_path / 'cms.db'
    sink = DatabaseStorage(f'sqlite:///{path}')
    with Session(sink.engine) as session:
        for source in ('web_ithome_ai', 'rss_hn_ai'):
            session.add(SourceConfigRecord(source_id=source, name=source, created_at='2026-09-01', updated_at='2026-09-01'))
        session.add(CollectionJobRecord(name='morning', fetcher_ids_json='["web_ithome_ai"]',
            cron_expr='0 8 * * *', created_at='2026-09-01', updated_at='2026-09-01'))
        session.commit()
    sink.engine.dispose()
    return path


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def test_preview_apply_repeat_and_rollback(database, tmp_path, capsys):
    with connect(database) as conn:
        rollout.write_setting(conn, rollout.SCOPE, '["web_ithome_ai"]')
    rollout.main(['--database', str(database)])
    with connect(database) as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0] == 1
        assert rollout.setting(conn, rollout.SCOPE) == '["web_ithome_ai"]'
    snapshot = tmp_path / 'snapshot.json'
    argv = ['--database', str(database), '--apply', '--offline', '--snapshot', str(snapshot)]
    rollout.main(argv)
    receipt = snapshot.read_text()
    rollout.main(argv)
    assert snapshot.read_text() == receipt
    with connect(database) as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0] == 2
        assert json.loads(rollout.setting(conn, rollout.SCOPE)) == ['web_ithome_ai', 'rss_hn_ai']
    undo = ['--database', str(database), '--apply', '--offline', '--rollback', str(snapshot)]
    rollout.main(undo)
    rollout.main(undo)
    with connect(database) as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0] == 1
        assert rollout.setting(conn, rollout.SCOPE) == '["web_ithome_ai"]'
        assert rollout.setting(conn, rollout.REVISION) is None


def test_scope_all_unchanged_and_modified_job_not_overwritten(database):
    with connect(database) as conn:
        conn.execute('DELETE FROM source_configs')  # built-ins need no override rows
        for value in (None, '', 'null', '[]'):
            rollout.write_setting(conn, rollout.SCOPE, value)
            changes = rollout.plan(conn)
            assert changes['settings_after'][rollout.SCOPE] == value
        row = {**rollout.JOB, 'cron_expr': '0 12 * * *', 'created_at': '', 'updated_at': ''}
        conn.execute(f'INSERT INTO collection_jobs({",".join(row)}) VALUES ({",".join("?" for _ in row)})', list(row.values()))
        with pytest.raises(ValueError, match='拒绝覆盖'): rollout.plan(conn)


def test_rollback_refuses_newer_operator_configuration(database, tmp_path):
    snapshot = tmp_path / 'snapshot.json'
    rollout.main(['--database', str(database), '--apply', '--offline', '--snapshot', str(snapshot)])
    with connect(database) as conn:
        rollout.write_setting(conn, rollout.SCOPE, '["new-preference"]')
    with pytest.raises(ValueError, match='发生变化'):
        rollout.main(['--database', str(database), '--apply', '--offline', '--rollback', str(snapshot)])
    with connect(database) as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0] == 2


def test_apply_requires_offline_and_snapshot_failure_rolls_back(database, tmp_path):
    with pytest.raises(SystemExit): rollout.main(['--database', str(database), '--apply'])
    with pytest.raises(FileNotFoundError):
        rollout.main(['--database', str(database), '--apply', '--offline', '--snapshot', str(tmp_path / 'missing' / 'snap.json')])
    with connect(database) as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0] == 1
