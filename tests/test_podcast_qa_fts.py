"""Startup passage backfill must persist without committing caller transactions."""

from sqlalchemy import text

from storage.impl.db_storage import DatabaseStorage
from storage.podcast_qa_fts import ensure_podcast_qa_fts
from tests.test_podcast_qa import _seed, _search


def _match_count(connection):
    # An external-content FTS table's ordinary SELECT reads the backing table,
    # even if its inverted index is empty. MATCH is the important assertion.
    return connection.execute(text(
        "SELECT count(*) FROM podcast_qa_fts WHERE podcast_qa_fts MATCH 'restartneedle'"
    )).scalar_one()


def test_restart_rebuilds_and_commits_existing_passages(tmp_path):
    url = f"sqlite:///{tmp_path / 'restart.db'}"
    sink = DatabaseStorage(db_url=url)
    _seed(sink.engine, "restartneedle appears only in the original transcript")
    assert _search(sink.engine, ["restartneedle"])
    with sink.engine.begin() as conn:
        assert _match_count(conn) == 1
        assert conn.execute(text("SELECT count(*) FROM podcast_qa_chunks")).scalar_one() == 1
        conn.execute(text("DROP TABLE podcast_qa_fts"))
    sink.engine.dispose()

    restarted = DatabaseStorage(db_url=url)
    try:
        with restarted.engine.connect() as conn:
            assert _match_count(conn) == 1
        assert len(_search(restarted.engine, ["restartneedle"])) == 1
        # A second restart must not depend on the rebuild connection remaining open.
        restarted.engine.dispose()
        again = DatabaseStorage(db_url=url)
        try:
            with again.engine.connect() as conn:
                assert _match_count(conn) == 1
        finally:
            again.engine.dispose()
    finally:
        restarted.engine.dispose()


def test_connection_backfill_does_not_commit_callers_transaction(tmp_path):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'caller.db'}")
    _seed(sink.engine, "restartneedle appears only in the original transcript")
    assert _search(sink.engine, ["restartneedle"])
    with sink.engine.begin() as conn:
        conn.execute(text("DROP TABLE podcast_qa_fts"))
    with sink.engine.connect() as conn:
        transaction = conn.begin()
        # Start a real SQLite transaction before DDL under legacy transaction mode.
        conn.execute(text("UPDATE articles SET title='uncommitted title' WHERE id='episode'"))
        assert ensure_podcast_qa_fts(conn)
        assert _match_count(conn) == 1
        assert transaction.is_active
        transaction.rollback()
    with sink.engine.connect() as conn:
        assert conn.execute(text("SELECT title FROM articles WHERE id='episode'")).scalar_one() == "Technical discussion"
        assert conn.execute(text("SELECT count(*) FROM sqlite_master WHERE name='podcast_qa_fts'")).scalar_one() == 0
    sink.engine.dispose()
