"""Request-local evidence IDs. Text and provenance are owned by the application.

This is an index into already budgeted context, not a retriever or a semantic
judge. No normalization, approximate matching, or model-authored quotation.
"""
from __future__ import annotations

import hashlib
import json
import re

CONTRACT_VERSION = "evidence_ids_v3"
PASSAGE_CHARS = 800


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _spans(body):
    """Bound long lines without erasing whitespace, words or speaker markers."""
    for line in re.finditer(r"[^\r\n]+", body):
        start, end = line.span()
        while start < end:
            stop = min(end, start + PASSAGE_CHARS)
            if stop < end:
                boundary = body.rfind(" ", start + PASSAGE_CHARS // 2, stop)
                if boundary >= 0:
                    stop = boundary
            text = body[start:stop]
            left = len(text) - len(text.lstrip())
            right = len(text.rstrip())
            if left < right:
                yield start + left, start + right
            start = stop


def _binding(text, fragments, cursors):
    # Search only the current source's authorized fragments. Repeated lines
    # advance within each fragment, preserving their distinct original offsets.
    for i, fragment in enumerate(fragments):
        original = fragment["text"]
        start, end = fragment["range_start"], fragment["range_end"]
        if (type(start) is not int or type(end) is not int or start < 0
                or end - start != len(original)):
            raise ValueError("invalid QA artifact range")
        at = original.find(text, cursors[i])
        if at >= 0:
            cursors[i] = at + len(text)
            return {"artifact_id": fragment["artifact_id"], "content_hash": fragment["content_hash"],
                    "range_start": start + at, "range_end": start + at + len(text)}
    return None


def build_catalog(sources):
    """Return serialized input and an ID map; never parse source headers in text.

    Podcast labels or unbound text stay visible as context without selectable
    IDs. Only literal spans of both injected body and authorized artifact get
    content IDs. Metadata IDs never acquire transcript provenance.
    """
    index, documents = {}, []
    for number, source in enumerate(sources, 1):
        body, fragments = source["body"], source["evidence"]
        cursors = [0] * len(fragments)
        pieces = []
        for start, end in _spans(body):
            proof = _binding(body[start:end], fragments, cursors) if fragments else None
            kind = "content" if not fragments or proof is not None else "context"
            current = {"kind": kind, "body_start": start, "body_end": end, **(proof or {})}
            if pieces and pieces[-1]["kind"] == kind:
                previous = pieces[-1]
                same_origin = (not fragments or kind == "context" or (
                    previous.get("artifact_id") == current.get("artifact_id")
                    and previous.get("content_hash") == current.get("content_hash")
                    and current["range_start"] - previous["range_end"] == start - previous["body_end"]
                    # Check the gap too: equal lengths do not prove equal text.
                    and any(f["artifact_id"] == current["artifact_id"]
                            and f["content_hash"] == current["content_hash"]
                            and f["range_start"] <= previous["range_start"]
                            and f["range_end"] >= current["range_end"]
                            and f["text"][previous["range_start"] - f["range_start"]:
                                          current["range_end"] - f["range_start"]]
                            == body[previous["body_start"]:end] for f in fragments)))
                if same_origin and end - previous["body_start"] <= PASSAGE_CHARS:
                    previous["body_end"] = end
                    if proof:
                        previous["range_end"] = proof["range_end"]
                    continue
            pieces.append(current)
        document = {"source": number, "passages": []}
        header = source.get("header", "")
        if header:
            identity = {"source": number, "source_id": source["id"], "kind": "metadata", "text": header}
            key = f"s{number}m"
            index[key] = {**identity, "binding_sha256": fingerprint(identity),
                          "text_sha256": hashlib.sha256(header.encode()).hexdigest()}
            document["metadata"] = {"id": key, "text": header}
        for i, piece in enumerate(pieces, 1):
            text = body[piece["body_start"]:piece["body_end"]]
            if piece["kind"] == "context":
                document["passages"].append({"kind": "context_only", "text": text})
                continue
            identity = {"source": number, "source_id": source["id"], **piece, "text": text}
            key = f"s{number}p{i}"
            index[key] = {**identity, "binding_sha256": fingerprint(identity),
                          "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
            document["passages"].append({"id": key, "text": text})
        documents.append(document)
    return json.dumps({"sources": documents}, ensure_ascii=False, separators=(",", ":")), index
