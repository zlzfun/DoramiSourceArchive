#!/usr/bin/env python3
"""Draft a human-reviewable taxonomy v1 catalog without changing taxonomy state."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from config import settings  # noqa: E402
from llm.client import (  # noqa: E402
    ChatMessage,
    UsageMeta,
    chat_completion,
    client_session,
    parse_json_object,
)
from models.db import (  # noqa: E402
    ArticleRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
)
from services.daily_brief import resolve_llm_config  # noqa: E402
from services.taxonomy import ENTITY_TYPES, normalize_label  # noqa: E402
from services.taxonomy_bootstrap import (  # noqa: E402
    BOOTSTRAP_ID,
    BootstrapManifest,
    validate_manifest,
    validate_manifest_sources,
)


REVIEW_CHUNK_SIZE = 45
CODE_RE = re.compile(r"^(topic|industry|entity)\.[a-z0-9][a-z0-9._-]*$")

SYSTEM_PROMPT = """\
你是 taxonomy v1 的人工审核助手。输入是开放抽取后按分面聚合的 Candidate 及证据统计。
你的任务只是生成审核草案，不得声称已激活或发布任何标签。

请合并中英文翻译、缩写、单复数和明显同义词，纠正错误分面，舍弃一次性事件、具体版本、
模糊词、过窄概念和低价值实体。不要因为来源多就机械保留。规范 code 必须带分面前缀，
例如 topic.agentic-ai、industry.healthcare、entity.openai。Alias 只列真正等价的旧称、缩写、
翻译或原始 Candidate，不要把上下位概念互设 Alias。

suggested_user_selectable 仅作为长期稳定性提示，不控制首版开放：所有被产品接受的
Topic、Industry、Entity 首次都默认可选，再由各分面 Top N 决定展示。每个分面都必须返回
有效条目。Entity 必须给出
entity_type，且只能是 organization、product、model、protocol、project 之一；这是草案建议，
仍需产品逐项确认。返回 JSON 对象：
{"entries":[{"code":"...",\
"kind":"topic","name_zh":"...","name_en":"...","aliases":["..."],\
"description":"供管理员理解的概念边界",\
"prompt_description":"明确何时应打、何时不应打此标签的模型判定规则",\
"parent_code":"可选；仅填同分面上位标签 code",\
"entity_type":"仅 Entity 填写",\
"suggested_user_selectable":false,"source_labels":["输入中的原始label"],"rationale":"简短理由",\
"risk":"无则空字符串"}]}。只返回 JSON，不得返回 Markdown。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draft taxonomy v1 human-review artifacts.")
    parser.add_argument("--database-url", default=settings.storage.database_url)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_manifest(path: Path) -> BootstrapManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ("source_ids", "article_ids", "structural_labels"):
        raw[key] = tuple(raw.get(key) or ())
    manifest = BootstrapManifest(**raw)
    validate_manifest(manifest)
    return manifest


