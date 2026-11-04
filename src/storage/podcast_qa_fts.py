"""Local transcript passage index and publication invalidation (Issue #248)."""

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from storage.fts import _as_connection, _sql_normalize_expr

logger = logging.getLogger(__name__)
FTS_TABLE = "podcast_qa_fts"


def ensure_podcast_qa_fts(bind) -> bool:
    """Install invalidation and commit startup backfill; retain caller transactions.

    Engine callers own a short transaction, as in ensure_fts. A supplied
    Connection/Session belongs to its caller (including Alembic), so never commit
    it here. Without this distinction startup could retain the DDL but roll back
    the passage INSERT, leaving a present yet silently empty FTS index.
    """
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            return ensure_podcast_qa_fts(conn)
    with _as_connection(bind) as conn:
        if conn.dialect.name != "sqlite":
            return False
        # Publication is the one lifecycle boundary shared by local processing,
        # translation and Archive Sync. No producer-specific hooks are required.
        for operation, reference in (("INSERT", "new"), ("DELETE", "old")):
            conn.execute(text(f"""CREATE TRIGGER IF NOT EXISTS podcast_qa_publication_{operation.lower()}
                AFTER {operation} ON podcast_text_publications BEGIN
                  DELETE FROM podcast_qa_indexes WHERE artifact_id = {reference}.artifact_id;
                END"""))
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS podcast_qa_publication_update
            AFTER UPDATE ON podcast_text_publications
            WHEN old.artifact_id IS NOT new.artifact_id OR old.status IS NOT new.status
              OR old.authority_id IS NOT new.authority_id OR old.published_at IS NOT new.published_at
            BEGIN
              DELETE FROM podcast_qa_indexes WHERE artifact_id IN (old.artifact_id, new.artifact_id);
            END"""))
        # Explicit cleanup also covers connections with foreign_keys disabled.
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS podcast_qa_index_delete
            AFTER DELETE ON podcast_qa_indexes BEGIN
              DELETE FROM podcast_qa_chunks WHERE artifact_id = old.artifact_id;
            END"""))
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS podcast_qa_episode_delete
            BEFORE DELETE ON articles BEGIN
              DELETE FROM podcast_qa_indexes WHERE episode_id = old.id;
              DELETE FROM podcast_qa_chunks WHERE episode_id = old.id;
            END"""))
        try:
            existed = conn.execute(text("SELECT 1 FROM sqlite_master WHERE name=:name"), {"name": FTS_TABLE}).first()
            conn.execute(text(f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(content, content='podcast_qa_chunks', content_rowid='id', tokenize='trigram')"))
            new = _sql_normalize_expr("new.content")
            old = _sql_normalize_expr("old.content")
            conn.execute(text(f"""CREATE TRIGGER IF NOT EXISTS podcast_qa_chunks_ai AFTER INSERT ON podcast_qa_chunks BEGIN
                INSERT INTO {FTS_TABLE}(rowid,content) VALUES(new.id,{new}); END"""))
            conn.execute(text(f"""CREATE TRIGGER IF NOT EXISTS podcast_qa_chunks_ad AFTER DELETE ON podcast_qa_chunks BEGIN
                INSERT INTO {FTS_TABLE}({FTS_TABLE},rowid,content) VALUES('delete',old.id,{old}); END"""))
            conn.execute(text(f"""CREATE TRIGGER IF NOT EXISTS podcast_qa_chunks_au AFTER UPDATE ON podcast_qa_chunks BEGIN
                INSERT INTO {FTS_TABLE}({FTS_TABLE},rowid,content) VALUES('delete',old.id,{old});
                INSERT INTO {FTS_TABLE}(rowid,content) VALUES(new.id,{new}); END"""))
            if not existed:
                normalized = _sql_normalize_expr("content")
                conn.execute(text(f"INSERT INTO {FTS_TABLE}(rowid,content) SELECT id,{normalized} FROM podcast_qa_chunks"))
            return True
        except Exception as exc:
            logger.warning("Podcast passage FTS unavailable; using passage LIKE: %s", exc)
            return False


def drop_podcast_qa_fts(bind) -> None:
    with _as_connection(bind) as conn:
        if conn.dialect.name != "sqlite":
            return
        for name in ("publication_insert", "publication_delete", "publication_update", "index_delete", "episode_delete", "chunks_ai", "chunks_ad", "chunks_au"):
            conn.execute(text(f"DROP TRIGGER IF EXISTS podcast_qa_{name}"))
        conn.execute(text(f"DROP TABLE IF EXISTS {FTS_TABLE}"))
