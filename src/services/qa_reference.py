"""Read-only QA reference navigation. Anchors locate text, never prove a claim."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from sqlmodel import Session

from models.db import ArticleRecord, PodcastTextArtifactRecord, PodcastTextPublicationRecord
from services import podcast_qa, source_visibility, user_sources
from services.podcast_text_reader import (
    _plain_text, PodcastTextReaderBadRequest, PodcastTextReaderNotFound,
)

NEIGHBOR_CHARS = 600
FOCUS_CHARS = 800
MAX_LOCATIONS = 4


class ReferenceChanged(RuntimeError):
    pass


def _sign(payload, secret):
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), ("qa-reference-v1." + encoded).encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def reference_sources(sources, state, *, secret, accepted):
    """Add bounded anchors on copies; never mutate the captured source snapshot.

    Validated model selections get priority; without them, the first excerpt's
    beginning is a reading aid, explicitly not an inferred answer location.
    """
    result = []
    for number, source in enumerate(sources, 1):
        fragments = source.get("evidence") or []
        proofs = [proof for part in state.checked_parts for proof in part["evidence"]
                  if proof["source"] == number and proof.get("artifact_id")] if accepted else []
        candidates = [(proof, "selected") for proof in proofs]
        if not candidates:
            candidates = [(fragment, "excerpt_start") for fragment in fragments]
        locations, seen = [], set()
        for candidate, origin in candidates:
            for fragment in fragments:
                start = candidate.get("range_start")
                end = candidate.get("range_end")
                if (type(start) is not int or type(end) is not int or end <= start
                        or fragment.get("kind") not in podcast_qa.KINDS
                        or candidate.get("artifact_id") != fragment.get("artifact_id")
                        or candidate.get("content_hash") != fragment.get("content_hash")
                        or start < fragment["range_start"] or end > fragment["range_end"]):
                    continue
                end = min(end, start + FOCUS_CHARS)
                identity = (fragment["artifact_id"], start, end)
                if identity in seen:
                    break
                seen.add(identity)
                focus = fragment["text"][start-fragment["range_start"]:end-fragment["range_start"]]
                if len(focus) != end - start:
                    continue
                payload = {"v": podcast_qa.PROJECTION_VERSION, "episode": source["id"],
                           "artifact": fragment["artifact_id"], "hash": fragment["content_hash"],
                           "kind": fragment["kind"], "start": start, "end": end,
                           "focus_hash": hashlib.sha256(focus.encode()).hexdigest()}
                locations.append({"token": _sign(payload, secret), "origin": origin})
                break
            if len(locations) == MAX_LOCATIONS:
                break
        result.append({**source, **({"reference_locations": locations} if locations else {})})
    return result


def refinement_questions(state, answer_mode):
    if answer_mode not in ("partial", "references_only"):
        return []
    missing = {part["id"] for part in state.checked_parts if part["status"] != "answered"}
    questions = []
    for part in state.parts:
        question = part["question"].strip()
        if (answer_mode == "partial" and part["id"] not in missing
                or question == state.question.strip() or not 1 <= len(question) <= 1000
                or question in questions):
            continue
        questions.append(question)
    return questions[:3]


def read_reference_context(session: Session, *, episode_id, token, username, config, secret):
    try:
        encoded, signature = token.split(".")
        expected = hmac.new(secret.encode(), ("qa-reference-v1." + encoded).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        start, end = payload["start"], payload["end"]
        if (payload["episode"] != episode_id or payload["kind"] not in podcast_qa.KINDS
                or type(start) is not int or type(end) is not int or start < 0
                or not 0 < end - start <= FOCUS_CHARS):
            raise ValueError("range")
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise PodcastTextReaderBadRequest("原文位置无效，请重新提问后查看") from exc

    # One database snapshot includes source permissions, publication and text.
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN")
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    episode = session.get(ArticleRecord, episode_id)
    if (episode is None or episode.content_type != "podcast_episode"
            or episode.source_id in source_visibility.reader_unavailable_source_ids(session)
            or user_sources.unauthorized_user_source_ids(session, username, [episode.source_id])
            or not user_sources.source_content_may_leave_deployment(session, episode.source_id)):
        raise PodcastTextReaderNotFound("这份资料已不可用，请重新选择来源")
    publication = session.get(PodcastTextPublicationRecord, f"{episode_id}:{payload['kind']}")
    artifact = session.get(PodcastTextArtifactRecord, payload["artifact"])
    if (payload["v"] != podcast_qa.PROJECTION_VERSION or publication is None
            or publication.status != "published" or not publication.published_at
            or artifact is None or publication.artifact_id != artifact.id
            or artifact.episode_id != episode_id or artifact.kind != payload["kind"]
            or artifact.authority_id != publication.authority_id
            or artifact.content_hash != payload["hash"]
            or hashlib.sha256(artifact.inline_text.encode()).hexdigest() != payload["hash"]):
        raise ReferenceChanged("逐字稿已更新或撤下，请重新提问后查看")
    # Match QA indexing's readable projection, including word-level JSON grouping.
    text = _plain_text(artifact, config, for_reading=True)
    focus = text[start:end]
    if end > len(text) or hashlib.sha256(focus.encode()).hexdigest() != payload["focus_hash"]:
        raise ReferenceChanged("原文位置已变化，请重新提问后查看")
    left, right = max(0, start-NEIGHBOR_CHARS), min(len(text), end+NEIGHBOR_CHARS)
    return {"before": text[left:start], "focus": focus, "after": text[end:right],
            "has_before": left > 0, "has_after": right < len(text)}
