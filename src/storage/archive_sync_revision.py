"""Commit-ordered Archive Sync entity revisions for the SQLite archive store.

The revision is allocated only when a business write reaches SQLite and in the
same transaction as that write.  An exporter therefore sees both the entity
state and its revision, or neither.  This removes the pre-flush hole inherent in
wall-clock watermarks and also observes bulk SQL writes that bypass ORM hooks.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import inspect
from sqlalchemy.engine import Connection


_CLOCK_INIT = "INSERT OR IGNORE INTO archive_sync_clock (id, revision) VALUES (1, 0)"


def _public_source(alias: str) -> str:
    # issue #263:源上的 collection_authority_id 降级为簿记(「上游也有」),不再决定一行算不算
    # 本机公共——否则本机在一个上游也有的源下自己抓到的文章不会被记账,也就导不出去。
    return f"{alias}.owner_username = ''"


def _public_article(alias: str) -> str:
    return (
        f"{alias}.analysis_authority_id = '' "
        f"AND {alias}.source_id NOT LIKE 'user\\_rss\\_%' ESCAPE '\\' "
        "AND (NOT EXISTS (SELECT 1 FROM source_configs sc "
        f"WHERE sc.source_id = {alias}.source_id) OR EXISTS ("
        "SELECT 1 FROM source_configs sc "
        f"WHERE sc.source_id = {alias}.source_id "
        "AND sc.owner_username = ''))"
    )


def _public_podcast_text_publication(alias: str) -> str:
    return (
        f"{alias}.authority_id = '' AND {alias}.status = 'published' "
        "AND EXISTS (SELECT 1 FROM articles a "
        f"WHERE a.id = {alias}.episode_id AND {_public_article('a')})"
    )


def _public_podcast_audio(alias: str) -> str:
    return (
        f"{alias}.authority_id = '' AND {alias}.kind = 'digest_audio_zh' "
        f"AND {alias}.status = 'published' AND {alias}.published_at IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM articles a "
        f"WHERE a.id = {alias}.episode_id AND {_public_article('a')})"
    )


def _state_values(
    stream: str, identity: str, operation: str, clock: str, now: str
) -> str:
    return f"""
        INSERT INTO archive_sync_entity_states(
          stream, identity, authority_id, revision, operation, updated_at
        ) VALUES ('{stream}', {identity}, '', {clock}, '{operation}', {now})
        ON CONFLICT(stream, identity) DO UPDATE SET
          authority_id = '', revision = excluded.revision,
          operation = excluded.operation, updated_at = excluded.updated_at;
    """


def _local_event(expr: str) -> str:
    """``expr`` 指向的事件是本机产生的(authority_id = ''),才需要进 events 流。"""

    return f"EXISTS (SELECT 1 FROM events e WHERE e.id = {expr} AND e.authority_id = '')"


def _local_event_state(expr: str, clock: str, now: str) -> str:
    """把 ``expr`` 指向的事件标为 upsert——仅当它是本机事件;供一条触发器同时归到两个事件用。"""

    return f"""
        INSERT INTO archive_sync_entity_states(
          stream, identity, authority_id, revision, operation, updated_at
        ) SELECT 'events', CAST({expr} AS TEXT), '', {clock}, 'upsert', {now}
        WHERE {_local_event(expr)}
        ON CONFLICT(stream, identity) DO UPDATE SET
          authority_id = '', revision = excluded.revision,
          operation = excluded.operation, updated_at = excluded.updated_at;
    """


def _trigger_sql(
    *, include_podcast_integrity: bool = True
) -> Iterable[tuple[str, str]]:
    now = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
    clock = "(SELECT revision FROM archive_sync_clock WHERE id = 1)"
    tick = "UPDATE archive_sync_clock SET revision = revision + 1 WHERE id = 1;"

    yield (
        "archive_sync_source_insert",
        f"""
        CREATE TRIGGER archive_sync_source_insert AFTER INSERT ON source_configs
        WHEN {_public_source("NEW")}
        BEGIN
          {tick}
          {_state_values("sources", "NEW.source_id", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_source_update",
        f"""
        CREATE TRIGGER archive_sync_source_update AFTER UPDATE ON source_configs
        WHEN {_public_source("NEW")}
        BEGIN
          {tick}
          {_state_values("sources", "NEW.source_id", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_source_delete",
        f"""
        CREATE TRIGGER archive_sync_source_delete AFTER DELETE ON source_configs
        WHEN {_public_source("OLD")}
        BEGIN
          {tick}
          {_state_values("sources", "OLD.source_id", "tombstone", clock, now)}
          DELETE FROM source_states
          WHERE source_id = OLD.source_id AND authority_id = '';
        END
    """,
    )
    yield (
        "archive_sync_source_nonpublic_insert",
        """
        CREATE TRIGGER archive_sync_source_nonpublic_insert AFTER INSERT ON source_configs
        WHEN NEW.owner_username <> ''
        BEGIN
          DELETE FROM archive_sync_entity_states
          WHERE authority_id = '' AND (
            (stream IN ('sources','source_states') AND identity = NEW.source_id) OR
            (stream IN ('articles','analyses') AND identity IN (
              SELECT id FROM articles WHERE source_id = NEW.source_id
            ))
          );
        END
    """,
    )
    yield (
        "archive_sync_source_scope_exit",
        f"""
        CREATE TRIGGER archive_sync_source_scope_exit AFTER UPDATE ON source_configs
        WHEN {_public_source("OLD")} AND NEW.owner_username <> ''
        BEGIN
          {tick}
          {_state_values("sources", "NEW.source_id", "tombstone", clock, now)}
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
    """,
    )
    yield (
        "archive_sync_source_scope_enter",
        f"""
        CREATE TRIGGER archive_sync_source_scope_enter AFTER UPDATE ON source_configs
        WHEN NOT ({_public_source("OLD")}) AND {_public_source("NEW")}
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
    """,
    )

    article_public_new = _public_article("NEW")
    article_public_old = _public_article("OLD")
    yield (
        "archive_sync_article_insert",
        f"""
        CREATE TRIGGER archive_sync_article_insert AFTER INSERT ON articles
        WHEN {article_public_new}
        BEGIN
          {tick}
          {_state_values("articles", "NEW.id", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_article_update",
        f"""
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
          {_state_values("articles", "NEW.id", "upsert", clock, now)}
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
    """,
    )
    yield (
        "archive_sync_article_scope_exit",
        f"""
        CREATE TRIGGER archive_sync_article_scope_exit AFTER UPDATE ON articles
        WHEN {article_public_old} AND NOT ({article_public_new})
          AND NEW.analysis_authority_id = ''
        BEGIN
          {tick}
          {_state_values("articles", "OLD.id", "tombstone", clock, now)}
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
    """,
    )
    yield (
        "archive_sync_article_scope_enter",
        f"""
        CREATE TRIGGER archive_sync_article_scope_enter AFTER UPDATE ON articles
        WHEN NOT ({article_public_old}) AND {article_public_new}
        BEGIN
          {tick}
          {_state_values("articles", "NEW.id", "upsert", clock, now)}
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
    """,
    )
    yield (
        "archive_sync_article_remote_handoff",
        """
        CREATE TRIGGER archive_sync_article_remote_handoff AFTER UPDATE ON articles
        WHEN NEW.analysis_authority_id <> ''
        BEGIN
          DELETE FROM archive_sync_entity_states
          WHERE authority_id = '' AND identity = NEW.id
            AND stream IN ('articles','analyses');
        END
    """,
    )
    yield (
        "archive_sync_article_delete",
        f"""
        CREATE TRIGGER archive_sync_article_delete BEFORE DELETE ON articles
        WHEN {article_public_old}
        BEGIN
          {tick}
          {_state_values("articles", "OLD.id", "tombstone", clock, now)}
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
    """,
    )

    analysis_scope = (
        "NEW.authority_id = '' AND EXISTS (SELECT 1 FROM articles a "
        f"WHERE a.id = NEW.article_id AND {_public_article('a')})"
    )
    for suffix, action in (("insert", "INSERT"), ("update", "UPDATE")):
        yield (
            f"archive_sync_analysis_{suffix}",
            f"""
            CREATE TRIGGER archive_sync_analysis_{suffix}
            AFTER {action} ON article_analyses
            WHEN {analysis_scope}
            BEGIN
              {tick}
              {_state_values("analyses", "NEW.article_id", "upsert", clock, now)}
            END
        """,
        )
    yield (
        "archive_sync_analysis_delete",
        f"""
        CREATE TRIGGER archive_sync_analysis_delete AFTER DELETE ON article_analyses
        WHEN OLD.authority_id = '' AND EXISTS (
          SELECT 1 FROM articles a WHERE a.id = OLD.article_id AND {_public_article("a")}
        )
        BEGIN
          {tick}
          DELETE FROM article_tag_assignments
          WHERE article_id = OLD.article_id AND assignment_source = 'llm';
          {_state_values("analyses", "OLD.article_id", "tombstone", clock, now)}
        END
    """,
    )

    assignment_specs = (
        ("insert", "INSERT", "NEW"),
        ("update", "UPDATE", "NEW"),
        ("delete", "DELETE", "OLD"),
    )
    for suffix, action, alias in assignment_specs:
        yield (
            f"archive_sync_assignment_{suffix}",
            f"""
            CREATE TRIGGER archive_sync_assignment_{suffix}
            AFTER {action} ON article_tag_assignments
            WHEN {alias}.assignment_source = 'llm' AND EXISTS (
              SELECT 1 FROM article_analyses aa JOIN articles a ON a.id = aa.article_id
              WHERE aa.article_id = {alias}.article_id
                AND aa.authority_id = '' AND {_public_article("a")}
            )
            BEGIN
              {tick}
              {_state_values("analyses", f"{alias}.article_id", "upsert", clock, now)}
            END
        """,
        )

    yield (
        "archive_sync_media_insert",
        f"""
        CREATE TRIGGER archive_sync_media_insert AFTER INSERT ON media_assets
        WHEN NEW.sync_authority_id = ''
        BEGIN
          {tick}
          {_state_values("media", "NEW.url_hash", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_media_update",
        f"""
        CREATE TRIGGER archive_sync_media_update AFTER UPDATE ON media_assets
        WHEN NEW.sync_authority_id = ''
        BEGIN
          {tick}
          {_state_values("media", "NEW.url_hash", "upsert", clock, now)}
        END
    """,
    )

    state_public_new = (
        "NEW.authority_id = '' AND NEW.source_id NOT LIKE 'user\\_rss\\_%' ESCAPE '\\' "
        "AND (NOT EXISTS (SELECT 1 FROM source_configs sc WHERE sc.source_id = NEW.source_id) "
        "OR EXISTS (SELECT 1 FROM source_configs sc WHERE sc.source_id = NEW.source_id "
        "AND sc.owner_username = ''))"
    )
    state_public_old = state_public_new.replace("NEW.", "OLD.")
    for suffix, action in (("insert", "INSERT"), ("update", "UPDATE")):
        yield (
            f"archive_sync_source_state_{suffix}",
            f"""
            CREATE TRIGGER archive_sync_source_state_{suffix}
            AFTER {action} ON source_states
            WHEN {state_public_new}
            BEGIN
              {tick}
              {_state_values("source_states", "NEW.source_id", "upsert", clock, now)}
            END
        """,
        )
    yield (
        "archive_sync_source_state_delete",
        f"""
        CREATE TRIGGER archive_sync_source_state_delete AFTER DELETE ON source_states
        WHEN {state_public_old}
        BEGIN
          {tick}
          {_state_values("source_states", "OLD.source_id", "tombstone", clock, now)}
        END
    """,
    )

    podcast_public_new = _public_podcast_text_publication("NEW")
    podcast_public_old = _public_podcast_text_publication("OLD")
    yield (
        "archive_sync_podcast_text_insert",
        f"""
        CREATE TRIGGER archive_sync_podcast_text_insert
        AFTER INSERT ON podcast_text_publications
        WHEN {podcast_public_new}
        BEGIN
          {tick}
          {_state_values("podcast_texts", "NEW.identity", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_text_update",
        f"""
        CREATE TRIGGER archive_sync_podcast_text_update
        AFTER UPDATE ON podcast_text_publications
        WHEN {podcast_public_old} AND {podcast_public_new}
        BEGIN
          {tick}
          {_state_values("podcast_texts", "NEW.identity", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_text_scope_exit",
        f"""
        CREATE TRIGGER archive_sync_podcast_text_scope_exit
        AFTER UPDATE ON podcast_text_publications
        WHEN NOT ({podcast_public_new}) AND EXISTS (
          SELECT 1 FROM archive_sync_entity_states aes
          WHERE aes.stream = 'podcast_texts' AND aes.identity = OLD.identity
            AND aes.authority_id = '' AND aes.operation = 'upsert'
        )
        BEGIN
          {tick}
          {_state_values("podcast_texts", "OLD.identity", "tombstone", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_text_scope_enter",
        f"""
        CREATE TRIGGER archive_sync_podcast_text_scope_enter
        AFTER UPDATE ON podcast_text_publications
        WHEN NOT ({podcast_public_old}) AND {podcast_public_new}
        BEGIN
          {tick}
          {_state_values("podcast_texts", "NEW.identity", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_text_delete",
        f"""
        CREATE TRIGGER archive_sync_podcast_text_delete
        BEFORE DELETE ON podcast_text_publications
        WHEN EXISTS (
          SELECT 1 FROM archive_sync_entity_states aes
          WHERE aes.stream = 'podcast_texts' AND aes.identity = OLD.identity
            AND aes.authority_id = '' AND aes.operation = 'upsert'
        )
        BEGIN
          {tick}
          {_state_values("podcast_texts", "OLD.identity", "tombstone", clock, now)}
        END
    """,
    )
    podcast_audio_public_new = _public_podcast_audio("NEW")
    podcast_audio_public_old = _public_podcast_audio("OLD")
    yield (
        "archive_sync_podcast_audio_insert",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_insert
        AFTER INSERT ON podcast_artifacts
        WHEN {podcast_audio_public_new}
        BEGIN
          {tick}
          {_state_values("podcast_audio", "NEW.id", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_update",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_update
        AFTER UPDATE ON podcast_artifacts
        WHEN {podcast_audio_public_old} AND {podcast_audio_public_new}
        BEGIN
          {tick}
          {_state_values("podcast_audio", "NEW.id", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_scope_exit",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_scope_exit
        AFTER UPDATE ON podcast_artifacts
        WHEN {podcast_audio_public_old} AND NOT ({podcast_audio_public_new})
        BEGIN
          {tick}
          {_state_values("podcast_audio", "OLD.id", "tombstone", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_scope_enter",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_scope_enter
        AFTER UPDATE ON podcast_artifacts
        WHEN NOT ({podcast_audio_public_old}) AND {podcast_audio_public_new}
        BEGIN
          {tick}
          {_state_values("podcast_audio", "NEW.id", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_delete",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_delete
        BEFORE DELETE ON podcast_artifacts
        WHEN OLD.authority_id = '' AND OLD.kind = 'digest_audio_zh'
          AND EXISTS (
            SELECT 1 FROM archive_sync_entity_states aes
            WHERE aes.stream = 'podcast_audio' AND aes.identity = OLD.id
              AND aes.authority_id = '' AND aes.operation = 'upsert'
          )
        BEGIN
          {tick}
          {_state_values("podcast_audio", "OLD.id", "tombstone", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_source_scope_exit",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_source_scope_exit
        AFTER UPDATE ON source_configs
        WHEN {_public_source("OLD")} AND NOT ({_public_source("NEW")})
        BEGIN
          {tick}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'podcast_audio', pa.id, '', {clock}, 'tombstone', {now}
          FROM podcast_artifacts pa JOIN articles a ON a.id = pa.episode_id
          WHERE a.source_id = OLD.source_id AND pa.authority_id = ''
            AND pa.kind = 'digest_audio_zh' AND pa.status = 'published'
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_source_scope_enter",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_source_scope_enter
        AFTER UPDATE ON source_configs
        WHEN NOT ({_public_source("OLD")}) AND {_public_source("NEW")}
        BEGIN
          {tick}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'podcast_audio', pa.id, '', {clock}, 'upsert', {now}
          FROM podcast_artifacts pa JOIN articles a ON a.id = pa.episode_id
          WHERE a.source_id = NEW.source_id AND pa.authority_id = ''
            AND pa.kind = 'digest_audio_zh' AND pa.status = 'published'
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_article_scope_exit",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_article_scope_exit
        AFTER UPDATE ON articles
        WHEN {article_public_old} AND NOT ({article_public_new})
        BEGIN
          {tick}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'podcast_audio', pa.id, '', {clock}, 'tombstone', {now}
          FROM podcast_artifacts pa
          WHERE pa.episode_id = OLD.id AND pa.authority_id = ''
            AND pa.kind = 'digest_audio_zh' AND pa.status = 'published'
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_article_scope_enter",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_article_scope_enter
        AFTER UPDATE ON articles
        WHEN NOT ({article_public_old}) AND {article_public_new}
        BEGIN
          {tick}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'podcast_audio', pa.id, '', {clock}, 'upsert', {now}
          FROM podcast_artifacts pa
          WHERE pa.episode_id = NEW.id AND pa.authority_id = ''
            AND pa.kind = 'digest_audio_zh' AND pa.status = 'published'
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """,
    )
    if include_podcast_integrity:
        source_public_old = _public_source("OLD")
        source_public_new = _public_source("NEW")
        yield (
            "archive_sync_podcast_text_source_nonpublic_insert",
            f"""
            CREATE TRIGGER archive_sync_podcast_text_source_nonpublic_insert
            AFTER INSERT ON source_configs
            WHEN NOT ({source_public_new})
              AND EXISTS (
                SELECT 1 FROM podcast_text_publications p
                JOIN articles a ON a.id = p.episode_id
                JOIN archive_sync_entity_states aes
                  ON aes.stream = 'podcast_texts' AND aes.identity = p.identity
                WHERE a.source_id = NEW.source_id AND p.authority_id = ''
                  AND p.status = 'published' AND aes.authority_id = ''
                  AND aes.operation = 'upsert'
              )
            BEGIN
              {tick}
              INSERT INTO archive_sync_entity_states(
                stream, identity, authority_id, revision, operation, updated_at
              ) SELECT 'podcast_texts', p.identity, '', {clock}, 'tombstone', {now}
              FROM podcast_text_publications p
              JOIN articles a ON a.id = p.episode_id
              JOIN archive_sync_entity_states aes
                ON aes.stream = 'podcast_texts' AND aes.identity = p.identity
              WHERE a.source_id = NEW.source_id AND p.authority_id = ''
                AND p.status = 'published' AND aes.authority_id = ''
                AND aes.operation = 'upsert'
              ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
                revision = excluded.revision, operation = excluded.operation,
                updated_at = excluded.updated_at;
            END
        """,
        )
        yield (
            "archive_sync_podcast_text_source_scope_exit",
            f"""
            CREATE TRIGGER archive_sync_podcast_text_source_scope_exit
            AFTER UPDATE ON source_configs
            WHEN {source_public_old} AND NOT ({source_public_new})
              AND EXISTS (
                SELECT 1 FROM podcast_text_publications p
                JOIN articles a ON a.id = p.episode_id
                JOIN archive_sync_entity_states aes
                  ON aes.stream = 'podcast_texts' AND aes.identity = p.identity
                WHERE a.source_id = OLD.source_id AND p.authority_id = ''
                  AND p.status = 'published' AND aes.authority_id = ''
                  AND aes.operation = 'upsert'
              )
            BEGIN
              {tick}
              INSERT INTO archive_sync_entity_states(
                stream, identity, authority_id, revision, operation, updated_at
              ) SELECT 'podcast_texts', p.identity, '', {clock}, 'tombstone', {now}
              FROM podcast_text_publications p
              JOIN articles a ON a.id = p.episode_id
              JOIN archive_sync_entity_states aes
                ON aes.stream = 'podcast_texts' AND aes.identity = p.identity
              WHERE a.source_id = OLD.source_id AND p.authority_id = ''
                AND p.status = 'published' AND aes.authority_id = ''
                AND aes.operation = 'upsert'
              ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
                revision = excluded.revision, operation = excluded.operation,
                updated_at = excluded.updated_at;
            END
        """,
        )
        yield (
            "archive_sync_podcast_text_source_scope_enter",
            f"""
            CREATE TRIGGER archive_sync_podcast_text_source_scope_enter
            AFTER UPDATE ON source_configs
            WHEN NOT ({source_public_old}) AND {source_public_new}
              AND EXISTS (
                SELECT 1 FROM podcast_text_publications p
                JOIN articles a ON a.id = p.episode_id
                WHERE a.source_id = NEW.source_id AND p.authority_id = ''
                  AND p.status = 'published'
              )
            BEGIN
              {tick}
              INSERT INTO archive_sync_entity_states(
                stream, identity, authority_id, revision, operation, updated_at
              ) SELECT 'podcast_texts', p.identity, '', {clock}, 'upsert', {now}
              FROM podcast_text_publications p
              JOIN articles a ON a.id = p.episode_id
              WHERE a.source_id = NEW.source_id AND p.authority_id = ''
                AND p.status = 'published'
              ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
                revision = excluded.revision, operation = excluded.operation,
                updated_at = excluded.updated_at;
            END
        """,
        )
        yield (
            "archive_sync_podcast_text_article_scope_exit",
            f"""
            CREATE TRIGGER archive_sync_podcast_text_article_scope_exit
            AFTER UPDATE ON articles
            WHEN {article_public_old} AND NOT ({article_public_new})
              AND EXISTS (
                SELECT 1 FROM podcast_text_publications p
                JOIN archive_sync_entity_states aes
                  ON aes.stream = 'podcast_texts' AND aes.identity = p.identity
                WHERE p.episode_id = OLD.id AND p.authority_id = ''
                  AND p.status = 'published' AND aes.authority_id = ''
                  AND aes.operation = 'upsert'
              )
            BEGIN
              {tick}
              INSERT INTO archive_sync_entity_states(
                stream, identity, authority_id, revision, operation, updated_at
              ) SELECT 'podcast_texts', p.identity, '', {clock}, 'tombstone', {now}
              FROM podcast_text_publications p
              JOIN archive_sync_entity_states aes
                ON aes.stream = 'podcast_texts' AND aes.identity = p.identity
              WHERE p.episode_id = OLD.id AND p.authority_id = ''
                AND p.status = 'published' AND aes.authority_id = ''
                AND aes.operation = 'upsert'
              ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
                revision = excluded.revision, operation = excluded.operation,
                updated_at = excluded.updated_at;
            END
        """,
        )
        yield (
            "archive_sync_podcast_text_article_scope_enter",
            f"""
            CREATE TRIGGER archive_sync_podcast_text_article_scope_enter
            AFTER UPDATE ON articles
            WHEN NOT ({article_public_old}) AND {article_public_new}
              AND EXISTS (
                SELECT 1 FROM podcast_text_publications p
                WHERE p.episode_id = NEW.id AND p.authority_id = ''
                  AND p.status = 'published'
              )
            BEGIN
              {tick}
              INSERT INTO archive_sync_entity_states(
                stream, identity, authority_id, revision, operation, updated_at
              ) SELECT 'podcast_texts', p.identity, '', {clock}, 'upsert', {now}
              FROM podcast_text_publications p
              WHERE p.episode_id = NEW.id AND p.authority_id = ''
                AND p.status = 'published'
              ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
                revision = excluded.revision, operation = excluded.operation,
                updated_at = excluded.updated_at;
            END
        """,
        )
    authority_immutable = (
        "\n          OR NEW.authority_id IS NOT OLD.authority_id"
        if include_podcast_integrity
        else ""
    )
    yield (
        "podcast_text_publication_identity_immutable",
        f"""
        CREATE TRIGGER podcast_text_publication_identity_immutable
        BEFORE UPDATE ON podcast_text_publications
        WHEN NEW.identity IS NOT OLD.identity
          OR NEW.episode_id IS NOT OLD.episode_id
          OR NEW.kind IS NOT OLD.kind{authority_immutable}
        BEGIN
          SELECT RAISE(ABORT, 'podcast text publication identity is immutable');
        END
    """,
    )
    yield (
        "podcast_text_artifact_immutable_update",
        """
        CREATE TRIGGER podcast_text_artifact_immutable_update
        BEFORE UPDATE ON podcast_text_artifacts
        BEGIN
          SELECT RAISE(ABORT, 'podcast text artifacts are immutable');
        END
    """,
    )
    yield (
        "podcast_text_artifact_immutable_delete",
        """
        CREATE TRIGGER podcast_text_artifact_immutable_delete
        BEFORE DELETE ON podcast_text_artifacts
        WHEN EXISTS (SELECT 1 FROM articles a WHERE a.id = OLD.episode_id)
        BEGIN
          SELECT RAISE(ABORT, 'podcast text artifacts are immutable');
        END
    """,
    )

    # ── 事件层(issue #121):events / event_members / event_redirects 的变更一律归到所属事件
    # (identity = events.id 文本化),只对本机产生的事件(authority_id = '')触发;事件物理删除落
    # tombstone。events 删除用 AFTER:SQLite 先跑 FK 级联(成员 / 重定向的删除触发器会把父事件
    # 标 upsert),再跑 AFTER 触发器,tombstone 最后落笔、不会被级联写回覆盖。
    yield (
        "archive_sync_event_insert",
        f"""
        CREATE TRIGGER archive_sync_event_insert AFTER INSERT ON events
        WHEN NEW.authority_id = ''
        BEGIN
          {tick}
          {_state_values("events", "CAST(NEW.id AS TEXT)", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_event_update",
        f"""
        CREATE TRIGGER archive_sync_event_update AFTER UPDATE ON events
        WHEN OLD.authority_id = '' AND NEW.authority_id = ''
        BEGIN
          {tick}
          {_state_values("events", "CAST(NEW.id AS TEXT)", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_event_delete",
        f"""
        CREATE TRIGGER archive_sync_event_delete AFTER DELETE ON events
        WHEN OLD.authority_id = ''
        BEGIN
          {tick}
          {_state_values("events", "CAST(OLD.id AS TEXT)", "tombstone", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_event_member_insert",
        f"""
        CREATE TRIGGER archive_sync_event_member_insert AFTER INSERT ON event_members
        WHEN {_local_event("NEW.event_id")}
        BEGIN
          {tick}
          {_state_values("events", "CAST(NEW.event_id AS TEXT)", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_event_member_update",
        f"""
        CREATE TRIGGER archive_sync_event_member_update AFTER UPDATE ON event_members
        WHEN {_local_event("NEW.event_id")} OR {_local_event("OLD.event_id")}
        BEGIN
          {tick}
          {_local_event_state("NEW.event_id", clock, now)}
          {_local_event_state("OLD.event_id", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_event_member_delete",
        f"""
        CREATE TRIGGER archive_sync_event_member_delete AFTER DELETE ON event_members
        WHEN {_local_event("OLD.event_id")}
        BEGIN
          {tick}
          {_state_values("events", "CAST(OLD.event_id AS TEXT)", "upsert", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_event_redirect_insert",
        f"""
        CREATE TRIGGER archive_sync_event_redirect_insert AFTER INSERT ON event_redirects
        WHEN {_local_event("NEW.old_event_id")} OR {_local_event("NEW.new_event_id")}
        BEGIN
          {tick}
          {_local_event_state("NEW.old_event_id", clock, now)}
          {_local_event_state("NEW.new_event_id", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_event_redirect_update",
        f"""
        CREATE TRIGGER archive_sync_event_redirect_update AFTER UPDATE ON event_redirects
        WHEN {_local_event("NEW.old_event_id")} OR {_local_event("NEW.new_event_id")}
          OR {_local_event("OLD.old_event_id")} OR {_local_event("OLD.new_event_id")}
        BEGIN
          {tick}
          {_local_event_state("NEW.old_event_id", clock, now)}
          {_local_event_state("NEW.new_event_id", clock, now)}
          {_local_event_state("OLD.old_event_id", clock, now)}
          {_local_event_state("OLD.new_event_id", clock, now)}
        END
    """,
    )
    yield (
        "archive_sync_event_redirect_delete",
        f"""
        CREATE TRIGGER archive_sync_event_redirect_delete AFTER DELETE ON event_redirects
        WHEN {_local_event("OLD.old_event_id")} OR {_local_event("OLD.new_event_id")}
        BEGIN
          {tick}
          {_local_event_state("OLD.old_event_id", clock, now)}
          {_local_event_state("OLD.new_event_id", clock, now)}
        END
    """,
    )
    # 成员文章的「可导出性」变了(源转私有 / 凭据 / 远端,或文章换源 / 移交 authority)→ 其事件重新
    # 导出:导出侧按剩余可导出成员重建元数据,一个都不剩就以 tombstone 让接收端撤回(检视 R1 #2)。
    # 凭据判定在 Python 侧(显式分类 + URL 推断),SQL 里只能按 url / params_json 变化粗判,宁多勿漏。
    source_change = (
        "NEW.owner_username IS NOT OLD.owner_username "
        "OR NEW.collection_authority_id IS NOT OLD.collection_authority_id "
        "OR NEW.params_json IS NOT OLD.params_json OR NEW.url IS NOT OLD.url"
    )
    source_has_event_members = (
        "EXISTS (SELECT 1 FROM event_members em "
        "JOIN articles a ON a.id = em.article_id "
        "JOIN events e ON e.id = em.event_id "
        "WHERE a.source_id = NEW.source_id AND e.authority_id = '')"
    )
    source_event_states = f"""
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT DISTINCT 'events', CAST(e.id AS TEXT), '', {clock}, 'upsert', {now}
          FROM events e
          JOIN event_members em ON em.event_id = e.id
          JOIN articles a ON a.id = em.article_id
          WHERE a.source_id = NEW.source_id AND e.authority_id = ''
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
    """
    yield (
        "archive_sync_event_source_insert",
        f"""
        CREATE TRIGGER archive_sync_event_source_insert AFTER INSERT ON source_configs
        WHEN {source_has_event_members}
        BEGIN
          {tick}
          {source_event_states}
        END
    """,
    )
    yield (
        "archive_sync_event_source_change",
        f"""
        CREATE TRIGGER archive_sync_event_source_change AFTER UPDATE ON source_configs
        WHEN ({source_change}) AND {source_has_event_members}
        BEGIN
          {tick}
          {source_event_states}
        END
    """,
    )
    yield (
        "archive_sync_event_article_scope_change",
        f"""
        CREATE TRIGGER archive_sync_event_article_scope_change AFTER UPDATE ON articles
        WHEN (NEW.source_id IS NOT OLD.source_id
              OR NEW.analysis_authority_id IS NOT OLD.analysis_authority_id)
          AND EXISTS (SELECT 1 FROM event_members em JOIN events e ON e.id = em.event_id
                      WHERE em.article_id = NEW.id AND e.authority_id = '')
        BEGIN
          {tick}
          INSERT INTO archive_sync_entity_states(
            stream, identity, authority_id, revision, operation, updated_at
          ) SELECT 'events', CAST(em.event_id AS TEXT), '', {clock}, 'upsert', {now}
          FROM event_members em JOIN events e ON e.id = em.event_id
          WHERE em.article_id = NEW.id AND e.authority_id = ''
          ON CONFLICT(stream, identity) DO UPDATE SET authority_id = '',
            revision = excluded.revision, operation = excluded.operation,
            updated_at = excluded.updated_at;
        END
    """,
    )
    # 接收端围栏:authority 事件只接受同一 authority 的文章做成员——本机分配(含 user_rss_ 私有文章)
    # 永远进不了同步来的事件。成员的 assigned_by 原样照抄生产端,所以不能拿它当「写者」标记。
    for suffix, action in (
        ("insert", "INSERT"),
        ("update", "UPDATE OF event_id, article_id"),
    ):
        yield (
            f"event_member_authority_guard_{suffix}",
            f"""
            CREATE TRIGGER event_member_authority_guard_{suffix}
            BEFORE {action} ON event_members
            WHEN EXISTS (
              SELECT 1 FROM events e
              WHERE e.id = NEW.event_id AND e.authority_id <> ''
                AND NOT EXISTS (
                  SELECT 1 FROM articles a
                  WHERE a.id = NEW.article_id
                    AND a.analysis_authority_id = e.authority_id
                )
            )
            BEGIN
              SELECT RAISE(ABORT, 'authority-owned event accepts only articles of the same authority');
            END
        """,
        )


    # ── 导出修订(issue #263)──────────────────────────────────────────────────
    # 状态表一列两用拆开:revision 对同步写入的行是「上游修订」(接收去重),local_revision 才是
    # 「下游需要看到的本机修订」。本机行由下面两个镜像触发器保持 local_revision = revision,现有
    # 写状态的业务触发器因此一字不改;同步写入的行由导入端在应用时写本机时钟。
    for suffix, action in (
        ("insert", "INSERT"),
        ("update", "UPDATE OF revision, authority_id"),
    ):
        yield (
            f"archive_sync_state_local_revision_{suffix}",
            f"""
            CREATE TRIGGER archive_sync_state_local_revision_{suffix}
            AFTER {action} ON archive_sync_entity_states
            WHEN NEW.authority_id = '' AND NEW.local_revision IS NOT NEW.revision
            BEGIN
              UPDATE archive_sync_entity_states SET local_revision = NEW.revision
              WHERE stream = NEW.stream AND identity = NEW.identity;
            END
        """,
        )
    # 同步写入的图片 / 播客音频是「先落元数据、后装文件」:导出只认装好的行,所以文件就绪(以及
    # 撤回、恢复)本身就是一次对下游的发布,必须推进导出修订——否则下游若恰好在两个阶段之间拉取,
    # 文件装好后没有新修订通知它(方案检视 R1 #5)。本机行的状态变化已由上面的业务触发器覆盖。
    yield (
        "archive_sync_media_remote_publish",
        f"""
        CREATE TRIGGER archive_sync_media_remote_publish
        AFTER UPDATE OF status ON media_assets
        WHEN NEW.sync_authority_id <> '' AND NEW.status IS NOT OLD.status
        BEGIN
          {tick}
          UPDATE archive_sync_entity_states SET local_revision = {clock}
          WHERE stream = 'media' AND identity = NEW.url_hash AND authority_id <> '';
        END
    """,
    )
    yield (
        "archive_sync_podcast_audio_remote_publish",
        f"""
        CREATE TRIGGER archive_sync_podcast_audio_remote_publish
        AFTER UPDATE OF status ON podcast_artifacts
        WHEN NEW.authority_id <> '' AND NEW.kind = 'digest_audio_zh'
          AND NEW.status IS NOT OLD.status
        BEGIN
          {tick}
          UPDATE archive_sync_entity_states SET local_revision = {clock}
          WHERE stream = 'podcast_audio' AND identity = NEW.id AND authority_id <> '';
        END
    """,
    )


_EVENT_TABLES = frozenset({"events", "event_members", "event_redirects"})

_EVENT_TRIGGER_NAMES = frozenset(
    {
        "archive_sync_event_insert",
        "archive_sync_event_update",
        "archive_sync_event_delete",
        "archive_sync_event_member_insert",
        "archive_sync_event_member_update",
        "archive_sync_event_member_delete",
        "archive_sync_event_redirect_insert",
        "archive_sync_event_redirect_update",
        "archive_sync_event_redirect_delete",
        "archive_sync_event_source_insert",
        "archive_sync_event_source_change",
        "archive_sync_event_article_scope_change",
        "event_member_authority_guard_insert",
        "event_member_authority_guard_update",
    }
)


# 依赖 archive_sync_entity_states.local_revision 的触发器(issue #263)。历史迁移会调用当前的
# 安装器,列还不存在时不能装:它们一触发就会报 no such column。
_LOCAL_REVISION_TRIGGER_NAMES = frozenset(
    {
        "archive_sync_state_local_revision_insert",
        "archive_sync_state_local_revision_update",
        "archive_sync_media_remote_publish",
        "archive_sync_podcast_audio_remote_publish",
    }
)


def _local_revision_ready(connection: Connection) -> bool:
    rows = connection.exec_driver_sql(
        "PRAGMA table_info('archive_sync_entity_states')"
    ).fetchall()
    return any(str(row[1]) == "local_revision" for row in rows)


def _event_schema_ready(connection: Connection, tables: set[str]) -> bool:
    """事件三表齐全,且 archive_sync_entity_states 的 stream CHECK 已含 'events'。

    迁移链里 ed530f8224f9 建事件表、0919431adfa8 才放宽 CHECK;两者之间若装了事件触发器,
    一次成员写入就会撞 CHECK。按 DDL 文本判断比沿迁移链传旗子更不易漏。
    """

    if not _EVENT_TABLES <= tables:
        return False
    row = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'archive_sync_entity_states'"
    ).fetchone()
    return bool(row and row[0] and "'events'" in str(row[0]))


_PODCAST_TRIGGER_NAMES = frozenset(
    {
        "archive_sync_podcast_text_insert",
        "archive_sync_podcast_text_update",
        "archive_sync_podcast_text_scope_exit",
        "archive_sync_podcast_text_scope_enter",
        "archive_sync_podcast_text_delete",
        "archive_sync_podcast_text_source_nonpublic_insert",
        "archive_sync_podcast_text_source_scope_exit",
        "archive_sync_podcast_text_source_scope_enter",
        "archive_sync_podcast_text_article_scope_exit",
        "archive_sync_podcast_text_article_scope_enter",
        "podcast_text_publication_identity_immutable",
        "podcast_text_artifact_immutable_update",
        "podcast_text_artifact_immutable_delete",
        "archive_sync_podcast_audio_insert",
        "archive_sync_podcast_audio_update",
        "archive_sync_podcast_audio_scope_exit",
        "archive_sync_podcast_audio_scope_enter",
        "archive_sync_podcast_audio_delete",
        "archive_sync_podcast_audio_source_scope_exit",
        "archive_sync_podcast_audio_source_scope_enter",
        "archive_sync_podcast_audio_article_scope_exit",
        "archive_sync_podcast_audio_article_scope_enter",
        "archive_sync_podcast_audio_remote_publish",
    }
)

_PODCAST_AUDIO_TRIGGER_NAMES = frozenset(
    {
        "archive_sync_podcast_audio_insert",
        "archive_sync_podcast_audio_update",
        "archive_sync_podcast_audio_scope_exit",
        "archive_sync_podcast_audio_scope_enter",
        "archive_sync_podcast_audio_delete",
        "archive_sync_podcast_audio_source_scope_exit",
        "archive_sync_podcast_audio_source_scope_enter",
        "archive_sync_podcast_audio_article_scope_exit",
        "archive_sync_podcast_audio_article_scope_enter",
        "archive_sync_podcast_audio_remote_publish",
    }
)


# 已退役、不再由生成器产出的触发器:旧库里还留着,安装与清理时都要显式 DROP。
# archive_sync_source_remote_handoff(issue #263):源一带归属印记就删它和它名下文章的本机状态——
# 源印记降级为簿记后,这会把本机在该源下自己抓到的文章的导出状态删掉。
_RETIRED_TRIGGER_NAMES = ("archive_sync_source_remote_handoff",)


def _drop_retired_triggers(connection: Connection) -> None:
    for name in _RETIRED_TRIGGER_NAMES:
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')


def drop_archive_sync_revision_triggers(connection: Connection) -> None:
    """Drop the complete current trigger set before SQLite table recreation."""

    if connection.dialect.name != "sqlite":
        return
    _drop_retired_triggers(connection)
    for name, _statement in _trigger_sql():
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')


def install_archive_sync_revision_triggers(
    connection: Connection,
    *,
    include_podcast_integrity: bool = True,
    include_podcast_audio: bool = False,
) -> None:
    """Install or refresh all revision triggers on a SQLite connection."""

    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql(_CLOCK_INIT)
    _drop_retired_triggers(connection)
    tables = set(inspect(connection).get_table_names())
    podcast_text_schema_ready = {
        "podcast_text_artifacts",
        "podcast_text_publications",
        "podcast_artifacts",
    } <= tables
    event_schema_ready = _event_schema_ready(connection, tables)
    local_revision_ready = _local_revision_ready(connection)
    for name, statement in _trigger_sql(
        include_podcast_integrity=include_podcast_integrity
    ):
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
        if name in _PODCAST_TRIGGER_NAMES and not podcast_text_schema_ready:
            continue
        if name in _EVENT_TRIGGER_NAMES and not event_schema_ready:
            continue
        if name in _LOCAL_REVISION_TRIGGER_NAMES and not local_revision_ready:
            continue
        # Historical migrations rebuild podcast_artifacts. Only the final audio
        # stream migration, or a fresh create_all schema, may install triggers
        # that reference that table.
        if name in _PODCAST_AUDIO_TRIGGER_NAMES and not include_podcast_audio:
            continue
        connection.exec_driver_sql(statement)
