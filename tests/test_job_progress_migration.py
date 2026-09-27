from alembic import command
from sqlalchemy import create_engine, inspect, text

from storage.migrations import make_alembic_config


def test_progress_migration_preserves_old_job_and_downgrades(tmp_path):
    url = f"sqlite:///{tmp_path / 'jobs.db'}"
    config = make_alembic_config(url)
    command.upgrade(config, "d17e9a4c2b61")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO jobs (id, type, status, processed, payload_json, "
                          "created_at) VALUES ('old', 'remote_archive_sync', 'failed', 17, '{}', 1)"))
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT processed, progress_json FROM jobs WHERE id='old'")).one() == (17, None)
    command.downgrade(config, "d17e9a4c2b61")
    assert "progress_json" not in {col["name"] for col in inspect(engine).get_columns("jobs")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT processed FROM jobs WHERE id='old'")).scalar_one() == 17
    engine.dispose()
