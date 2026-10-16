#!/usr/bin/env python3
"""Compare candidate-scan allocations on an isolated synthetic SQLite database.

Run from the repository venv; never opens the configured production/dev DB.
Each measured version runs in a fresh process. Not a CI timing assertion.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import hashlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc
import types


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
STAMP = "2026-10-16T00:00:00+00:00"


def measure(args):
    os.environ["DORAMI_CONFIG_FILE"] = str(Path(args.worker) / "benchmark.ini")
    # Registry startup messages should not contaminate the JSON result.
    with redirect_stdout(io.StringIO()):
        from services.podcast_premium_guides import pending_premium_guide_candidates
        from storage.impl.db_storage import DatabaseStorage

        sink = DatabaseStorage(f"sqlite:///{Path(args.worker) / 'benchmark.db'}")
        scan = pending_premium_guide_candidates
        if args.mode == "baseline":
            source = subprocess.check_output(
                ["git", "show", f"{args.baseline_ref}:src/services/podcast_premium_guides.py"],
                cwd=ROOT, text=True,
            )
            module = types.ModuleType("services._candidate_scan_baseline")
            sys.modules[module.__name__] = module
            exec(compile(source, "<candidate-scan-baseline>", "exec"), module.__dict__)
            scan = module.pending_premium_guide_candidates
    try:
        tracemalloc.start()
        started = time.perf_counter()
        candidates = scan(sink.engine, minimum_duration_seconds=1800, score_threshold=7.5)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_divisor = 2**20 if sys.platform == "darwin" else 1024
        return {
            "mode": args.mode, "candidates": len(candidates),
            "python_peak_mib": round(peak / 2**20, 3),
            "process_maxrss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rss_divisor, 3),
            "elapsed_seconds": round(elapsed, 3),
        }
    finally:
        sink.engine.dispose()


def run(args):
    with tempfile.TemporaryDirectory(prefix="dorami-candidate-benchmark-") as directory:
        folder = Path(directory)
        database = folder / "benchmark.db"
        ini = folder / "benchmark.ini"
        ini.write_text(f"[storage]\ndatabase_url = sqlite:///{database}\n")
        os.environ["DORAMI_CONFIG_FILE"] = str(ini)
        with redirect_stdout(io.StringIO()):
            from sqlmodel import Session
            from storage.impl.db_storage import DatabaseStorage
            from models.db import ArticleRecord, ArticleAnalysisRecord, PodcastTextArtifactRecord

            sink = DatabaseStorage(f"sqlite:///{database}")
        text = ("逐字稿内容" * ((args.characters + 4) // 5))[:args.characters]
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            for index in range(args.episodes):
                episode_id = f"benchmark-{index:04d}"
                with Session(sink.engine) as session:
                    session.add(ArticleRecord(
                        id=episode_id, title=episode_id, content_type="podcast_episode",
                        source_id="synthetic", source_url="https://example.test/episode",
                        content="Synthetic show notes", publish_date=STAMP, fetched_date=STAMP,
                        extensions_json='{"duration_seconds":2400}',
                    ))
                    session.commit()
                    session.add(PodcastTextArtifactRecord(
                        id=episode_id, episode_id=episode_id, kind="normalized_transcript",
                        version=1, content_hash=digest, inline_text=text, language="en",
                        authority_id="synthetic", created_at=STAMP,
                    ))
                    session.commit()
                    session.add(ArticleAnalysisRecord(
                        article_id=episode_id, status="succeeded", quality_score=9,
                        podcast_final_score=9, analysis_basis="asr_transcript",
                        transcript_artifact_id=episode_id, created_at=STAMP, updated_at=STAMP,
                    ))
                    session.commit()
        finally:
            sink.engine.dispose()
        results = []
        for mode in ("baseline", "fixed"):
            output = subprocess.check_output([
                sys.executable, str(Path(__file__).resolve()), "--worker", directory,
                "--mode", mode, "--baseline-ref", args.baseline_ref,
            ], cwd=ROOT, text=True)
            results.append(json.loads(output))
        if any(result["candidates"] != args.episodes for result in results):
            raise RuntimeError("candidate counts differ from the seeded episodes")
        print(json.dumps({
            "sqlite_version": sqlite3.sqlite_version, "episodes": args.episodes,
            "characters_per_transcript": args.characters,
            "utf8_mib": round(len(text.encode("utf-8")) * args.episodes / 2**20, 3),
            "results": results,
            "note": "Synthetic allocation comparison; timing is not a production I/O prediction.",
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="742326f")
    parser.add_argument("--episodes", type=int, default=96)
    parser.add_argument("--characters", type=int, default=1_000_000)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("baseline", "fixed"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.episodes <= 0 or args.characters <= 0:
        parser.error("episodes and characters must be positive")
    if args.worker:
        print(json.dumps(measure(args)))
    else:
        run(args)
