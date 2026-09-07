"""Commit-ordered Archive Sync entity revisions for the SQLite archive store.

The revision is allocated only when a business write reaches SQLite and in the
same transaction as that write.  An exporter therefore sees both the entity
state and its revision, or neither.  This removes the pre-flush hole inherent in
wall-clock watermarks and also observes bulk SQL writes that bypass ORM hooks.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.engine import Connection


_CLOCK_INIT = "INSERT OR IGNORE INTO archive_sync_clock (id, revision) VALUES (1, 0)"


def _public_source(alias: str) -> str:
    return f"{alias}.owner_username = '' AND {alias}.collection_authority_id = ''"


def _public_article(alias: str) -> str:
    return (
        f"{alias}.analysis_authority_id = '' "
        f"AND {alias}.source_id NOT LIKE 'user\\_rss\\_%' ESCAPE '\\' "
        "AND (NOT EXISTS (SELECT 1 FROM source_configs sc "
        f"WHERE sc.source_id = {alias}.source_id) OR EXISTS ("
        "SELECT 1 FROM source_configs sc "
        f"WHERE sc.source_id = {alias}.source_id "
        "AND sc.owner_username = '' AND sc.collection_authority_id = ''))"
    )


def _state_values(stream: str, identity: str, operation: str, clock: str, now: str) -> str:
    return f"""
        INSERT INTO archive_sync_entity_states(
          stream, identity, authority_id, revision, operation, updated_at
        ) VALUES ('{stream}', {identity}, '', {clock}, '{operation}', {now})
        ON CONFLICT(stream, identity) DO UPDATE SET
          authority_id = '', revision = excluded.revision,
          operation = excluded.operation, updated_at = excluded.updated_at;
    """


def _trigger_sql() -> Iterable[tuple[str, str]]:
    now = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
    clock = "(SELECT revision FROM archive_sync_clock WHERE id = 1)"
    tick = "UPDATE archive_sync_clock SET revision = revision + 1 WHERE id = 1;"

    yield "archive_sync_source_insert", f"""
        CREATE TRIGGER archive_sync_source_insert AFTER INSERT ON source_configs
        WHEN {_public_source('NEW')}
        BEGIN
          {tick}
          {_state_values('sources', 'NEW.source_id', 'upsert', clock, now)}
        END
    """
    yield "archive_sync_source_update", f"""
        CREATE TRIGGER archive_sync_source_update AFTER UPDATE ON source_configs
        WHEN {_public_source('NEW')}
        BEGIN
          {tick}
          {_state_values('sources', 'NEW.source_id', 'upsert', clock, now)}
        END
    """
    yield "archive_sync_source_delete", f"""
        CREATE TRIGGER archive_sync_source_delete AFTER DELETE ON source_configs
        WHEN {_public_source('OLD')}
        BEGIN
          {tick}
          {_state_values('sources', 'OLD.source_id', 'tombstone', clock, now)}
          DELETE FROM source_states
          WHERE source_id = OLD.source_id AND authority_id = '';
        END
    """
    yield "archive_sync_source_nonpublic_insert", """
        CREATE TRIGGER archive_sync_source_nonpublic_insert AFTER INSERT ON source_configs
        WHEN NEW.owner_username <> '' OR NEW.collection_authority_id <> ''
        BEGIN
          DELETE FROM archive_sync_entity_states
          WHERE authority_id = '' AND (
            (stream IN ('sources','source_states') AND identity = NEW.source_id) OR
            (stream IN ('articles','analyses') AND identity IN (
              SELECT id FROM articles WHERE source_id = NEW.source_id
            ))
          );
        END
    """
    yield "archive_sync_source_remote_handoff", """
        CREATE TRIGGER archive_sync_source_remote_handoff AFTER UPDATE ON source_configs
        WHEN NEW.collection_authority_id <> ''
        BEGIN
          DELETE FROM archive_sync_entity_states
          WHERE authority_id = '' AND (
            (stream IN ('sources','source_states') AND identity = NEW.source_id) OR
            (stream IN ('articles','analyses') AND identity IN (
              SELECT id FROM articles WHERE source_id = NEW.source_id
            ))
          );
        END
    """
    yield "archive_sync_source_scope_exit", f"""
        CREATE TRIGGER archive_sync_source_scope_exit AFTER UPDATE ON source_configs
        WHEN {_public_source('OLD')} AND NEW.owner_username <> ''
          AND NEW.collection_authority_id = ''
        BEGIN
          {tick}
          {_state_values('sources', 'NEW.source_id', 'tombstone', clock, now)}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'articles', a.id, '', {clock}, 'tombstone', {now}
          FROM articles a WHERE a.source_id = NEW.source_id AND a.analysis_authority_id = ''
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'analyses', aa.article_id, '', {clock}, 'tombstone', {now}
          FROM article_analyses aa JOIN articles a ON a.id = aa.article_id
          WHERE a.source_id = NEW.source_id AND aa.authority_id = ''
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'source_states', ss.source_id, '', {clock}, 'tombstone', {now}
          FROM source_states ss WHERE ss.source_id = NEW.source_id AND ss.authority_id = ''
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """
    yield "archive_sync_source_scope_enter", f"""
        CREATE TRIGGER archive_sync_source_scope_enter AFTER UPDATE ON source_configs
        WHEN NOT ({_public_source('OLD')}) AND {_public_source('NEW')}
        BEGIN
          {tick}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'articles', a.id, '', {clock}, 'upsert', {now}
          FROM articles a WHERE a.source_id = NEW.source_id AND a.analysis_authority_id = ''
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'analyses', aa.article_id, '', {clock}, 'upsert', {now}
          FROM article_analyses aa JOIN articles a ON a.id = aa.article_id
          WHERE a.source_id = NEW.source_id AND aa.authority_id = ''
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'source_states', ss.source_id, '', {clock}, 'upsert', {now}
          FROM source_states ss WHERE ss.source_id = NEW.source_id AND ss.authority_id = ''
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """

    article_public_new = _public_article("NEW")
    article_public_old = _public_article("OLD")
    yield "archive_sync_article_insert", f"""
        CREATE TRIGGER archive_sync_article_insert AFTER INSERT ON articles
        WHEN {article_public_new}
        BEGIN
          {tick}
          {_state_values('articles', 'NEW.id', 'upsert', clock, now)}
        END
    """
    yield "archive_sync_article_update", f"""
        CREATE TRIGGER archive_sync_article_update AFTER UPDATE ON articles
        WHEN {article_public_new}
          AND (
            NEW.title IS NOT OLD.title OR NEW.content_type IS NOT OLD.content_type OR
            NEW.source_id IS NOT OLD.source_id OR NEW.source_url IS NOT OLD.source_url OR
            NEW.publish_date IS NOT OLD.publish_date OR NEW.fetched_date IS NOT OLD.fetched_date OR
            NEW.fetch_run_id IS NOT OLD.fetch_run_id OR NEW.job_id IS NOT OLD.job_id OR
            NEW.job_run_id IS NOT OLD.job_run_id OR
            NEW.source_group_id IS NOT OLD.source_group_id OR
            NEW.run_scope IS NOT OLD.run_scope OR NEW.has_content IS NOT OLD.has_content OR
            NEW.content IS NOT OLD.content OR NEW.extensions_json IS NOT OLD.extensions_json
          )
        BEGIN
          {tick}
          {_state_values('articles', 'NEW.id', 'upsert', clock, now)}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'analyses', NEW.id, '', {clock}, 'upsert', {now}
          WHERE EXISTS (
            SELECT 1 FROM article_analyses aa
            WHERE aa.article_id = NEW.id AND aa.authority_id = ''
          )
          ON CONFLICT(stream, identity) DO UPDATE SET
            authority_id = '', revision = excluded.revision,
            operation = excluded.operation, updated_at = excluded.updated_at;
        END
    """
    yield "archive_sync_article_scope_exit", f"""
        CREATE TRIGGER archive_sync_article_scope_exit AFTER UPDATE ON articles
        WHEN {article_public_old} AND NOT ({article_public_new})
          AND NEW.analysis_authority_id = ''
        BEGIN
          {tick}
          {_state_values('articles', 'OLD.id', 'tombstone', clock, now)}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'analyses', OLD.id, '', {clock}, 'tombstone', {now}
          WHERE EXISTS (
            SELECT 1 FROM article_analyses aa
            WHERE aa.article_id = OLD.id AND aa.authority_id = ''
          )
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """
    yield "archive_sync_article_scope_enter", f"""
        CREATE TRIGGER archive_sync_article_scope_enter AFTER UPDATE ON articles
        WHEN NOT ({article_public_old}) AND {article_public_new}
        BEGIN
          {tick}
          {_state_values('articles', 'NEW.id', 'upsert', clock, now)}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'analyses', NEW.id, '', {clock}, 'upsert', {now}
          WHERE EXISTS (
            SELECT 1 FROM article_analyses aa
            WHERE aa.article_id = NEW.id AND aa.authority_id = ''
          )
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """
    yield "archive_sync_article_remote_handoff", """
        CREATE TRIGGER archive_sync_article_remote_handoff AFTER UPDATE ON articles
        WHEN NEW.analysis_authority_id <> ''
        BEGIN
          DELETE FROM archive_sync_entity_states
          WHERE authority_id = '' AND identity = NEW.id
            AND stream IN ('articles','analyses');
        END
    """
    yield "archive_sync_article_delete", f"""
        CREATE TRIGGER archive_sync_article_delete BEFORE DELETE ON articles
        WHEN {article_public_old}
        BEGIN
          {tick}
          {_state_values('articles', 'OLD.id', 'tombstone', clock, now)}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'analyses', OLD.id, '', {clock}, 'tombstone', {now}
          WHERE EXISTS (
            SELECT 1 FROM article_analyses aa
            WHERE aa.article_id = OLD.id AND aa.authority_id = ''
          )
          ON CONFLICT(stream, identity) DO UPDATE SET
            authority_id = '', revision = excluded.revision,
            operation = excluded.operation, updated_at = excluded.updated_at;
        END
    """

    analysis_scope = (
        "NEW.authority_id = '' AND EXISTS (SELECT 1 FROM articles a "
        f"WHERE a.id = NEW.article_id AND {_public_article('a')})"
    )
    for suffix, action in (("insert", "INSERT"), ("update", "UPDATE")):
        yield f"archive_sync_analysis_{suffix}", f"""
            CREATE TRIGGER archive_sync_analysis_{suffix}
            AFTER {action} ON article_analyses
            WHEN {analysis_scope}
            BEGIN
              {tick}
              {_state_values('analyses', 'NEW.article_id', 'upsert', clock, now)}
            END
        """
    yield "archive_sync_analysis_delete", f"""
        CREATE TRIGGER archive_sync_analysis_delete AFTER DELETE ON article_analyses
        WHEN OLD.authority_id = '' AND EXISTS (
          SELECT 1 FROM articles a WHERE a.id = OLD.article_id AND {_public_article('a')}
        )
        BEGIN
          {tick}
          DELETE FROM article_tag_assignments
          WHERE article_id = OLD.article_id AND assignment_source = 'llm';
          {_state_values('analyses', 'OLD.article_id', 'tombstone', clock, now)}
        END
    """

    assignment_specs = (
        ("insert", "INSERT", "NEW"),
        ("update", "UPDATE", "NEW"),
        ("delete", "DELETE", "OLD"),
    )
    for suffix, action, alias in assignment_specs:
        yield f"archive_sync_assignment_{suffix}", f"""
            CREATE TRIGGER archive_sync_assignment_{suffix}
            AFTER {action} ON article_tag_assignments
            WHEN {alias}.assignment_source = 'llm' AND EXISTS (
              SELECT 1 FROM article_analyses aa JOIN articles a ON a.id = aa.article_id
              WHERE aa.article_id = {alias}.article_id
                AND aa.authority_id = '' AND {_public_article('a')}
            )
            BEGIN
              {tick}
              {_state_values('analyses', f'{alias}.article_id', 'upsert', clock, now)}
            END
        """

    yield "archive_sync_media_insert", f"""
        CREATE TRIGGER archive_sync_media_insert AFTER INSERT ON media_assets
        WHEN NEW.sync_authority_id = ''
        BEGIN
          {tick}
          {_state_values('media', 'NEW.url_hash', 'upsert', clock, now)}
        END
    """
    yield "archive_sync_media_update", f"""
        CREATE TRIGGER archive_sync_media_update AFTER UPDATE ON media_assets
        WHEN NEW.sync_authority_id = ''
        BEGIN
          {tick}
          {_state_values('media', 'NEW.url_hash', 'upsert', clock, now)}
        END
    """

    state_public_new = (
        "NEW.authority_id = '' AND NEW.source_id NOT LIKE 'user\\_rss\\_%' ESCAPE '\\' "
        "AND (NOT EXISTS (SELECT 1 FROM source_configs sc WHERE sc.source_id = NEW.source_id) "
        "OR EXISTS (SELECT 1 FROM source_configs sc WHERE sc.source_id = NEW.source_id "
        "AND sc.owner_username = '' AND sc.collection_authority_id = ''))"
    )
    state_public_old = state_public_new.replace("NEW.", "OLD.")
    for suffix, action in (("insert", "INSERT"), ("update", "UPDATE")):
        yield f"archive_sync_source_state_{suffix}", f"""
            CREATE TRIGGER archive_sync_source_state_{suffix}
            AFTER {action} ON source_states
            WHEN {state_public_new}
            BEGIN
              {tick}
              {_state_values('source_states', 'NEW.source_id', 'upsert', clock, now)}
            END
        """
    yield "archive_sync_source_state_delete", f"""
        CREATE TRIGGER archive_sync_source_state_delete AFTER DELETE ON source_states
        WHEN {state_public_old}
        BEGIN
          {tick}
          {_state_values('source_states', 'OLD.source_id', 'tombstone', clock, now)}
        END
    """


def install_archive_sync_revision_triggers(connection: Connection) -> None:
    """Install or refresh all revision triggers on a SQLite connection."""

    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql(_CLOCK_INIT)
    for name, statement in _trigger_sql():
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
        connection.exec_driver_sql(statement)
