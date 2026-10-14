#!/usr/bin/env python
"""内容价值黄金集门禁(issue #178 第二波):用当前文章提示词给黄金集重评,与两位评审的均值比对。

    .venv/bin/python scripts/eval_content_value_golden.py                 # dry-run:只报规模,不调用模型
    .venv/bin/python scripts/eval_content_value_golden.py --run [--runs 3] [--model deepseek-flash] [--out FILE]

读 tests/fixtures/golden_content_value.json(74 篇,14 组:论文 / 研究报道 / 真伪教程 / 一手二手观点 /
精选与稀薄汇编 / 官方与媒体资讯配对 / 版本更新 / 项目 / 领域外),调 ``analyze_article_with_llm``
(与 worker 同一函数、同一输入构造与标签闭集召回),每篇跑 ``--runs`` 次取中位数。门禁(方案 §3.6):

1. JSON 与契约成功率 100%;
2. 内容类型:规则判不出的条目上模型判对 ≥ 85%,研究报道不得判成论文;
3. 领域外条目每次都 ≤ 4.9;
4. 成对 / 成组检验(官方 > 媒体转述、真教程 > 伪教程、一手观点 > 二手、精选汇编 > 稀薄汇编)≥ 80% 成立;
5. 内容价值对金标 MAE ≤ 0.9、档位一致率 ≥ 70%;
6. 多次运行时单篇极差 p90 不高于同批调用里新闻分的极差 p90,且不超过 1.0——即内容价值不比新闻分更不稳定。
   (首轮设的 0.8 借自播客方案,那里是同一份证据重复归约;2026-10-12 改为与同批新闻分对照,见方案 §3.6。)

新闻分不回归另由 ``scripts/eval_news_value_golden.py`` 把关。会产生 LLM 调用(篇数 × 次数)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics as st
import sys
from dataclasses import replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "golden_content_value.json")

MAE_LIMIT = 0.9
BAND_AGREE_MIN = 0.70
KIND_ACCURACY_MIN = 0.85
COMPARISON_PASS_MIN = 0.80
RANGE_P90_CEILING = 1.0
DOMAIN_CAP = 4.9
GROUP_COMPARISONS = (
    ("tutorial_real", "tutorial_pseudo"),
    ("opinion_firsthand", "opinion_secondhand"),
    ("digest_curated", "digest_thin"),
)


def band(value: float) -> str:
    return "7+" if value >= 7 else "5-6.9" if value >= 5 else "<5"


def p90(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]


def majority_kind(kinds: list[str]) -> str | None:
    """多数票;票数相同按内容类型词表顺序取,保证确定性。"""
    if not kinds:
        return None
    from services.content_classification import ITEM_KINDS

    order = {kind: index for index, kind in enumerate(ITEM_KINDS)}
    return min(set(kinds), key=lambda kind: (-kinds.count(kind), order.get(kind, len(order)), kind))


def check_gates(items: list[dict], results: dict[int, dict]) -> tuple[list[str], dict]:
    """纯函数:按方案 §3.6 的门禁给出失败项与读数(测试直接覆盖)。"""

    failures: list[str] = []
    ok = [it for it in items if results.get(it["n"], {}).get("values")]
    errors = [it["n"] for it in items if results.get(it["n"], {}).get("errors")]
    if errors:
        failures.append(f"契约失败 {len(errors)} 篇: {errors}")
    med = {it["n"]: st.median(results[it["n"]]["values"]) for it in ok}

    judged = [it for it in ok if it["gold"]["kind"] and not it.get("kind_hint")]
    kind_hits = sum(results[it["n"]]["kind"] == it["gold"]["kind"] for it in judged)
    kind_acc = kind_hits / len(judged) if judged else 1.0
    if kind_acc < KIND_ACCURACY_MIN:
        failures.append(f"内容类型准确率 {kind_acc:.0%} < {KIND_ACCURACY_MIN:.0%}")
    # 硬约束逐次检查:生产只跑一次,任一次判成论文都算(codex R1-7);总体准确率仍按多数口径。
    report_as_paper = [
        it["n"] for it in ok
        if it["group"] == "research_report"
        and "paper" in (results[it["n"]].get("kinds") or [results[it["n"]]["kind"]])
    ]
    if report_as_paper:
        failures.append(f"研究报道被判成论文: {report_as_paper}")

    over_cap = [it["n"] for it in ok if it["group"] == "off_domain" and max(results[it["n"]]["values"]) > DOMAIN_CAP]
    if over_cap:
        failures.append(f"领域外超过 {DOMAIN_CAP}: {over_cap}")

    comparisons: list[tuple[str, bool]] = []
    by_id = {it["id"]: it for it in ok}
    for it in ok:
        other = by_id.get(it.get("pair_with") or "")
        if other is not None:
            comparisons.append((f"{it['n']}>{other['n']}", med[it["n"]] > med[other["n"]]))
    for high, low in GROUP_COMPARISONS:
        hi = [med[it["n"]] for it in ok if it["group"] == high]
        lo = [med[it["n"]] for it in ok if it["group"] == low]
        if hi and lo:
            comparisons.append((f"{high}>{low}", st.mean(hi) > st.mean(lo)))
    passed = sum(flag for _label, flag in comparisons)
    comparison_rate = passed / len(comparisons) if comparisons else 1.0
    if comparison_rate < COMPARISON_PASS_MIN:
        failed = [label for label, flag in comparisons if not flag]
        failures.append(f"成对检验 {passed}/{len(comparisons)} < {COMPARISON_PASS_MIN:.0%}: {failed}")

    mae = st.mean(abs(med[it["n"]] - it["gold"]["content_value"]) for it in ok) if ok else 0.0
    agree = sum(band(med[it["n"]]) == band(it["gold"]["content_value"]) for it in ok) / len(ok) if ok else 0.0
    if mae > MAE_LIMIT:
        failures.append(f"MAE {mae:.2f} > {MAE_LIMIT}")
    if agree < BAND_AGREE_MIN:
        failures.append(f"档位一致率 {agree:.0%} < {BAND_AGREE_MIN:.0%}")

    ranges = [max(results[it["n"]]["values"]) - min(results[it["n"]]["values"])
              for it in ok if len(results[it["n"]]["values"]) > 1]
    news_ranges = [max(results[it["n"]]["news"]) - min(results[it["n"]]["news"])
                   for it in ok if len(results[it["n"]].get("news") or []) > 1]
    range_p90 = p90(ranges) if ranges else 0.0
    news_range_p90 = p90(news_ranges) if news_ranges else 0.0
    if ranges and (range_p90 > RANGE_P90_CEILING + 1e-9 or (news_ranges and range_p90 > news_range_p90 + 1e-9)):
        failures.append(
            f"极差 p90 {range_p90:.2f} 超过同批新闻分 {news_range_p90:.2f} 或上限 {RANGE_P90_CEILING}"
        )

    readings = {
        "evaluated": len(ok), "errors": errors, "kind_accuracy": kind_acc, "kind_judged": len(judged),
        "comparisons": comparisons, "mae": mae, "band_agree": agree, "range_p90": range_p90,
        "news_range_p90": news_range_p90,
        "bias": st.mean(med[it["n"]] - it["gold"]["content_value"] for it in ok) if ok else 0.0,
        "median": med,
    }
    return failures, readings


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="真正调用模型(默认只做 dry-run)")
    ap.add_argument("--runs", type=int, default=3, help="每篇重复次数,取中位数")
    ap.add_argument("--model", default="", help="覆盖模型名(默认用配置的模型)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default="", help="逐篇结果 JSON 输出路径")
    ap.add_argument("--allow-regression", action="store_true", help="只报告不判定")
    args = ap.parse_args()

    items = json.load(open(FIXTURE, encoding="utf-8"))["items"]
    if args.limit:
        items = items[: args.limit]
    chars = sum(len(it["body"]) + len(it["title"]) for it in items)
    print(f"黄金集 {len(items)} 篇,正文 {chars} 字;将调用 {len(items) * args.runs} 次(另有可能的结构修复重试)")
    if not args.run:
        print("dry-run:加 --run 才会调用模型")
        return 0

    from sqlmodel import Session

    from config import settings
    from llm.article_analysis_prompt import ARTICLE_ANALYSIS_PROMPT_VERSION, CONTENT_VALUE_SCORING_VERSION
    from models.db import ArticleRecord, SourceConfigRecord
    from services.article_analysis import (
        analysis_input_from_article,
        analyze_article_with_llm,
        load_relevant_active_tags,
        validate_analysis_payload,
    )
    from services.daily_brief import resolve_llm_config
    from storage.impl.db_storage import DatabaseStorage

    storage = DatabaseStorage(settings.storage.database_url)
    with Session(storage.engine) as session:
        cfg = resolve_llm_config(session)
        if not cfg.configured:
            print("LLM 未配置", file=sys.stderr)
            return 2
        if args.model:
            cfg = replace(cfg, model=args.model, aux_model="", thinking_mode="disabled")
        prepared = []
        for it in items:
            art = ArticleRecord(
                id=it["id"], title=it["title"], content_type=it["content_type"], source_id=it["source_id"],
                source_url=it.get("source_url") or "", publish_date=it["publish_date"], fetched_date="",
                content=it["body"], has_content=True,
                extensions_json=json.dumps(it.get("extensions") or {}, ensure_ascii=False),
            )
            inp = analysis_input_from_article(art, session.get(SourceConfigRecord, it["source_id"]))
            prepared.append((it, inp, load_relevant_active_tags(session, art)))

    sem = asyncio.Semaphore(args.concurrency)
    results: dict[int, dict] = {it["n"]: {"values": [], "kinds": [], "news": [], "reasons": [], "errors": []} for it in items}

    async def one(it, inp, tags):
        async with sem:
            try:
                raw = await analyze_article_with_llm(inp, tags, cfg)
                v = validate_analysis_payload(raw, active_tags=tags, require_content_value=True, kind_hint=inp.kind_hint)
                slot = results[it["n"]]
                slot["values"].append(float(v.result.content_value))
                slot["kinds"].append(v.result.content_kind)
                slot["news"].append(float(v.result.quality_score))
                slot["reasons"].append(v.result.content_value_reason)
            except Exception as exc:  # noqa: BLE001
                results[it["n"]]["errors"].append(str(exc)[:200])

    await asyncio.gather(*(one(*p) for p in prepared for _ in range(max(1, args.runs))))
    for slot in results.values():
        slot["kind"] = majority_kind(slot["kinds"])

    failures, r = check_gates(items, results)
    print(f"prompt={ARTICLE_ANALYSIS_PROMPT_VERSION} ruler={CONTENT_VALUE_SCORING_VERSION} model={cfg.model} "
          f"runs={args.runs} 评了 {r['evaluated']}/{len(items)} 篇")
    print(f"vs 金标: MAE {r['mae']:.2f}  平均偏差 {r['bias']:+.2f}  档位一致率 {r['band_agree']:.0%}  "
          f"极差 p90 {r['range_p90']:.2f}(同批新闻分 {r['news_range_p90']:.2f})")
    print(f"内容类型(规则判不出的 {r['kind_judged']} 篇): {r['kind_accuracy']:.0%}")
    med = r["median"]
    ok = [it for it in items if it["n"] in med]
    for name in ("claude_opus", "codex_gpt6astra"):
        print(f"vs {name}: MAE {st.mean(abs(med[it['n']] - it['raters'][name]['score']) for it in ok):.2f}")
    print("\n组                    n  模型   金标  新闻分")
    for group in sorted({it["group"] for it in ok}):
        g = [it for it in ok if it["group"] == group]
        print(f"{group:<20} {len(g):>2}  {st.mean(med[it['n']] for it in g):.2f}  {st.mean(it['gold']['content_value'] for it in g):.2f}"
              f"  {st.mean(st.median(results[it['n']]['news']) for it in g):.2f}")
    print("\n成对 / 成组检验: " + ", ".join(f"{label}{'✓' if flag else '✗'}" for label, flag in r["comparisons"]))
    wrong_kind = [it for it in ok if it["gold"]["kind"] and results[it["n"]]["kind"] != it["gold"]["kind"]]
    if wrong_kind:
        print("类型不符: " + "; ".join(f"{it['n']} {it['gold']['kind']}→{results[it['n']]['kind']}" for it in wrong_kind))
    worst = sorted(ok, key=lambda it: -abs(med[it["n"]] - it["gold"]["content_value"]))[:12]
    print("\n偏差最大 12 篇: n | 组 | 模型 | 金标 | 标题 | 模型依据")
    for it in worst:
        print(f"  {it['n']:>2} | {it['group']:<18} | {med[it['n']]:>4} | {it['gold']['content_value']:>5} | "
              f"{it['title'][:36]} | {results[it['n']]['reasons'][0] if results[it['n']]['reasons'] else ''}")
    if args.out:
        json.dump({"prompt_version": ARTICLE_ANALYSIS_PROMPT_VERSION, "model": cfg.model, "runs": args.runs,
                   "results": {str(k): v for k, v in results.items()}},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if failures:
        print("\n门禁未通过:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 0 if args.allow_regression else 3
    print("\n门禁通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
