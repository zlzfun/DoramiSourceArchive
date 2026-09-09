#!/usr/bin/env python
"""新闻价值评分黄金集门禁(issue #33):用当前提示词给 62 篇黄金集重评,与两位评审的均值比对。

    .venv/bin/python scripts/eval_news_value_golden.py [--model deepseek-v4-flash] [--limit N] [--out FILE]

读 tests/fixtures/golden_news_value.json(生产文章 + Claude/codex 独立打分 + v5 基线),
调 `analyze_article_with_llm`(与 worker/日报同一函数、同一标签闭集召回),打印:
MAE(vs 黄金均值 / 各评审)、档位一致率、按层均分、与基线 v5 的逐篇变化。
提示词改动前后各跑一次,MAE 与档位一致率不得劣化。会产生 LLM 调用(62 次)。
"""
from __future__ import annotations

import argparse, asyncio, json, os, statistics as st, sys
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "golden_news_value.json")


def band(s: float) -> str:
    return "9+" if s >= 9 else "7-8.9" if s >= 7 else "5-6.9" if s >= 5 else "<5"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="", help="覆盖模型名(默认用配置的模型;生产对齐用 deepseek-v4-flash)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default="", help="逐篇结果 JSON 输出路径")
    ap.add_argument("--allow-regression", action="store_true",
                    help="只报告不判定(默认:任一篇评分失败、或 MAE/档位一致率劣于夹具里的 v5 基线即非零退出)")
    args = ap.parse_args()

    from sqlmodel import Session
    from storage.impl.db_storage import DatabaseStorage
    from config import settings
    from models.db import ArticleRecord, SourceConfigRecord
    from services.daily_brief import resolve_llm_config
    from services.article_analysis import (
        analyze_article_with_llm, analysis_input_from_article, load_relevant_active_tags, validate_analysis_payload,
    )
    from llm.article_analysis_prompt import ARTICLE_ANALYSIS_PROMPT_VERSION

    items = json.load(open(FIXTURE, encoding="utf-8"))["items"]
    if args.limit:
        items = items[: args.limit]
    storage = DatabaseStorage(settings.storage.database_url)
    with Session(storage.engine) as session:
        cfg = resolve_llm_config(session)
        if not cfg.configured:
            print("LLM 未配置", file=sys.stderr); return 2
        if args.model:
            cfg = replace(cfg, model=args.model, aux_model="", thinking_mode="disabled")
        prepared = []
        for it in items:
            art = ArticleRecord(id=it["id"], title=it["title"], content_type=it["content_type"], source_id=it["source_id"],
                                source_url="", publish_date=it["publish_date"], fetched_date="", content=it["body"], extensions_json="{}")
            prepared.append((it, analysis_input_from_article(art, session.get(SourceConfigRecord, it["source_id"])),
                             load_relevant_active_tags(session, art)))
    sem = asyncio.Semaphore(args.concurrency)
    results: dict[int, dict] = {}

    async def one(it, inp, tags):
        async with sem:
            try:
                v = validate_analysis_payload(await analyze_article_with_llm(inp, tags, cfg), active_tags=tags)
                results[it["n"]] = {"score": float(v.result.quality_score), "reason": v.result.score_reason}
            except Exception as exc:  # noqa: BLE001
                results[it["n"]] = {"error": str(exc)[:200]}

    await asyncio.gather(*(one(*p) for p in prepared))
    ok = [it for it in items if "score" in results.get(it["n"], {})]
    print(f"prompt={ARTICLE_ANALYSIS_PROMPT_VERSION} model={cfg.model} 评了 {len(ok)}/{len(items)} 篇")
    if not ok:
        return 1
    def mae(pairs):
        pairs = list(pairs)
        return sum(abs(a - b) for a, b in pairs) / len(pairs)
    cur = [results[it["n"]]["score"] for it in ok]
    gold = [it["gold"] for it in ok]
    print(f"vs 黄金均值: MAE {mae(zip(cur, gold)):.2f}  平均偏差 {st.mean(a-b for a,b in zip(cur,gold)):+.2f}  "
          f"档位一致率 {sum(band(a)==band(b) for a,b in zip(cur,gold))/len(ok):.0%}")
    for name in ("claude", "codex_gpt56sol"):
        r = [it["raters"][name]["score"] for it in ok]
        print(f"vs {name}: MAE {mae(zip(cur, r)):.2f}  档位一致率 {sum(band(a)==band(b) for a,b in zip(cur,r))/len(ok):.0%}")
    base = [(results[it["n"]]["score"], it["baseline"]["score"]) for it in ok if it["baseline"].get("score") is not None]
    if base:
        print(f"vs 基线 v5: MAE {mae(base):.2f}  平均变化 {st.mean(a-b for a,b in base):+.2f}")
    print("\n层        n  当前   黄金   Claude codex  基线v5")
    for s in ("official", "media", "paper", "board", "community"):
        g = [it for it in ok if it["stratum"] == s]
        if not g: continue
        m = lambda f: st.mean(f(it) for it in g)
        b = [it["baseline"]["score"] for it in g if it["baseline"].get("score") is not None]
        print(f"{s:<9} {len(g):>2}  {m(lambda it: results[it['n']]['score']):.2f}   {m(lambda it: it['gold']):.2f}   "
              f"{m(lambda it: it['raters']['claude']['score']):.2f}   {m(lambda it: it['raters']['codex_gpt56sol']['score']):.2f}   "
              f"{st.mean(b) if b else float('nan'):.2f}")
    worst = sorted(ok, key=lambda it: -abs(results[it["n"]]["score"] - it["gold"]))[:10]
    print("\n偏差最大 10 篇: n | 层 | 当前 | 黄金 | 标题")
    for it in worst:
        print(f"  {it['n']:>2} | {it['stratum']:<9} | {results[it['n']]['score']:>4} | {it['gold']:>5} | {it['title'][:48]}")
    if args.out:
        json.dump({"prompt_version": ARTICLE_ANALYSIS_PROMPT_VERSION, "model": cfg.model,
                   "results": {str(k): v for k, v in results.items()}}, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if args.allow_regression:
        return 0
    # 门禁判定:评分失败即失败(提示词产出畸形输出不能靠「至少评了一篇」混过);
    # 与黄金均值的 MAE 与档位一致率不得劣于夹具自带的 v5 基线(同一 ok 子集上比较)。
    failed = [it["n"] for it in items if "score" not in results.get(it["n"], {})]
    if failed:
        print(f"门禁失败:{len(failed)} 篇评分失败 {failed}", file=sys.stderr)
        return 3
    with_base = [it for it in ok if it["baseline"].get("score") is not None]
    if with_base:
        cur_mae = mae((results[it["n"]]["score"], it["gold"]) for it in with_base)
        base_mae = mae((it["baseline"]["score"], it["gold"]) for it in with_base)
        cur_agree = sum(band(results[it["n"]]["score"]) == band(it["gold"]) for it in with_base) / len(with_base)
        base_agree = sum(band(it["baseline"]["score"]) == band(it["gold"]) for it in with_base) / len(with_base)
        if cur_mae > base_mae + 1e-9 or cur_agree < base_agree - 1e-9:
            print(f"门禁失败:MAE {cur_mae:.2f}(基线 {base_mae:.2f})/ 档位一致率 {cur_agree:.0%}(基线 {base_agree:.0%})劣于 v5 基线",
                  file=sys.stderr)
            return 4
        print(f"门禁通过:MAE {cur_mae:.2f} ≤ 基线 {base_mae:.2f},档位一致率 {cur_agree:.0%} ≥ 基线 {base_agree:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
