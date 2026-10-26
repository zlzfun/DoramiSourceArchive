"""Narrow preservation of verified node-local translations during Archive Sync.

Only the explicitly known final translation groups survive a remote extension
replacement. Account preferences, partial jobs and arbitrary local extensions
are deliberately outside this merge.
"""
from __future__ import annotations

import json

from models.db import ArticleRecord
from services.title_cache import extensions


def preserve_local_translations(
    existing: ArticleRecord, incoming: ArticleRecord, authority_id: str,
) -> str:
    """Return incoming extensions with eligible local final caches preserved.

    A new authority never adopts a previous owner's caches. For the same owner,
    body identity is the actual translation input, independent of the title or
    paper analysis summary. Social identity includes repost/quote snapshots.
    """
    if (
        not authority_id
        or existing.analysis_authority_id != authority_id
        or existing.id != incoming.id
        or existing.source_id != incoming.source_id
        or existing.content_type != incoming.content_type
    ):
        return incoming.extensions_json

    # Share the translation writer's source normalization and versions. Import
    # lazily to avoid coupling module initialization to the reader AI services.
    from services.reader_ai import (
        TRANSLATION_KEY, TRANSLATION_FP_KEY, TRANSLATION_VERSION_KEY,
        TRANSLATION_VERSION, SOCIAL_TRANSLATION_KEY, SOCIAL_TRANSLATION_VERSION,
        _body_fingerprint, _social_fingerprint, social_translation_sources,
    )

    local = extensions(existing)
    remote = extensions(incoming)
    changed = False
    local_body = (existing.content or "").strip()
    incoming_body = (incoming.content or "").strip()

    def valid_body(ext: dict, fp: str) -> bool:
        value = ext.get(TRANSLATION_KEY)
        return (
            isinstance(value, str) and bool(value.strip())
            and ext.get(TRANSLATION_FP_KEY) == fp
            and type(ext.get(TRANSLATION_VERSION_KEY)) is int
            and ext.get(TRANSLATION_VERSION_KEY) == TRANSLATION_VERSION
        )

    if local_body and local_body == incoming_body:
        body_fp = _body_fingerprint(incoming_body)
        if valid_body(local, body_fp) and not valid_body(remote, body_fp):
            for key in (TRANSLATION_KEY, TRANSLATION_FP_KEY, TRANSLATION_VERSION_KEY):
                remote[key] = local[key]
            changed = True

    local_sources = social_translation_sources(existing.content or "", local)
    incoming_sources = social_translation_sources(incoming.content or "", remote)

    def valid_social(ext: dict, fp: str) -> bool:
        entry = ext.get(SOCIAL_TRANSLATION_KEY)
        if not isinstance(entry, dict):
            return False
        # A source containing only protected literals can have no translated
        # value; the other side must still contain a usable translated result.
        values = (entry.get("text"), entry.get("quoted"))
        return (
            entry.get("fp") == fp
            and type(entry.get("v")) is int
            and entry.get("v") == SOCIAL_TRANSLATION_VERSION
            and all(value is None or isinstance(value, str) for value in values)
            and any(isinstance(value, str) and value.strip() for value in values)
        )

    if local_sources and local_sources == incoming_sources:
        social_fp = _social_fingerprint(incoming_sources)
        if valid_social(local, social_fp) and not valid_social(remote, social_fp):
            # Do not preserve arbitrary future keys even inside a cache group.
            entry = local[SOCIAL_TRANSLATION_KEY]
            remote[SOCIAL_TRANSLATION_KEY] = {
                key: entry.get(key) for key in ("text", "quoted", "fp", "v")
            }
            changed = True

    return json.dumps(remote, ensure_ascii=False) if changed else incoming.extensions_json
