"""Request-local QA contract. Checks structure and provenance, never entailment.

The original question and source bodies stay in memory for this request only.
Diagnostics contain counters, hashes and ranges, never questions or quotations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import time
import uuid

from llm.client import LLMStructuredOutputError, parse_json_object

MAX_PARTS = 12
_CITATION = re.compile(r"\[(\d+)\]")
_GREETING = re.compile(r"(?:(?:你好|您好|谢谢你?|多谢|辛苦了|早上好|晚上好|再见|hi|hello|thanks|thank you)[\s，,。.!！?？、]*)+", re.I)


def simple_chat(question: str) -> bool:
    # Entire-message match: “你好，解释这个结论” must still retrieve evidence.
    return bool(_GREETING.fullmatch(question.strip()))


def clean_parts(raw, question: str) -> list[dict]:
    """Reject the whole malformed/overlong plan instead of silently losing parts."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_PARTS:
        return [{"id": "q1", "question": question, "keywords": []}]
    parts = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            return clean_parts(None, question)
        value = item["question"].strip()
        if not value or len(value) > 1000:
            return clean_parts(None, question)
        words = item.get("keywords")
        words = words if isinstance(words, list) else []
        parts.append({"id": f"q{len(parts) + 1}", "question": value, "keywords": list(dict.fromkeys(
            word.strip() for word in words if isinstance(word, str) and 2 <= len(word.strip()) <= 80
        ))[:2]})
    return parts


def part_keywords(parts: list[dict]) -> list[str]:
    """One term per part before second synonyms; the existing retrieval cap stays 12."""
    return list(dict.fromkeys(word for slot in range(2) for part in parts
                              for word in part.get("keywords", [])[slot:slot + 1]))[:12]


def validate_citations(answer: str, source_count: int) -> set[int]:
    # Code samples such as array[99] are not source references.
    prose = re.sub(r"(```|~~~)[\s\S]*?\1", "", answer)
    prose = re.sub(r"(`+)([^`]|(?!\1)`)*?\1", "", prose)
    tokens = _CITATION.findall(prose)
    if any(len(n) > 9 for n in tokens):
        raise LLMStructuredOutputError("qa_citation", "回答引用与本轮资料不一致，请重新提问")
    numbers = {int(n) for n in tokens}
    if any(n < 1 or n > source_count for n in numbers):
        raise LLMStructuredOutputError("qa_citation", "回答引用与本轮资料不一致，请重新提问")
    return numbers


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def traced_completion(call, *, state, stage, **kwargs):
    """One logical call; protocol fallbacks remain bounded by the shared client."""
    meta = {}
    config = kwargs["config"]
    started = time.monotonic()
    try:
        return await call(**kwargs, require_complete=True, max_retries=1, response_meta=meta)
    finally:
        if state is not None:
            model = config.model
            if config.api_key and config.api_key in model:
                model = "[redacted]"
            state.calls.append({"stage": stage, "requested_model": model[:200],
                                "thinking_mode": config.thinking_mode, "max_tokens": config.max_tokens,
                                "elapsed_ms": round((time.monotonic() - started) * 1000), **meta})


