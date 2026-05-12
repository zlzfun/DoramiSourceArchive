#!/usr/bin/env python3
"""
RAG 评估脚本

用法:
  python evaluate.py                            # 使用默认测试集 testset_v1.json
  python evaluate.py --testset testset_v2.json  # 指定测试集文件
  python evaluate.py --top-k 5                  # 覆盖所有用例的 top_k
  python evaluate.py --tag-filter T6            # 仅运行包含指定 requires 标签的用例
  python evaluate.py --dry-run                  # 仅打印用例清单，不执行检索

前置条件:
  - ChromaDB 中需要有已向量化的文章（运行 POST /api/vector/reindex-all 或逐条向量化）
  - 若 ChromaDB 为空，所有用例结果均为 0，可作为"未向量化基线"留存

输出:
  - 终端打印按类别汇总的评估报告
  - 结果自动保存至 tests/rag/results/eval_<timestamp>.json
"""

import sys
import os
import json
import asyncio
import argparse
import datetime
from typing import Optional

# ── 路径初始化 ────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")

sys.path.insert(0, SRC_DIR)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# ── 模块导入（延迟到 path 设置后）────────────────────────────────────────────
from storage.impl.vector_storage import ChromaVectorStorage
from storage.impl.db_storage import DatabaseStorage


# ── 评估核心逻辑 ──────────────────────────────────────────────────────────────

async def run_case(case: dict, vector: ChromaVectorStorage, db: DatabaseStorage,
                   top_k_override: Optional[int] = None,
                   use_rerank: bool = False) -> dict:
    """执行单条测试用例并返回指标。"""
    top_k = top_k_override or case["top_k"]
    primary = set(case["primary_relevant_ids"])

    # 向量检索（过量拉取以支持去重）
    raw = await vector.search(case["query"], n_results=top_k * 4)

    # 按 parent_id 去重，保留相同父文章中 distance 最小的 chunk
    best_by_parent: dict[str, dict] = {}
    for r in raw:
        pid = r["metadata"].get("parent_id", r["id"])
        if pid not in best_by_parent or r["distance"] < best_by_parent[pid]["distance"]:
            best_by_parent[pid] = r

    candidates = list(best_by_parent.values())

    # T12: cross-encoder 重排序（可选）
    if use_rerank:
        candidates = vector.rerank(case["query"], candidates[:top_k * 2])
        deduplicated = candidates[:top_k]
    else:
        deduplicated = sorted(candidates, key=lambda x: x["distance"])[:top_k]

    retrieved_ids = [r["metadata"].get("parent_id", r["id"]) for r in deduplicated]
    distances = [round(r["distance"], 4) for r in deduplicated]

    # 回查 DB 获取标题
    retrieved_titles = []
    for rid in retrieved_ids:
        record = await db.get(rid)
        retrieved_titles.append(record.title if record else f"[未知: {rid}]")

    # ── 计算指标 ──
    hits_in_top_k = [rid for rid in retrieved_ids if rid in primary]
    hit_at_k = 1 if hits_in_top_k else 0

    # MRR: 第一个命中的位置
    mrr = 0.0
    first_hit_rank = None
    first_hit_id = None
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in primary:
            mrr = 1.0 / rank
            first_hit_rank = rank
            first_hit_id = rid
            break

    precision_at_k = len(hits_in_top_k) / top_k
    recall_at_k = len(hits_in_top_k) / len(primary) if primary else 0.0

    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "top_k": top_k,
        "difficulty": case.get("difficulty", "unknown"),
        "requires": case.get("requires", []),
        "retrieved_ids": retrieved_ids,
        "retrieved_titles": retrieved_titles,
        "distances": distances,
        "hit_at_k": hit_at_k,
        "mrr": round(mrr, 4),
        "precision_at_k": round(precision_at_k, 4),
        "recall_at_k": round(recall_at_k, 4),
        "first_hit_rank": first_hit_rank,
        "first_hit_id": first_hit_id,
        "primary_relevant_ids": list(primary),
    }


