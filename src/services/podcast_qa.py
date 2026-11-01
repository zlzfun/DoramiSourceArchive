"""Read-only Podcast QA evidence from current published original transcripts.

Chunks and their FTS index are disposable local projections. Publication pointers,
never processing status or an old artifact, decide what can reach the model.
No function in this module enqueues ASR or calls a provider.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from sqlalchemy import column, delete, or_, table, text
from sqlmodel import Session, select

from config import PodcastConfig
from models.db import (
    ArticleRecord, PodcastQaChunkRecord as Chunk, PodcastQaIndexRecord as Manifest,
    PodcastTextArtifactRecord as Artifact, PodcastTextPublicationRecord as Publication,
)
from services import source_visibility, user_sources
from services.podcast_text_reader import _plain_text, PodcastTextReaderError
from storage.fts import build_search_components, normalize_for_search
from storage.podcast_qa_fts import FTS_TABLE

KINDS = ("publisher_transcript", "normalized_transcript")
LABELS = {"publisher_transcript": "来源逐字稿", "normalized_transcript": "ASR逐字稿"}
# Bump when parsing/normalization/chunk boundaries change; manifests then rebuild.
PROJECTION_VERSION = 1
CHUNK_CHARS = 1600
CHUNK_OVERLAP = 200
# Spoken arguments span multiple turns. Keep bounded retrieval, while allowing
# six seeds plus their neighboring explanations in a deep, multi-part question.
# Long-form podcast arguments need surrounding qualifications and counterexamples.
# Keep a fixed total cap; ordinary article context limits remain separate.
CONTEXT_CHARS = 64000
_INDEX_LOCK = threading.RLock()


def _get(record: Any, key: str, default=None):
    return record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)


def _allowed_sources(session, source_ids, username=None):
    allowed = set(user_sources.external_ai_allowed_source_ids(session, list(source_ids)))
    allowed -= source_visibility.reader_unavailable_source_ids(session)
    if username is not None:
        allowed -= set(user_sources.unauthorized_user_source_ids(session, username, list(allowed)))
    return sorted(allowed)


def _published_query(source_ids=None, episode_ids=None):
    query = select(Publication, Artifact.content_hash).join(
        Artifact,
        (Artifact.id == Publication.artifact_id)
        & (Artifact.episode_id == Publication.episode_id)
        & (Artifact.kind == Publication.kind)
        & (Artifact.authority_id == Publication.authority_id),
    ).join(ArticleRecord, ArticleRecord.id == Publication.episode_id).where(
        Publication.status == "published", Publication.published_at.is_not(None),
        Publication.kind.in_(KINDS), ArticleRecord.content_type == "podcast_episode",
    )
    if source_ids is not None:
        query = query.where(ArticleRecord.source_id.in_(source_ids))
    if episode_ids is not None:
        query = query.where(ArticleRecord.id.in_(episode_ids))
    return query


def _split(body):
    start = 0
    while start < len(body):
        end = min(len(body), start + CHUNK_CHARS)
        if end < len(body):
            boundary = body.rfind("\n", start + CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary + 1
        yield start, end, body[start:end]
        if end == len(body):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)


def _ensure_indexes(engine, *, source_ids, episode_ids=None):
    """Only missing projections read full immutable text; existing indexes are cheap.

    BEGIN IMMEDIATE serializes publication checks and cache installation across
    processes. Parsing is bounded by the canonical transcript limits. Manifest
    rows also remember malformed input, avoiding repeated expensive retries.
    """
    if not source_ids:
        return
    with _INDEX_LOCK, Session(engine) as session:
        if engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        source_ids = _allowed_sources(session, source_ids)
        missing = _published_query(source_ids, episode_ids).outerjoin(
            Manifest, Manifest.artifact_id == Publication.artifact_id,
        ).where(or_(Manifest.artifact_id.is_(None), Manifest.content_hash != Artifact.content_hash,
                    Manifest.projection_version != PROJECTION_VERSION))
        current = session.exec(missing).all()
        for publication, content_hash in current:
            manifest = session.get(Manifest, publication.artifact_id)
            if manifest and manifest.content_hash == content_hash and manifest.projection_version == PROJECTION_VERSION:
                continue
            if manifest:
                session.exec(delete(Chunk).where(Chunk.artifact_id == publication.artifact_id))
                session.delete(manifest)
                session.flush()
            artifact = session.get(Artifact, publication.artifact_id)
            body = ""
            if artifact and hashlib.sha256(artifact.inline_text.encode("utf-8")).hexdigest() == content_hash:
                try:
                    body = _plain_text(artifact, PodcastConfig(), for_reading=True)
                except (PodcastTextReaderError, ValueError, TypeError, UnicodeError, RecursionError):
                    pass
            session.add(Manifest(
                artifact_id=publication.artifact_id, episode_id=publication.episode_id,
                content_hash=content_hash, projection_version=PROJECTION_VERSION, valid=bool(body.strip()),
            ))
            session.flush()
            if body.strip():
                for index, (start, end, value) in enumerate(_split(body)):
                    session.add(Chunk(
                        artifact_id=publication.artifact_id, episode_id=publication.episode_id,
                        chunk_index=index, range_start=start, range_end=end, content=value,
                    ))
        session.commit()


def _chunk_query(source_ids, episode_ids=None, date_gte=None, date_lte=None):
    query = select(Chunk, Publication.kind, Manifest.content_hash, ArticleRecord).join(
        Manifest, Manifest.artifact_id == Chunk.artifact_id,
    ).join(Publication, (Publication.artifact_id == Chunk.artifact_id)
           & (Publication.episode_id == Chunk.episode_id)).join(
        Artifact, (Artifact.id == Publication.artifact_id)
        & (Artifact.episode_id == Publication.episode_id)
        & (Artifact.kind == Publication.kind)
        & (Artifact.authority_id == Publication.authority_id)
        & (Artifact.content_hash == Manifest.content_hash),
    ).join(ArticleRecord, ArticleRecord.id == Chunk.episode_id).where(
        Publication.status == "published", Publication.published_at.is_not(None),
        Publication.kind.in_(KINDS), Manifest.valid == True,  # noqa: E712
        Manifest.projection_version == PROJECTION_VERSION,
        ArticleRecord.content_type == "podcast_episode", ArticleRecord.source_id.in_(source_ids),
    )
    if episode_ids is not None:
        query = query.where(ArticleRecord.id.in_(episode_ids))
    if date_gte:
        query = query.where(ArticleRecord.publish_date >= date_gte)
    if date_lte:
        query = query.where(ArticleRecord.publish_date <= date_lte + "~")
    return query


def _search_chunks(session, *, source_ids, keywords, episode_ids=None, date_gte=None, date_lte=None, limit=800):
    base = _chunk_query(source_ids, episode_ids, date_gte, date_lte)
    keys = [k.strip() for k in keywords if isinstance(k, str) and k.strip()][:12]
    if not keys:
        return session.exec(base.order_by(ArticleRecord.fetched_date.desc(), Chunk.chunk_index).limit(limit)).all()
    rows, seen, pools = [], set(), []
    fts = table(FTS_TABLE, column("rowid"), column("rank"))
    for keyword in keys:
        match, short = build_search_components(keyword)
        from storage.fts import _sql_normalize_expr
        expr = _sql_normalize_expr("podcast_qa_chunks.content")

        def with_words(statement, words):
            for index, word in enumerate(words):
                normalized = normalize_for_search(word)
                escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                statement = statement.where(text(
                    f"lower({expr}) LIKE lower(:qa_word_{index}) ESCAPE '\\'"
                ).bindparams(**{f"qa_word_{index}": f"%{escaped}%"}))
            return statement

        if match and session.bind.dialect.name == "sqlite":
            try:
                statement = with_words(base, short).join(fts, fts.c.rowid == Chunk.id).where(
                    text(f"{FTS_TABLE} MATCH :qa_match")
                ).order_by(fts.c.rank, ArticleRecord.fetched_date.desc()).limit(limit)
                found = session.exec(statement, params={"qa_match": match}).all()
            except Exception:
                # FTS is optional. Execute LIKE against passages, not show notes.
                found = None
        else:
            found = None
        if found is None:
            # Keep AND semantics within a phrase, including short terms such as
            # PR. A short token must not independently broaden the query.
            statement = with_words(base, normalize_for_search(keyword).split())
            found = session.exec(statement.order_by(ArticleRecord.fetched_date.desc(), Chunk.chunk_index).limit(limit)).all()
        pools.append(found)
    # Round-robin keyword result lists so a frequent first term cannot crowd out
    # a narrow second concept before episode grouping or excerpt selection.
    from itertools import zip_longest
    for group in zip_longest(*pools):
        for row in group:
            if row is None:
                continue
            if row[0].id not in seen:
                seen.add(row[0].id)
                rows.append(row)
    return rows[:limit]


def _evidence(row):
    chunk, kind, content_hash, _ = row
    return {"kind": kind, "artifact_id": chunk.artifact_id, "content_hash": content_hash,
            "range_start": chunk.range_start, "range_end": chunk.range_end, "text": chunk.content}


def _as_dict(record):
    return dict(record) if isinstance(record, dict) else record.model_dump()


def _sample_chunks(session, sources, episode_id):
    """Evenly sample one complete original without loading every passage."""
    base = _chunk_query(sources, [episode_id])
    for kind in KINDS:
        query = base.where(Publication.kind == kind).order_by(Chunk.chunk_index)
        ids = session.execute(query.with_only_columns(Chunk.id)).scalars().all()
        if ids:
            selected = ids if len(ids) <= 4 else [ids[round(i * (len(ids) - 1) / 3)] for i in range(4)]
            return session.exec(query.where(Chunk.id.in_(selected))).all()
    return []


def _surrounding_evidence(session, item, allowance):
    """Read a bounded window around a hit from its immutable indexed passages.

    An argument often continues immediately after the matching chunk. Preserve
    both sides of the hit, including attribution, without reparsing a full file.
    """
    extra = max(0, allowance - len(item["text"]))
    start = max(0, item["range_start"] - extra // 2)
    end = item["range_end"] + extra - extra // 2
    chunks = session.exec(select(Chunk).where(
        Chunk.artifact_id == item["artifact_id"], Chunk.range_start < end,
        Chunk.range_end > start,
    ).order_by(Chunk.range_start)).all()
    if not chunks:
        return item
    origin, value = chunks[0].range_start, chunks[0].content
    for chunk in chunks[1:]:
        overlap = origin + len(value) - chunk.range_start
        if overlap < 0:  # A corrupt projection must not invent missing text.
            return item
        value += chunk.content[overlap:]
    start, end = max(start, origin), min(end, origin + len(value))
    return {**item, "range_start": start, "range_end": end,
            "text": value[start - origin:end - origin]}


def _complete_original(session, record, budget):
    """Reuse a current, bounded projection in full, preferring the publisher.

    Check its indexed extent before loading chunks. This never reparses an
    immutable artifact, and the same publication/authority joins as retrieval
    keep stale versions out. Callers have already checked the reader's scope.
    """
    base = _chunk_query([_get(record, "source_id")], [_get(record, "id")])
    for kind in KINDS:
        query = base.where(Publication.kind == kind)
        last = session.exec(query.order_by(Chunk.range_end.desc()).limit(1)).first()
        if last is None:
            continue
        extent = last[0].range_end
        label = f"【{LABELS[kind]}｜全文 0–{extent} 字】\n"
        if extent + len(label) > budget:
            # Do not replace a preferred publisher original with a smaller ASR
            # rendition merely to fit it. Fall back to ranked excerpts.
            return None
        rows = session.exec(query.where(Chunk.artifact_id == last[0].artifact_id)
                            .order_by(Chunk.range_start)).all()
        body = ""
        for row in rows:
            chunk = row[0]
            overlap = len(body) - chunk.range_start
            if (overlap < 0 or overlap > len(chunk.content)
                    or chunk.range_end - chunk.range_start != len(chunk.content)
                    or (overlap and body[chunk.range_start:] != chunk.content[:overlap])):
                return None  # A broken cache must not invent or omit text.
            body += chunk.content[overlap:]
        if len(body) != extent:
            return None
        return label, {**_evidence(last), "range_start": 0, "range_end": extent, "text": body}
    return None


def _uncovered_evidence(item, included):
    """Remove already supplied source ranges after neighboring-window expansion."""
    ranges = [(item["range_start"], item["range_end"])]
    for old in included:
        if old["artifact_id"] != item["artifact_id"]:
            continue
        remaining = []
        for start, end in ranges:
            left, right = max(start, old["range_start"]), min(end, old["range_end"])
            if left >= right:
                remaining.append((start, end))
            else:
                if start < left:
                    remaining.append((start, left))
                if right < end:
                    remaining.append((right, end))
        ranges = remaining
    return [{**item, "range_start": start, "range_end": end,
             "text": item["text"][start - item["range_start"]:end - item["range_start"]]}
            for start, end in ranges]


def _pack(record, evidence, budget, keywords=(), session=None):
    result = _as_dict(record)
    budget = max(0, int(budget))
    complete = _complete_original(session, record, budget) if session is not None else None
    if complete is not None:
        label, item = complete
        result.update(content=label + item["text"], qa_basis=LABELS[item["kind"]], qa_evidence=[item])
        return result
    if not evidence:
        notice = "【节目简介】尚无可用的已发布逐字稿；以下仅是简介，未读取音频，不能据此确认节目细节。\n"
        result.update(content=(notice + (result.get("content") or ""))[:budget], qa_basis="节目简介", qa_evidence=[])
        return result
    # Drop overlapping passages of the same artifact, retaining ranked relevance.
    distinct, uncovered = [], {normalize_for_search(k).casefold() for k in keywords if isinstance(k, str) and k.strip()}
    def covers(value, word):
        return all(token in value for token in word.split())
    pending = list(evidence)
    max_passages = max(4, min(6, budget // 2000))
    while pending and len(distinct) < max_passages:
        # Cover different concepts first, then preserve the FTS ordering.
        item = max(pending, key=lambda e: sum(covers(normalize_for_search(e["text"]).casefold(), word) for word in uncovered))
        pending.remove(item)
        if any(item["artifact_id"] == old["artifact_id"] and
               min(item["range_end"], old["range_end"]) - max(item["range_start"], old["range_start"]) > len(item["text"]) // 2
               for old in distinct):
            continue
        distinct.append(item)
        folded = normalize_for_search(item["text"]).casefold()
        uncovered -= {word for word in uncovered if covers(folded, word)}
    blocks, included = [], []
    remaining = budget
    for index, item in enumerate(distinct):
        share = remaining // (len(distinct) - index)
        # Conservative label reservation; recompute after truncating so the
        # reported end offset always describes exactly the text shown.
        take = max(0, share - 90)
        if session is not None and take > len(item["text"]):
            item = _surrounding_evidence(session, item, take)
        # A match near a passage's end must survive the final context budget.
        # re.IGNORECASE preserves source offsets (casefold can change length).
        import re
        positions = [m.start() for word in keywords if isinstance(word, str) and word.strip()
                     for m in [re.search(re.escape(normalize_for_search(word)), normalize_for_search(item["text"]), re.IGNORECASE)] if m]
        start = max(0, (min(positions) if positions else 0) - take // 4)
        start = min(start, max(0, len(item["text"]) - take))
        value = item["text"][start:start + take]
        if not value:
            continue
        selected = {**item, "text": value, "range_start": item["range_start"] + start,
                    "range_end": item["range_start"] + start + len(value)}
        # Seeds can be distinct while their expanded windows overlap heavily.
        # Spend each source character once; unused share goes to later concepts.
        for fragment in _uncovered_evidence(selected, included):
            label = f"【{LABELS[item['kind']]}｜相关片段 {fragment['range_start']}–{fragment['range_end']} 字（节选，非全文）】\n"
            block = label + fragment["text"]
            separator = "\n\n" if blocks else ""
            if len(separator) + len(block) > remaining:
                continue
            blocks.append(separator + block)
            included.append(fragment)
            remaining -= len(separator) + len(block)
    result.update(content="".join(blocks), qa_evidence=included,
                  qa_basis="、".join(dict.fromkeys(LABELS[e["kind"]] for e in included)))
    return result


def search_episodes(engine, *, keywords, source_ids, date_gte=None, date_lte=None, limit=100):
    """Find episodes by current transcript passages; empty keywords means recent."""
    with Session(engine) as session:
        sources = _allowed_sources(session, source_ids)
    if not sources or limit <= 0:
        return []
    _ensure_indexes(engine, source_ids=sources)
    with Session(engine) as session:
        sources = _allowed_sources(session, sources)
        if not any(isinstance(k, str) and k.strip() for k in keywords):
            query = _chunk_query(sources, date_gte=date_gte, date_lte=date_lte)
            episodes = session.execute(query.with_only_columns(ArticleRecord).distinct().order_by(ArticleRecord.fetched_date.desc()).limit(limit)).scalars().all()
            return [_pack(r, [_evidence(row) for row in _sample_chunks(session, sources, r.id)], 10000, session=session) for r in episodes]
        rows = _search_chunks(session, source_ids=sources, keywords=keywords,
                              date_gte=date_gte, date_lte=date_lte, limit=max(32, limit * 8))
        grouped, records = {}, {}
        for row in rows:
            aid = row[0].episode_id
            grouped.setdefault(aid, []).append(_evidence(row))
            records[aid] = row[3]
        return [_pack(records[aid], evidence, 10000, keywords, session=session) for aid, evidence in list(grouped.items())[:limit]]


def prepare_records(engine, records, *, keywords, per_episode_chars=10000, username=None):
    """Replace only Podcast records with bounded current evidence dictionaries."""
    podcasts = [r for r in records if _get(r, "content_type") == "podcast_episode"]
    if not podcasts:
        return list(records)
    with Session(engine) as session:
        sources = _allowed_sources(session, [_get(r, "source_id") for r in podcasts], username)
    ids = [_get(r, "id") for r in podcasts]
    _ensure_indexes(engine, source_ids=sources, episode_ids=ids)
    prepared = {}
    with Session(engine) as session:
        sources = _allowed_sources(session, sources, username)
        for record in podcasts:
            # Resolve fresh metadata instead of accepting stale source identity.
            current = session.get(ArticleRecord, _get(record, "id"))
            if current is None or current.source_id not in sources or current.content_type != "podcast_episode":
                continue
            rows = _search_chunks(session, source_ids=sources, episode_ids=[current.id], keywords=keywords, limit=64) if keywords else []
            if not rows:
                # No relevant match: sample across the whole text, not only its
                # beginning. The context clearly labels these as excerpts.
                rows = _sample_chunks(session, sources, current.id)
            prepared[current.id] = _pack(current, [_evidence(row) for row in rows], per_episode_chars, keywords, session=session)
    return [prepared[_get(r, "id")] if _get(r, "content_type") == "podcast_episode" else r
            for r in records if _get(r, "content_type") != "podcast_episode" or _get(r, "id") in prepared]


def revalidate_records(engine, records):
    """Fail closed if an evidence pointer or source permission changed meanwhile.

    Accept both content records and the small sources dictionaries. Remove the
    entire episode if any supplied transcript is no longer current; callers must
    not retain an answer or a citation based on a withdrawn passage.
    """
    kept = []
    with Session(engine) as session:
        for record in records:
            evidence = _get(record, "qa_evidence", _get(record, "evidence", None))
            is_podcast = _get(record, "content_type") == "podcast_episode" or evidence is not None or _get(record, "qa_basis") is not None
            if not is_podcast:
                kept.append(record)
                continue
            current = session.get(ArticleRecord, _get(record, "id"))
            if not current or current.content_type != "podcast_episode" or current.source_id not in _allowed_sources(session, [current.source_id]):
                continue
            active = {(p.artifact_id, p.kind, content_hash) for p, content_hash in session.exec(_published_query(episode_ids=[current.id])).all()}
            if all((e.get("artifact_id"), e.get("kind"), e.get("content_hash")) in active for e in (evidence or [])):
                kept.append(record)
    return kept