def load_bootstrap_candidate_summaries(
    session: Session,
    manifest: BootstrapManifest,
) -> tuple[list[dict[str, Any]], int, Counter[str]]:
    """Use only evidence created by this exact public bootstrap manifest."""

    validate_manifest_sources(session, manifest)
    if not manifest.article_ids:
        return [], 0, Counter()
    evidence = list(
        session.exec(
            select(CmsTagCandidateEvidenceRecord).where(
                CmsTagCandidateEvidenceRecord.article_id.in_(manifest.article_ids),
                CmsTagCandidateEvidenceRecord.prompt_version == BOOTSTRAP_ID,
            )
        ).all()
    )
    evidence_by_candidate: dict[int, list[CmsTagCandidateEvidenceRecord]] = {}
    for row in evidence:
        evidence_by_candidate.setdefault(row.candidate_id, []).append(row)
    candidates = {
        row.id: row
        for row in session.exec(
            select(CmsTagCandidateRecord).where(
                CmsTagCandidateRecord.id.in_(tuple(evidence_by_candidate))
            )
        ).all()
    } if evidence_by_candidate else {}
    summaries: list[dict[str, Any]] = []
    for candidate_id, rows in evidence_by_candidate.items():
        candidate = candidates.get(candidate_id)
        if candidate is None:
            continue
        summaries.append(
            {
                "candidate_id": int(candidate_id),
                "label": candidate.label,
                "normalized_label": candidate.normalized_label,
                "proposed_kind": candidate.proposed_kind,
                "support_articles_30d": len({row.article_id for row in rows}),
                "distinct_sources_30d": len({row.source_id for row in rows}),
                "distinct_days_30d": len(
                    {str(row.published_date or row.created_at)[:10] for row in rows}
                ),
                "mean_confidence": sum(row.confidence for row in rows) / len(rows),
                "article_ids": sorted({row.article_id for row in rows}),
                "source_ids": sorted({row.source_id for row in rows}),
                "evidence_days": sorted(
                    {str(row.published_date or row.created_at)[:10] for row in rows}
                ),
                "confidence_sum": sum(row.confidence for row in rows),
                "evidence_count": len(rows),
            }
        )
    source_counts: Counter[str] = Counter()
    for article_id in manifest.article_ids:
        article = session.get(ArticleRecord, article_id)
        if article is not None:
            source_counts[article.source_id] += 1
    return summaries, len(evidence), source_counts