@dataclass
class QAState:
    question: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: float = field(default_factory=time.monotonic)
    intent: str = "evidence"
    parts: list[dict] = field(default_factory=list)
    # Each body is exactly the text actually injected for that source, not a
    # regex parse of attacker-controlled “[n]” markers in article content.
    sources: list[dict] = field(default_factory=list)
    stages: dict = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    checked_parts: list[dict] = field(default_factory=list)
    outcome: str = "in_progress"

    def __post_init__(self):
        self.parts = clean_parts(self.parts or None, self.question)
        if simple_chat(self.question):
            self.intent = "chat"

    def apply_plan(self, plan):
        if plan is None:
            self.stages["plan"] = "fallback"
            return
        self.stages["plan"] = "planned"
        self.intent = "chat" if plan.get("chat") is True else "evidence"
        self.parts = clean_parts(plan.get("subquestions"), self.question)

    def capture_source(self, record, body: str):
        get = record.get if isinstance(record, dict) else lambda key, default=None: getattr(record, key, default)
        self.sources.append({"id": get("id"), "body": body,
                             "evidence": get("qa_evidence") or []})

    def lexical_hits(self, body: str) -> list[str]:
        """Diagnostic search-term hits, explicitly NOT semantic coverage."""
        from storage.fts import normalize_for_search
        folded = normalize_for_search(body).casefold()
        return [part["id"] for part in self.parts if any(
            all(token in folded for token in normalize_for_search(word).casefold().split())
            for word in part["keywords"] if word.strip()
        )]

    def diagnostic(self) -> dict:
        return {
            "trace_id": self.trace_id, "outcome": self.outcome, "intent": self.intent,
            "elapsed_ms": round((time.monotonic() - self.started) * 1000),
            "planned_parts": len(self.parts), "stages": dict(self.stages),
            "calls": list(self.calls), "checked_parts": list(self.checked_parts),
            "sources": [{"id": source["id"], "chars": len(source["body"]),
                         "sha256": _hash(source["body"]),
                         "lexical_part_hits": self.lexical_hits(source["body"]),
                         "ranges": [{key: item[key] for key in (
                             "artifact_id", "content_hash", "range_start", "range_end", "kind"
                         ) if key in item} for item in source["evidence"]]}
                        for source in self.sources],
            "semantic_status": "not_evaluated",
        }


def render_structured_answer(raw: str, state: QAState) -> str:
    """Validate every planned ID and bind quotations to current source text.

    No repair call, no second model rewrite. A matching quote does NOT prove the
    answer follows from it; missing/contradictory evidence remains a semantic eval.
    """
    def reject(code):
        raise LLMStructuredOutputError(code, "回答的依据或完整性检查未通过，请重新提问")

    data = parse_json_object(raw)
    rows = data.get("parts")
    expected = [part["id"] for part in state.parts]
    if not isinstance(rows, list) or len(rows) != len(expected):
        reject("qa_parts_missing")
    by_id = {}
    diagnostics = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            reject("qa_parts_invalid")
        part_id = row["id"]
        if part_id not in expected or part_id in by_id:
            reject("qa_parts_invalid")
        status, answer, evidence = row.get("status"), row.get("answer"), row.get("evidence")
        if status not in ("answered", "partial", "insufficient") or not isinstance(answer, str) or not answer.strip():
            reject("qa_part_empty")
        if len(answer) > 12000 or not isinstance(evidence, list) or len(evidence) > 8:
            reject("qa_part_invalid")
        if status != "insufficient" and not evidence:
            reject("qa_evidence_missing")
        refs, proofs = set(), []
        for entry in evidence:
            if not isinstance(entry, dict):
                reject("qa_evidence_invalid")
            n, quote = entry.get("source"), entry.get("quote")
            if type(n) is not int or not 1 <= n <= len(state.sources):
                reject("qa_citation")
            if not isinstance(quote, str) or not 2 <= len(quote.strip()) <= 1000:
                reject("qa_quote_invalid")
            source = state.sources[n - 1]
            # Exact substring; no normalization that might erase negations or
            # merge speaker turns, and no match against another source.
            if quote not in source["body"]:
                reject("qa_quote_missing")
            proof = {"source": n, "quote_sha256": _hash(quote)}
            if source["evidence"]:
                fragment = next((e for e in source["evidence"] if quote in e["text"]), None)
                if fragment is None:
                    reject("qa_quote_missing")
                start = fragment["range_start"] + fragment["text"].index(quote)
                if start + len(quote) > fragment["range_end"]:
                    reject("qa_range_invalid")
                proof.update(artifact_id=fragment["artifact_id"], content_hash=fragment["content_hash"],
                             range_start=start, range_end=start + len(quote))
            refs.add(n)
            proofs.append(proof)
        cited = validate_citations(answer, len(state.sources))
        if not cited.issubset(refs):
            reject("qa_citation_without_quote")
        # The model writes the user-facing prose once. Only missing source links
        # are appended mechanically; no removal of conditions or limitations.
        by_id[part_id] = answer.strip() + "".join(f"[{n}]" for n in sorted(refs - cited))
        diagnostics.append({"id": part_id, "status": status, "evidence": proofs})
    state.checked_parts = diagnostics
    return "\n\n".join(by_id[key] for key in expected)
