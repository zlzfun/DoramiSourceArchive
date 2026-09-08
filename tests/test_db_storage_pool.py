from sqlalchemy.pool import NullPool

from storage.impl.db_storage import DatabaseStorage


def test_file_sqlite_uses_unpooled_connections(tmp_path):
    storage = DatabaseStorage(f"sqlite:///{tmp_path / 'pool.db'}")

    assert isinstance(storage.engine.pool, NullPool)


def test_memory_sqlite_keeps_shared_connection_pool():
    storage = DatabaseStorage("sqlite:///:memory:")

    assert not isinstance(storage.engine.pool, NullPool)