def candidate_payload(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    selected = [row for row in rows if row["proposed_kind"] == kind]
    selected.sort(
        key=lambda row: (
            -row["support_articles_30d"],
            -row["distinct_sources_30d"],
            -row["mean_confidence"],
            normalize_label(row["label"]),
        )
    )
    return [
        {
            "label": row["label"],
            "support_articles_30d": row["support_articles_30d"],
            "distinct_sources_30d": row["distinct_sources_30d"],
            "distinct_days_30d": row["distinct_days_30d"],
            "mean_confidence": round(row["mean_confidence"], 3),
        }
        for row in selected
    ]


def validate_entries(
    payload: dict[str, Any],
    *,
    kind: str,
    source_labels: set[str],
) -> list[dict[str, Any]]:
    raw_entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(raw_entries, list):
        raise ValueError(f"taxonomy review output for {kind} must contain entries")
    result: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for raw in raw_entries:
        raw_kind = str(raw.get("kind") or "").strip().casefold() if isinstance(raw, dict) else ""
        if not isinstance(raw, dict) or raw_kind != kind:
            continue
        code = str(raw.get("code") or "").strip().casefold()
        if not CODE_RE.fullmatch(code) or not code.startswith(f"{kind}.") or code in seen_codes:
            continue
        name_zh = " ".join(str(raw.get("name_zh") or "").split())[:120]
        name_en = " ".join(str(raw.get("name_en") or "").split())[:120]
        if not name_zh and not name_en:
            continue
        canonical_names = {normalize_label(value) for value in (name_zh, name_en) if value}
        aliases = list(
            dict.fromkeys(
                " ".join(str(value or "").split())[:120]
                for value in (raw.get("aliases") if isinstance(raw.get("aliases"), list) else [])
                if str(value or "").strip()
                and normalize_label(str(value)) not in canonical_names
            )
        )[:12]
        mapped = list(
            dict.fromkeys(
                str(value)
                for value in (
                    raw.get("source_labels") if isinstance(raw.get("source_labels"), list) else []
                )
                if str(value) in source_labels
            )
        )
        seen_codes.add(code)
        entry = {
            "decision": "pending",
            "code": code,
            "kind": kind,
            "name_zh": name_zh,
            "name_en": name_en,
            "aliases": aliases,
            "description": " ".join(str(raw.get("description") or "").split())[:500],
            "prompt_description": " ".join(
                str(raw.get("prompt_description") or "").split()
            )[:1000],
            "parent_code": (
                str(raw.get("parent_code") or "").strip().casefold()
                if CODE_RE.fullmatch(str(raw.get("parent_code") or "").strip().casefold())
                and str(raw.get("parent_code") or "").strip().casefold().startswith(f"{kind}.")
                and str(raw.get("parent_code") or "").strip().casefold() != code
                else ""
            ),
            # Nothing is activated by drafting.  Once a human accepts/imports
            # this v1 entry, every facet starts with the same selectable rule;
            # explicit false remains available as a product override.
            "user_selectable": True,
            "suggested_user_selectable": bool(
                raw.get("suggested_user_selectable", raw.get("user_selectable", False))
            ),
            "source_labels": mapped,
            "rationale": " ".join(str(raw.get("rationale") or "").split())[:300],
            "risk": " ".join(str(raw.get("risk") or "").split())[:300],
        }
        if kind == "entity":
            entity_type = str(raw.get("entity_type") or "").strip().lower()
            entry["entity_type"] = entity_type if entity_type in ENTITY_TYPES else ""
            entry["external_key"] = None
        result.append(entry)
    return result


def mark_conflicts(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Surface same-facet Alias/source-label collisions for human resolution."""

    alias_owners: dict[tuple[str, str], set[str]] = {}
    source_owners: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        for alias in entry["aliases"]:
            alias_owners.setdefault((entry["kind"], normalize_label(alias)), set()).add(entry["code"])
        for label in entry["source_labels"]:
            source_owners.setdefault((entry["kind"], normalize_label(label)), set()).add(entry["code"])
    for entry in entries:
        conflicts = sorted(
            {
                code
                for value in (*entry["aliases"], *entry["source_labels"])
                for code in (
                    alias_owners.get((entry["kind"], normalize_label(value)), set())
                    | source_owners.get((entry["kind"], normalize_label(value)), set())
                )
                if code != entry["code"]
            }
        )
        if conflicts:
            warning = f"与 {', '.join(conflicts)} 存在 Alias/来源候选冲突"
            entry["risk"] = f"{entry['risk']}；{warning}".strip("；")
    return entries


def attach_evidence_metrics(
    entries: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach auditable aggregate evidence to every proposed canonical row."""

    by_key = {
        (row["proposed_kind"], row["label"]): row
        for row in candidates
    }
    for entry in entries:
        matched = [
            by_key[(entry["kind"], label)]
            for label in entry["source_labels"]
            if (entry["kind"], label) in by_key
        ]
        article_ids = {value for row in matched for value in row["article_ids"]}
        source_ids = {value for row in matched for value in row["source_ids"]}
        evidence_days = {value for row in matched for value in row["evidence_days"]}
        evidence_count = sum(int(row["evidence_count"]) for row in matched)
        entry["source_candidate_ids"] = sorted({int(row["candidate_id"]) for row in matched})
        entry["source_candidates"] = sorted(
            (
                {
                    "candidate_id": int(row["candidate_id"]),
                    "kind": row["proposed_kind"],
                    "label": row["label"],
                    "normalized_label": row["normalized_label"],
                }
                for row in matched
            ),
            key=lambda item: (item["kind"], item["normalized_label"]),
        )
        entry["support_articles_30d"] = len(article_ids)
        entry["distinct_sources_30d"] = len(source_ids)
        entry["distinct_days_30d"] = len(evidence_days)
        entry["mean_confidence"] = round(
            sum(float(row["confidence_sum"]) for row in matched) / evidence_count,
            3,
        ) if evidence_count else 0.0
    return entries


def review_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """Return the public, JSON-serializable fields for an unmapped row."""

    return {
        "candidate_id": int(row["candidate_id"]),
        "label": row["label"],
        "normalized_label": row["normalized_label"],
        "proposed_kind": row["proposed_kind"],
        "support_articles_30d": int(row["support_articles_30d"]),
        "distinct_sources_30d": int(row["distinct_sources_30d"]),
        "distinct_days_30d": int(row["distinct_days_30d"]),
        "mean_confidence": round(float(row["mean_confidence"]), 3),
        "decision": "pending",
        "resolution_code": "",
    }


async def draft_entries(
    candidates: dict[str, list[dict[str, Any]]],
    llm_config,
) -> list[dict[str, Any]]:
    llm_cfg = llm_config.for_aux()
    semaphore = asyncio.Semaphore(max(1, min(int(llm_config.map_concurrency), 4)))
    async with client_session(llm_cfg) as http_client:
        async def draft_chunk(kind: str, chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
            async with semaphore:
                raw = await chat_completion(
                    messages=[
                        ChatMessage(role="system", content=SYSTEM_PROMPT),
                        ChatMessage(
                            role="user",
                            content=json.dumps(
                                {
                                    "facet": kind,
                                    "maximum_entries": len(chunk),
                                    "candidates": chunk,
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        ),
                    ],
                    config=llm_cfg,
                    temperature=0.1,
                    max_tokens=8192,
                    response_json=True,
                    usage_meta=UsageMeta(purpose="taxonomy_review_draft", username=None),
                    http_client=http_client,
                )
            entries = validate_entries(
                parse_json_object(raw),
                kind=kind,
                source_labels={item["label"] for item in chunk},
            )
            if chunk and not entries:
                raise ValueError(f"taxonomy review draft returned no valid {kind} entries")
            return entries

        jobs = [
            draft_chunk(kind, rows[offset : offset + REVIEW_CHUNK_SIZE])
            for kind, rows in candidates.items()
            for offset in range(0, len(rows), REVIEW_CHUNK_SIZE)
        ]
        drafted = await asyncio.gather(*jobs)
    by_code: dict[str, dict[str, Any]] = {}
    for entry in (entry for chunk in drafted for entry in chunk):
        existing = by_code.get(entry["code"])
        if existing is None:
            by_code[entry["code"]] = entry
            continue
        existing["aliases"] = list(dict.fromkeys([*existing["aliases"], *entry["aliases"]]))[:24]
        existing["source_labels"] = list(
            dict.fromkeys([*existing["source_labels"], *entry["source_labels"]])
        )
        existing["suggested_user_selectable"] = bool(
            existing["suggested_user_selectable"] or entry["suggested_user_selectable"]
        )
        if entry["risk"] and entry["risk"] not in existing["risk"]:
            existing["risk"] = "；".join(value for value in (existing["risk"], entry["risk"]) if value)
    return mark_conflicts(list(by_code.values()))


def markdown_review(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    complete_coverage = int(coverage.get("sampled_source_count", 0)) >= int(
        coverage.get("manifest_source_count", 0)
    )
    coverage_note = (
        "- 来源覆盖已达到本次 manifest，可继续完成逐项产品决策。"
        if complete_coverage
        else "- 当前覆盖不足，必须补齐来源或由产品明确接受偏差后，才能发布 taxonomy v1。"
    )
    lines = [
        "# Taxonomy v1 产品审核草案",
        "",
        "> 本文档仅是 LLM 辅助归并草案；所有条目均为 `pending`，未激活、未发布、未回填。",
        "",
        "## 采样覆盖",
        "",
        f"- Manifest：`{report['manifest_sha256']}`",
        f"- 文章：{coverage['article_count']} 篇；有样本来源：{coverage['sampled_source_count']} / {coverage['manifest_source_count']}",
        f"- Candidate：{coverage['candidate_count']} 个；Evidence：{coverage['evidence_count']} 条",
        coverage_note,
        "",
        "## 审核方法",
        "",
        "把每项的 `decision` 改成 `accept`、`edit` 或 `reject`；被接受的三个分面首版均默认用户可选，只有明确需要下架时才把 `user_selectable` 改为 false。",
        "重点检查：跨分面同名、上下位概念误合并、短期事件/版本、实体稳定标识，以及中英文 Alias 是否真正等价。",
    ]
    labels = {"topic": "Topic", "industry": "Industry", "entity": "Entity"}
    for kind in ("topic", "industry", "entity"):
        entries = [entry for entry in report["entries"] if entry["kind"] == kind]
        lines.extend(["", f"## {labels[kind]}（{len(entries)}）", ""])
        for entry in entries:
            names = " / ".join(value for value in (entry["name_zh"], entry["name_en"]) if value)
            aliases = "、".join(entry["aliases"]) or "—"
            source_labels = "、".join(entry["source_labels"]) or "—"
            selectable = "是" if entry["suggested_user_selectable"] else "否"
            risk = entry["risk"] or "无"
            lines.extend(
                [
                    f"- [ ] `{entry['code']}` — {names}",
                    f"  - decision: `{entry['decision']}`；接受后默认用户可选：是；模型长期稳定性建议：{selectable}",
                    f"  - 证据：{entry.get('support_articles_30d', 0)} 篇 / {entry.get('distinct_sources_30d', 0)} 源 / {entry.get('distinct_days_30d', 0)} 天；平均置信 {float(entry.get('mean_confidence', 0)):.0%}",
                    f"  - 规范名解析入口：{names or '—'}",
                    f"  - Alias：{aliases}",
                    f"  - 来源候选：{source_labels}",
                    f"  - 理由：{entry['rationale'] or '—'}；风险：{risk}",
                ]
            )
            if kind == "entity":
                lines.append(
                    f"  - Entity 类型：`{entry.get('entity_type') or '待产品确认'}`；external key：`{entry.get('external_key') or '待确认/可空'}`"
                )
    unmapped = report.get("unmapped_candidates") or []
    lines.extend(["", f"## 尚未归并的 Candidate（{len(unmapped)}）", ""])
    if unmapped:
        lines.append("以下候选未被草案条目引用，仍需把 `decision` 改为 `merge` 或 `reject`；要独立收录时请提升为上方规范条目。它们不会再被摘要上限静默丢弃。")
        for item in unmapped:
            resolution = f" → `{item['resolution_code']}`" if item.get("resolution_code") else ""
            lines.append(f"- [ ] `{item['proposed_kind']}` · {item['label']}（{item['support_articles_30d']} 篇 / {item['distinct_sources_30d']} 源）")
            lines.append(f"  - decision: `{item.get('decision', 'pending')}`{resolution}")
    else:
        lines.append("所有输入 Candidate 都已映射到至少一个草案条目。")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    for path in (args.json_out, args.markdown_out):
        if path.exists() and not args.overwrite:
            raise SystemExit(f"refusing to overwrite {path}; pass --overwrite")
    manifest = load_manifest(args.manifest)
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            rows, evidence_count, source_counts = load_bootstrap_candidate_summaries(
                session,
                manifest,
            )
            llm_config = resolve_llm_config(session)
        if not llm_config.configured:
            raise SystemExit("LLM is not configured")
        candidates = {kind: candidate_payload(rows, kind) for kind in ("topic", "industry", "entity")}
        entries = attach_evidence_metrics(
            asyncio.run(draft_entries(candidates, llm_config)),
            rows,
        )
        mapped_labels = {label for entry in entries for label in entry["source_labels"]}
        unmapped = [
            review_candidate(row)
            for row in rows
            if row["label"] not in mapped_labels
        ]
        unmapped.sort(
            key=lambda row: (
                row["proposed_kind"],
                -row["support_articles_30d"],
                -row["distinct_sources_30d"],
                normalize_label(row["label"]),
            )
        )
        report = {
            "status": "human_review_required",
            "manifest_sha256": manifest.manifest_sha256,
            "coverage": {
                "manifest_source_count": len(manifest.source_ids),
                "sampled_source_count": len(source_counts),
                "sampled_sources": dict(sorted(source_counts.items())),
                "article_count": len(manifest.article_ids),
                "candidate_count": len(rows),
                "evidence_count": evidence_count,
            },
            "entries": entries,
            "unmapped_candidates": unmapped,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        args.markdown_out.write_text(markdown_review(report), encoding="utf-8")
        print(
            json.dumps(
                {
                    "candidate_count": len(rows),
                    "draft_entry_count": len(entries),
                    "entries_by_kind": dict(Counter(entry["kind"] for entry in entries)),
                    "unmapped_candidate_count": len(unmapped),
                    "json_out": str(args.json_out),
                    "markdown_out": str(args.markdown_out),
                    "sampled_source_count": len(source_counts),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