def aggregate_metrics(case_results: list[dict]) -> dict:
    """按全局及分类别汇总指标。"""
    def avg(vals): return round(sum(vals) / len(vals), 4) if vals else 0.0

    overall = {
        "total": len(case_results),
        "hit_rate": avg([r["hit_at_k"] for r in case_results]),
        "mrr": avg([r["mrr"] for r in case_results]),
        "precision_at_k": avg([r["precision_at_k"] for r in case_results]),
        "recall_at_k": avg([r["recall_at_k"] for r in case_results]),
    }

    by_category: dict[str, dict] = {}
    categories = sorted(set(r["category"] for r in case_results))
    for cat in categories:
        cat_results = [r for r in case_results if r["category"] == cat]
        by_category[cat] = {
            "count": len(cat_results),
            "hit_rate": avg([r["hit_at_k"] for r in cat_results]),
            "mrr": avg([r["mrr"] for r in cat_results]),
            "precision_at_k": avg([r["precision_at_k"] for r in cat_results]),
            "recall_at_k": avg([r["recall_at_k"] for r in cat_results]),
        }

    by_difficulty: dict[str, dict] = {}
    difficulties = sorted(set(r["difficulty"] for r in case_results))
    for diff in difficulties:
        diff_results = [r for r in case_results if r["difficulty"] == diff]
        by_difficulty[diff] = {
            "count": len(diff_results),
            "hit_rate": avg([r["hit_at_k"] for r in diff_results]),
            "mrr": avg([r["mrr"] for r in diff_results]),
        }

    return {"overall": overall, "by_category": by_category, "by_difficulty": by_difficulty}


def print_report(case_results: list[dict], metrics: dict, model_name: str):
    """终端输出人类可读报告。"""
    CATEGORY_NAMES = {
        "A": "按来源/组织检索",
        "B": "按产品/工具检索",
        "C": "技术话题语义检索",
        "D": "元数据/空正文检索",
        "E": "时效性查询",
        "F": "跨语言深层语义",
    }
    SEP = "─" * 70

    print(f"\n{'═'*70}")
    print(f"  RAG 评估报告")
    print(f"  模型: {model_name}")
    print(f"  时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*70}")

    # 按类别打印每个用例
    current_cat = None
    for r in sorted(case_results, key=lambda x: x["id"]):
        if r["category"] != current_cat:
            current_cat = r["category"]
            print(f"\n[{current_cat}] {CATEGORY_NAMES.get(current_cat, current_cat)}")
            print(SEP)

        hit_symbol = "✅" if r["hit_at_k"] else "❌"
        diff_badge = {"easy": "易", "medium": "中", "hard": "难", "very_hard": "极难"}.get(r["difficulty"], "?")
        print(f"  {r['id']} [{diff_badge}] {hit_symbol}  Hit={r['hit_at_k']}  MRR={r['mrr']:.2f}  P@k={r['precision_at_k']:.2f}  R@k={r['recall_at_k']:.2f}")
        print(f"    Q: {r['query']}")
        if r["retrieved_titles"]:
            for i, (title, dist) in enumerate(zip(r["retrieved_titles"][:3], r["distances"][:3]), 1):
                marker = "★" if r["retrieved_ids"][i-1] in r["primary_relevant_ids"] else " "
                print(f"    {marker} #{i} [{dist:.3f}] {title[:60]}")
        else:
            print("    (无结果 — ChromaDB 为空或该模型下无向量)")
        print()

    # 总体指标
    ov = metrics["overall"]
    print(f"\n{'═'*70}")
    print(f"  总体指标  (n={ov['total']})")
    print(f"  Hit Rate : {ov['hit_rate']:.3f}   命中率 (top-k 内有任意一条相关文档)")
    print(f"  MRR      : {ov['mrr']:.3f}   平均倒数排名")
    print(f"  P@k      : {ov['precision_at_k']:.3f}   平均精确率")
    print(f"  R@k      : {ov['recall_at_k']:.3f}   平均召回率")

    print(f"\n  按类别:")
    for cat, m in metrics["by_category"].items():
        print(f"  [{cat}] {CATEGORY_NAMES.get(cat,'?'):<16}  n={m['count']}  Hit={m['hit_rate']:.2f}  MRR={m['mrr']:.2f}")

    print(f"\n  按难度:")
    for diff, m in metrics["by_difficulty"].items():
        label = {"easy": "易", "medium": "中", "hard": "难", "very_hard": "极难"}.get(diff, diff)
        print(f"  [{label}]  n={m['count']}  Hit={m['hit_rate']:.2f}  MRR={m['mrr']:.2f}")

    print(f"{'═'*70}\n")


