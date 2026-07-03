"""SQLite ↔ ChromaDB 向量索引对账（阶段 2 数据层固化 · 跨存储一致性）。

两套存储靠「先写 SQLite，再写 Chroma，最后置 is_vectorized 位」的顺序调用维持一致，
无事务、无对账——任一步失败或历史遗留都会让二者漂移：DB 标记已向量化但 Chroma 里没有
chunk（丢索引）、或 Chroma 有孤儿 chunk 而文章早已删除（漏清理）。

本模块把两侧「谁被向量化了」的信念对齐并分类漂移，可只报告（dry-run）或顺带修复。
修复动作都走既有安全原语，不引入新写路径：

- flagged_but_absent（DB 说已索引，Chroma 无 chunk）→ 标记 index_status=stale
  （is_vectorized 随之为 False），使其重新进入 all-pending 队列被重新向量化。
- present_but_unflagged（Chroma 有 chunk，但文章 is_vectorized=False）→ 采纳既有 chunk，
  置 is_vectorized=True（chunk 已在，无需重算；若担心 chunk 陈旧可事后单篇重建）。
- orphan_chunks（Chroma 有 chunk，但 SQLite 已无该文章）→ 清除这些孤儿 chunk。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from sqlmodel import Session, select

from models.db import ArticleRecord

# 报告里每类漂移最多回显多少个样例 id（完整清单可能很大，修复以全量执行）。
_SAMPLE_CAP = 50


def _load_db_state(db_sink) -> tuple[Set[str], Set[str]]:
    """返回 (全部文章 id, is_vectorized=True 的文章 id)。"""
    all_ids: Set[str] = set()
    vectorized_ids: Set[str] = set()
    with Session(db_sink.engine) as session:
        for row_id, is_vec in session.exec(
            select(ArticleRecord.id, ArticleRecord.is_vectorized)
        ).all():
            all_ids.add(row_id)
            if is_vec:
                vectorized_ids.add(row_id)
    return all_ids, vectorized_ids


async def reconcile(db_sink, vector_sink, repair: bool = False) -> Dict[str, Any]:
    """比对 SQLite 与 Chroma 的向量化状态，返回漂移报告；repair=True 时顺带修复。"""
    all_ids, vectorized_ids = _load_db_state(db_sink)
    chroma_ids = await vector_sink.list_parent_ids()

    flagged_but_absent = sorted(vectorized_ids - chroma_ids)
    present_but_unflagged = sorted((chroma_ids & all_ids) - vectorized_ids)
    orphan_chunks = sorted(chroma_ids - all_ids)

    report: Dict[str, Any] = {
        "db_total": len(all_ids),
        "db_vectorized": len(vectorized_ids),
        "chroma_parents": len(chroma_ids),
        "flagged_but_absent": {
            "count": len(flagged_but_absent),
            "sample": flagged_but_absent[:_SAMPLE_CAP],
        },
        "present_but_unflagged": {
            "count": len(present_but_unflagged),
            "sample": present_but_unflagged[:_SAMPLE_CAP],
        },
        "orphan_chunks": {
            "count": len(orphan_chunks),
            "sample": orphan_chunks[:_SAMPLE_CAP],
        },
        "in_sync": not (flagged_but_absent or present_but_unflagged or orphan_chunks),
        "repaired": None,
    }

    if repair:
        repaired = {"reset_flag": 0, "adopted_flag": 0, "purged_chunks": 0}
        for article_id in flagged_but_absent:
            # 曾标已索引却无 chunk → 标 stale（is_vectorized 随之 False，all-pending 会重拾）。
            if await db_sink.set_index_status(article_id, "stale"):
                repaired["reset_flag"] += 1
        for article_id in present_but_unflagged:
            if await db_sink.mark_as_vectorized(article_id):
                repaired["adopted_flag"] += 1
        for article_id in orphan_chunks:
            if await vector_sink.delete(article_id):
                repaired["purged_chunks"] += 1
        report["repaired"] = repaired

    return report
