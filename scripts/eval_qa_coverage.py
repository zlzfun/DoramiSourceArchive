#!/usr/bin/env python3
"""Freeze QA inputs/rubrics, then explicitly run a bounded answer-only experiment.

No app, database, retrieval, ASR or TTS startup. `prepare` is offline; `run` needs
an explicit model, rates and attempt/estimated-cost limits. Semantic grading is
human work: successful HTTP and resolvable citations never constitute a pass.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx

from config import LLMConfig
from llm.client import ChatMessage, chat_completion
from services.reader_ai import build_qa_messages


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_new(path, value):
    # Exclusive creation prevents overwriting failed runs or resetting a journal.
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(cases, *, today, runs=3, baseline=None):
    if not cases or not 1 <= runs <= 10:
        raise ValueError("nonempty cases and 1..10 repeats required")
    seen = set()
    jobs = []
    for case in cases:
        if not case.get("id") or case["id"] in seen:
            raise ValueError("case IDs must be nonempty and unique")
        seen.add(case["id"])
        if not case.get("rubric") or not isinstance(case.get("source_count"), int) or case["source_count"] < 0:
            raise ValueError("each case needs a frozen rubric and source_count")
        messages = [m.to_dict() for m in build_qa_messages(
            case["question"], case["context"], scope=case.get("scope", "article"),
            history=case.get("history"), today=today,
        )]
        variants = {"candidate": messages}
        if baseline is not None:
            if not baseline.strip():
                raise ValueError("baseline prompt must not be empty")
            variants["baseline"] = [{"role": "system", "content": baseline}, *messages[1:]]
        # Alternate order by repetition to reduce a fixed order/time bias.
        for repeat in range(runs):
            order = list(variants) if repeat % 2 == 0 else list(reversed(variants))
            for variant in order:
                jobs.append({"case_id": case["id"], "variant": variant, "repeat": repeat + 1,
                             "messages": variants[variant], "source_count": case["source_count"],
                             "messages_sha256": digest(variants[variant])})
    payload = {"schema_version": 1, "today": today, "cases": cases, "jobs": jobs,
               "scope": "frozen_context_answer_only; retrieval not evaluated"}
    return {"payload": payload, "sha256": digest(payload)}


def verify_bundle(bundle):
    payload = bundle["payload"]
    if payload.get("schema_version") != 1 or bundle["sha256"] != digest(payload):
        raise ValueError("frozen batch hash mismatch")
    if not payload.get("jobs"):
        raise ValueError("empty batch")
    for job in payload["jobs"]:
        if job["messages_sha256"] != digest(job["messages"]):
            raise ValueError("frozen messages hash mismatch")
    return payload


def citation_check(answer, source_count):
    indices = [int(n) for n in re.findall(r"\[(\d+)\]", answer)]
    return {"indices": indices, "invalid_indices": sorted({n for n in indices if not 1 <= n <= source_count}),
            "has_citation": bool(indices), "semantic_support": "not_evaluated"}


def endpoint_identity(url, secret):
    parts = urlsplit(str(url))
    value = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
    return value.replace(secret, "[REDACTED]") if secret else value


class BudgetExceeded(RuntimeError):
    pass


class RecordingTransport(httpx.AsyncBaseTransport):
    """Reserve before every physical request, including compatibility fallback.

    The byte-based token estimate plus output cap is an estimate guard, never a
    provider billing guarantee. Unknown/failed attempts retain their reservation.
    """

    def __init__(self, inner, *, out, max_attempts, max_cny, input_rate, output_rate, secret):
        values = (max_cny, input_rate, output_rate)
        if max_attempts <= 0 or any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("positive finite attempt and estimate limits required")
        self.inner, self.out = inner, Path(out)
        self.max_attempts, self.max_cny = max_attempts, max_cny
        self.input_rate, self.output_rate, self.secret = input_rate, output_rate, secret
        self.attempts, self.reserved_cny = [], 0.0

    async def handle_async_request(self, request):
        body = json.loads(request.content)
        reserve = ((len(request.content) + 512) * self.input_rate
                   + body["max_tokens"] * self.output_rate) / 1_000_000
        if len(self.attempts) >= self.max_attempts or self.reserved_cny + reserve > self.max_cny:
            raise BudgetExceeded("HTTP attempt or estimated-cost limit reached")
        number = len(self.attempts) + 1
        record = {"attempt": number, "request": body, "reserved_cny": reserve,
                  "status": "reserved", "response": None,
                  "endpoint": endpoint_identity(request.url, self.secret)}
        write_new(self.out / f"attempt-{number:04d}-reserved.json", record)
        self.attempts.append(record)
        self.reserved_cny += reserve
        start = time.monotonic()
        try:
            response = await self.inner.handle_async_request(request)
            try:
                content = await response.aread()
                raw = content.decode("utf-8", errors="replace")
                if self.secret:
                    raw = raw.replace(self.secret, "[REDACTED]")
                record.update(status="received", http_status=response.status_code, response=raw)
                try:
                    decoded = json.loads(raw)
                except ValueError:
                    decoded = None
                if isinstance(decoded, dict):
                    record.update(served_model=decoded.get("model"), usage=decoded.get("usage"))
                # aread() already decoded gzip/br/deflate. Reusing those headers
                # on a buffered Response would decode the plain JSON a second time.
                headers = {k: v for k, v in response.headers.items()
                           if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
                return httpx.Response(response.status_code, headers=headers, content=content, request=request)
            finally:
                await response.aclose()
        except Exception as exc:
            record.update(status="unknown", error_type=type(exc).__name__)
            raise
        finally:
            record["latency_seconds"] = round(time.monotonic() - start, 3)
            write_new(self.out / f"attempt-{number:04d}-receipt.json", record)

    async def aclose(self):
        await self.inner.aclose()


async def run_batch(bundle, *, config, out, max_attempts, max_cny, input_rate, output_rate, inner=None):
    payload = verify_bundle(bundle)
    if not config.configured or config.max_tokens <= 0 or not math.isfinite(config.timeout_seconds) or config.timeout_seconds <= 0:
        raise ValueError("explicit configured model and positive output/timeout limits required")
    out = Path(out)
    if out.exists():
        raise ValueError("output directory already exists; retain it and use a new batch directory")
    transport = RecordingTransport(
        inner if inner is not None else httpx.AsyncHTTPTransport(retries=0), out=out,
        max_attempts=max_attempts, max_cny=max_cny, input_rate=input_rate,
        output_rate=output_rate, secret=config.api_key,
    )
    out.mkdir(parents=True)
    write_new(out / "batch.json", bundle)
    report = {"batch_sha256": bundle["sha256"], "requested_model": config.model,
              "thinking_mode": config.thinking_mode, "max_tokens": config.max_tokens,
              "base_url": endpoint_identity(config.base_url, config.api_key),
              "timeout_seconds": config.timeout_seconds,
              "temperature": config.temperature, "max_http_attempts": max_attempts,
              "max_estimated_cny": max_cny, "input_cny_per_million": input_rate,
              "output_cny_per_million": output_rate, "results": [],
              "quality_status": "not_evaluated", "model_identity": "supplier_report_only; snapshot unproven"}
    write_new(out / "configuration.json", report)
    async with httpx.AsyncClient(transport=transport, timeout=config.timeout_seconds, follow_redirects=False) as client:
        for index, job in enumerate(payload["jobs"]):
            before, start = len(transport.attempts), time.monotonic()
            meta = {}
            row = {k: v for k, v in job.items() if k != "messages"}
            try:
                answer = await chat_completion(
                    messages=[ChatMessage(**message) for message in job["messages"]],
                    config=config, http_client=client, max_retries=1,
                    require_complete=True, response_meta=meta,
                )
                row.update(status="answered", answer=answer,
                           citations=citation_check(answer, job["source_count"]))
            except BudgetExceeded:
                row.update(status="budget_stopped")
            except Exception as exc:
                # Raw provider response is retained separately, including failures.
                row.update(status="failed", error_type=type(exc).__name__)
            row.update(response_meta=meta, latency_seconds=round(time.monotonic() - start, 3),
                       attempts=list(range(before + 1, len(transport.attempts) + 1)), semantic_status="unreviewed")
            write_new(out / f"answer-{index + 1:04d}.json", row)
            report["results"].append(row)
            if row["status"] == "budget_stopped":
                break
    report.update(http_attempts=len(transport.attempts), reserved_estimated_cny=transport.reserved_cny,
                  unattempted_jobs=len(payload["jobs"]) - len(report["results"]),
                  answered_jobs=sum(r["status"] == "answered" for r in report["results"]),
                  failed_or_stopped_jobs=sum(r["status"] != "answered" for r in report["results"]))
    write_new(out / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="offline: freeze inputs and rubrics, no model calls")
    prep.add_argument("--cases", type=Path, default=ROOT / "tests/fixtures/qa_coverage_cases.json")
    prep.add_argument("--today", required=True)
    prep.add_argument("--runs", type=int, default=3)
    prep.add_argument("--baseline-prompt", type=Path)
    prep.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("run", help="paid: execute only an explicitly authorized frozen batch")
    run.add_argument("--batch", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--base-url", required=True)
    run.add_argument("--api-key-env", default="DORAMI_EVAL_API_KEY")
    run.add_argument("--model", required=True)
    run.add_argument("--thinking-mode", choices=["", "disabled", "low", "high", "max"], required=True)
    run.add_argument("--max-tokens", type=int, required=True)
    run.add_argument("--timeout-seconds", type=int, default=240)
    run.add_argument("--max-http-attempts", type=int, required=True)
    run.add_argument("--max-estimated-cny", type=float, required=True)
    run.add_argument("--input-cny-per-million", type=float, required=True)
    run.add_argument("--output-cny-per-million", type=float, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        batch = prepare(json.loads(args.cases.read_text()), today=args.today, runs=args.runs,
                        baseline=args.baseline_prompt.read_text() if args.baseline_prompt else None)
        write_new(args.out, batch)
        print(json.dumps({"jobs": len(batch["payload"]["jobs"]), "sha256": batch["sha256"], "provider_calls": 0}))
    else:
        config = LLMConfig(base_url=args.base_url, api_key=os.environ.get(args.api_key_env, ""),
                           model=args.model, thinking_mode=args.thinking_mode,
                           max_tokens=args.max_tokens, timeout_seconds=args.timeout_seconds)
        report = asyncio.run(run_batch(json.loads(args.batch.read_text()), config=config, out=args.out,
                                       max_attempts=args.max_http_attempts, max_cny=args.max_estimated_cny,
                                       input_rate=args.input_cny_per_million, output_rate=args.output_cny_per_million))
        print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
        return 0 if all(r["status"] == "answered" for r in report["results"]) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
