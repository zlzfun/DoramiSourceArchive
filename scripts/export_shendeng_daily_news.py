#!/usr/bin/env python3
"""从哆啦美导出某日日报为 shendeng「daily-news/batch」上传 JSON（独立脚本）。

本脚本逻辑上独立于哆啦美后端，不被后端运行时调用。它从哆啦美的 API 拉取
某日日报的**结构化条目 items**（哆啦美生成日报时已存于该记录的
extensions.items 中），再做**确定性字段改名**（复刻原 Dify code 节点），
生成 shendeng 接口的 batch body JSON 文件。由你手动把该 JSON 传进内网上传。

—— 不做 markdown 文本解析、不调用任何 LLM；转换是纯字段映射，稳定可靠。

用法（二选一鉴权）：

  # A. 管理员登录（最通用，无需订阅）
  PYTHONPATH=src .venv/bin/python scripts/export_shendeng_daily_news.py \
      --base-url http://127.0.0.1:8088 \
      --username admin --password '****' \
      --date 2026-06-07 -o daily-news-2026-06-07.json

  # B. 个人聚合接口令牌（需先在阅读器订阅「哆啦美·AI资讯日报」）
  PYTHONPATH=src .venv/bin/python scripts/export_shendeng_daily_news.py \
      --base-url http://127.0.0.1:8088 \
      --feed-token dfeed_xxx --date 2026-06-07

凭据也可用环境变量：DORAMI_ADMIN_USER / DORAMI_ADMIN_PASSWORD / DORAMI_FEED_TOKEN。

生成的 JSON 即 shendeng 接口的 body，手动上传参考（值占位、按需自填）：

  POST https://shendeng.ai.huawei.com/api/manage/daily-news/batch
  Content-Type: application/json
  Authorization: <你的 token>
  X-API-Key:     <你的 key>
  Body:          本脚本输出的 JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

DAILY_BRIEF_SOURCE_ID = "dorami_daily_brief"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ==========================================
# 纯映射：结构化 items → shendeng batch（复刻 Dify code 节点；可单测）
# ==========================================

def items_to_shendeng_batch(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把日报结构化条目映射成 shendeng daily-news/batch 的 item 数组。

    items 元素来自哆啦美日报的 extensions.items（ScoredItem.to_reduce_dict），
    含 title_cn / classification / source / company / realm / summary[] /
    comment / source_url / publish_date。映射为确定性字段改名，无 LLM。
    """
    batch: List[Dict[str, Any]] = []
    for idx, it in enumerate(items, start=1):
        summary_lines = [str(s) for s in (it.get("summary") or []) if s]
        content_text = "\n".join(f"• {line}" for line in summary_lines) if summary_lines else "暂无详情"
        classification = (it.get("classification") or "").strip() or "产业资讯"
        time_val = (str(it.get("publish_date") or "")[:10]) or _today()

        entry: Dict[str, Any] = {
            "title": (it.get("title_cn") or "").strip() or "无标题",
            "classification": classification,
            "type": classification,
            "source": (it.get("source") or "").strip() or "未知来源",
            "realm": (it.get("realm") or "").strip() or "综合动态",
            "summary": "",
            "link": it.get("source_url") or "",
            "content": content_text,
            "comment": it.get("comment") or "",
            "sort": idx,
            "time": time_val,
            "status": "published",
        }
        company = (it.get("company") or "").strip()
        if company:
            entry["company"] = company
        batch.append(entry)
    return batch


# ==========================================
# 取数：从哆啦美 API 拉取某日日报的结构化 items
# ==========================================

def _extract_items_for_date(records: List[Dict[str, Any]], date: str) -> Optional[List[Dict[str, Any]]]:
    """从一批日报记录里挑出目标日期那篇，返回其 extensions.items。

    兼容两种记录形状：
    - /api/articles 的 ArticleRecord：含 publish_date + extensions_json(str)
    - /api/public/feed 的 feed item：含 metadata.publish_date + metadata.extensions(dict)
    """
    target_id = f"daily_brief_{date}"
    for rec in records:
        rec_id = rec.get("id")
        publish_date = rec.get("publish_date") or (rec.get("metadata") or {}).get("publish_date")
        if rec_id != target_id and (publish_date or "")[:10] != date:
            continue
        # extensions 来源：ArticleRecord 的 extensions_json(str) 或 feed 的 metadata.extensions(dict)
        ext: Any = (rec.get("metadata") or {}).get("extensions")
        if ext is None:
            raw = rec.get("extensions_json") or "{}"
            try:
                ext = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                ext = {}
        return list((ext or {}).get("items") or [])
    return None


def fetch_items_via_admin(base_url: str, username: str, password: str, date: str) -> Optional[List[Dict[str, Any]]]:
    with httpx.Client(base_url=base_url, timeout=30, follow_redirects=True, trust_env=False) as client:
        resp = client.post("/api/auth/login", json={"username": username, "password": password})
        if resp.status_code != 200:
            raise SystemExit(f"登录失败 HTTP {resp.status_code}: {resp.text[:200]}")
        resp = client.get(
            "/api/articles",
            params={"source_id": DAILY_BRIEF_SOURCE_ID, "publish_date_start": date, "publish_date_end": date},
        )
        if resp.status_code != 200:
            raise SystemExit(f"拉取日报失败 HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        records = data["items"] if isinstance(data, dict) and "items" in data else data
        return _extract_items_for_date(records, date)


def fetch_items_via_feed_token(base_url: str, token: str, date: str) -> Optional[List[Dict[str, Any]]]:
    with httpx.Client(base_url=base_url, timeout=30, follow_redirects=True, trust_env=False) as client:
        resp = client.get(
            "/api/public/feed/articles",
            params={"source_ids": DAILY_BRIEF_SOURCE_ID, "publish_date_start": date, "publish_date_end": date},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            raise SystemExit(f"聚合接口拉取失败 HTTP {resp.status_code}: {resp.text[:200]}")
        return _extract_items_for_date(resp.json().get("items", []), date)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出某日日报为 shendeng daily-news/batch 上传 JSON。")
    parser.add_argument("--base-url", default=os.getenv("DORAMI_BASE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--date", default=_today(), help="目标日报日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--username", default=os.getenv("DORAMI_ADMIN_USER", ""))
    parser.add_argument("--password", default=os.getenv("DORAMI_ADMIN_PASSWORD", ""))
    parser.add_argument("--feed-token", default=os.getenv("DORAMI_FEED_TOKEN", ""), help="个人聚合接口令牌 dfeed_…")
    parser.add_argument("-o", "--output", default="", help="输出文件，默认 daily-news-{date}.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    date = args.date.strip()

    if args.feed_token:
        items = fetch_items_via_feed_token(args.base_url, args.feed_token, date)
    elif args.username and args.password:
        items = fetch_items_via_admin(args.base_url, args.username, args.password, date)
    else:
        raise SystemExit("请提供鉴权：--feed-token，或 --username/--password（亦可用环境变量）。")

    if items is None:
        raise SystemExit(f"未找到 {date} 的日报（请确认该日已生成日报）。")
    if not items:
        raise SystemExit(f"{date} 的日报没有结构化条目（items 为空），无可导出内容。")

    batch = items_to_shendeng_batch(items)
    output = args.output or f"daily-news-{date}.json"
    with open(output, "w", encoding="utf-8") as f:
        json.dump(batch, f, ensure_ascii=False, indent=2)

    print(f"✅ 已导出 {len(batch)} 条 → {output}")
    print("   下一步：将该 JSON 作为 body，手动 POST 到 shendeng 的 daily-news/batch 接口（见脚本顶部说明）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
