"""The OSS-only schema boundary is reversible precisely when the registry is empty."""
from alembic import command
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, inspect, text

from storage.migrations import make_alembic_config


PREVIOUS = "b715a91c4e02"
OSS_REVISION = "c92a8f01d6b3"


def test_empty_registry_downgrade_preserves_business_data(tmp_path):
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    cfg = make_alembic_config(url)
    command.upgrade(cfg, PREVIOUS)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO app_settings (key, value) VALUES ('compatibility-check', 'preserved')"))
    command.upgrade(cfg, OSS_REVISION)
    command.downgrade(cfg, PREVIOUS)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == PREVIOUS
        assert "object_blobs" not in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT value FROM app_settings WHERE key='compatibility-check'")).scalar_one() == "preserved"
    command.upgrade(cfg, OSS_REVISION)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM object_blobs")).scalar_one() == 0
    engine.dispose()


def test_nonempty_registry_refuses_downgrade_before_ddl(tmp_path):
    url = f"sqlite:///{tmp_path / 'populated.db'}"
    cfg = make_alembic_config(url)
    command.upgrade(cfg, OSS_REVISION)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO object_blobs "
            "(id, namespace, content_hash, ext, size_bytes, mime, bucket, region, object_key, created_at) "
            "VALUES ('media:retained', 'media', :hash, '.png', 1, 'image/png', "
            "'example-bucket', 'ap-southeast-1', 'prod/media/retained.png', '2026-09-16')"
        ), {"hash": "a" * 64})
    with pytest.raises(RuntimeError, match="finalize-local"):
        command.downgrade(cfg, PREVIOUS)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == OSS_REVISION
        assert connection.execute(text("SELECT count(*) FROM object_blobs")).scalar_one() == 1
        assert "ix_object_blobs_namespace" in {index["name"] for index in inspect(connection).get_indexes("object_blobs")}
    engine.dispose()