def save_results(case_results: list[dict], metrics: dict, testset_meta: dict,
                 model_name: str, collection_name: str, total_vectors: int):
    """保存评估结果到 results/ 目录。"""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_id = f"eval_{ts}"
    path = os.path.join(RESULTS_DIR, f"{eval_id}.json")

    output = {
        "eval_id": eval_id,
        "timestamp": datetime.datetime.now().isoformat(),
        "testset_version": testset_meta.get("version", "unknown"),
        "model": model_name,
        "collection_name": collection_name,
        "total_vectors_in_db": total_vectors,
        "metrics": metrics,
        "cases": case_results,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  💾 结果已保存: {os.path.relpath(path, PROJECT_ROOT)}")
    return path


# ── 主入口 ────────────────────────────────────────────────────────────────────

async def main(args):
    testset_path = os.path.join(SCRIPT_DIR, args.testset)
    with open(testset_path, encoding="utf-8") as f:
        testset = json.load(f)

    cases = testset["cases"]

    # 标签过滤
    if args.tag_filter:
        cases = [c for c in cases if args.tag_filter in c.get("requires", [])]
        print(f"  已筛选 requires={args.tag_filter} 的用例，共 {len(cases)} 条")

    if args.dry_run:
        print(f"\n{'─'*60}")
        print(f"  测试集: {args.testset}  共 {len(testset['cases'])} 条（过滤后 {len(cases)} 条）")
        for c in cases:
            print(f"  [{c['id']}] [{c.get('difficulty','')}] {c['query']}")
        print(f"{'─'*60}\n")
        return

    print(f"\n⏳ 正在加载 Embedding 模型（首次运行可能需要下载）...")
    model_name = os.environ.get("LOCAL_MODEL_PATH", "BAAI/bge-m3")
    if args.rerank:
        reranker_name = os.environ.get("RERANKER_MODEL_PATH", "BAAI/bge-reranker-v2-m3")
        model_name = f"{model_name} + {reranker_name}"
    collection_name = "dorami_docs"

    vector = ChromaVectorStorage(
        db_path=os.path.join(DATA_DIR, "chroma_db"),
        collection_name=collection_name,
    )
    db = DatabaseStorage(db_url=f"sqlite:///{os.path.join(DATA_DIR, 'cms_data.db')}")

    total_vectors = await vector.count()
    print(f"  ChromaDB chunk 数: {total_vectors}")
    if total_vectors == 0:
        print("  ⚠️  警告: 向量库为空，所有评估结果将为 0（可作为空基线记录）")

    print(f"  开始评估 {len(cases)} 条测试用例...\n")
    case_results = []
    for i, case in enumerate(cases, 1):
        print(f"  [{i}/{len(cases)}] {case['id']}: {case['query'][:40]}...")
        result = await run_case(case, vector, db, args.top_k, use_rerank=args.rerank)
        case_results.append(result)

    metrics = aggregate_metrics(case_results)
    print_report(case_results, metrics, model_name)
    save_results(case_results, metrics, testset, model_name, collection_name, total_vectors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 评估脚本")
    parser.add_argument("--testset", default="testset_v1.json", help="测试集文件名（相对于 tests/rag/）")
    parser.add_argument("--top-k", type=int, default=None, help="覆盖所有用例的 top_k 值")
    parser.add_argument("--tag-filter", type=str, default=None, help="只运行含指定 requires 标签的用例（如 T6）")
    parser.add_argument("--dry-run", action="store_true", help="仅列出用例，不执行检索")
    parser.add_argument("--rerank", action="store_true", help="启用 cross-encoder 重排序（T12）")
    args = parser.parse_args()
    asyncio.run(main(args))
