#!/usr/bin/env python3
"""从哆啦美导出某日日报为 shendeng「daily-news/batch」上传 JSON（独立脚本）。

本脚本逻辑上独立于哆啦美后端，不被后端运行时调用。它从哆啦美的 API 拉取
某日日报的**结构化条目 items**（哆啦美生成日报时已存于该记录的
extensions.items 中），再做**确定性字段改名**（复刻原 Dify code 节点），
生成 shendeng 接口的 batch body JSON 文件。按需也可同时导出日报 Markdown 正文。

—— 不做 markdown 文本解析、不调用任何 LLM；转换是纯字段映射，稳定可靠。

推荐用法：直接编辑本脚本顶部的「本地默认配置」常量；凭证类常量默认留空。

  PYTHONPATH=src .venv/bin/python scripts/export_shendeng_daily_news.py

配置优先级：脚本顶部常量 < 环境变量 < 命令行参数。
若不想改脚本，也可用环境变量 DORAMI_ADMIN_USER / DORAMI_ADMIN_PASSWORD / DORAMI_FEED_TOKEN。
不要把真实凭证提交到代码仓。

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
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

DAILY_BRIEF_SOURCE_ID = "dorami_daily_brief"

# shendeng 平台现已兼容多分类，导出时原样透传日报的原始细分类
# （模型发布/行业资讯/开源动态/技术大会/社交动态/资讯聚合/学术论文）。
# 仅当条目分类为空（极端兜底）时回落到 dorami 自己的 catch-all「资讯聚合」。
FALLBACK_CLASSIFICATION = "资讯聚合"

# ========================
# 本地默认配置
# ========================
# 这个脚本可以单独拷走运行。非敏感配置可直接改这里；凭证类字段保持空字符串，
# 运行时再手动填写到私有副本，或用环境变量/命令行参数覆盖。
DEFAULT_EXPORT_CONFIG = {
    "base_url": "https://www.dorami.cloud",
    "date": "",  # 留空表示今天；也可写 "2026-06-15"
    "output": "daily-news-{date}.json",
    "markdown_output": "daily-brief-{date}.md",
    "feed_token": "",
    "username": "",
    "password": "",
}
ENV_OVERRIDES = {
    "base_url": "DORAMI_BASE_URL",
    "date": "DORAMI_DAILY_BRIEF_DATE",
    "output": "SHENDENG_EXPORT_OUTPUT",
    "markdown_output": "SHENDENG_EXPORT_MARKDOWN_OUTPUT",
    "feed_token": "DORAMI_FEED_TOKEN",
    "username": "DORAMI_ADMIN_USER",
    "password": "DORAMI_ADMIN_PASSWORD",
}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _format_output_template(value: str, date: str) -> str:
    try:
        return value.format(date=date)
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"输出路径模板格式错误: {value!r}，仅支持 {{date}} 占位符。") from exc


def resolve_export_config(args: argparse.Namespace) -> Dict[str, str]:
    cfg = dict(DEFAULT_EXPORT_CONFIG)

    for key, env_name in ENV_OVERRIDES.items():
        value = os.getenv(env_name, "").strip()
        if value:
            cfg[key] = value

    for key in DEFAULT_EXPORT_CONFIG:
        value = getattr(args, key, None)
        if value:
            cfg[key] = str(value).strip()

    cfg["date"] = cfg["date"] or _today()
    cfg["output"] = _format_output_template(cfg["output"], cfg["date"])
    if cfg.get("markdown_output"):
        cfg["markdown_output"] = _format_output_template(cfg["markdown_output"], cfg["date"])
    return cfg


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
        # 原样透传日报原始分类（shendeng 已兼容多分类）；空值兜底「资讯聚合」
        classification = (it.get("classification") or "").strip() or FALLBACK_CLASSIFICATION
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

def _extract_daily_brief_for_date(records: List[Dict[str, Any]], date: str) -> Optional[Dict[str, Any]]:
    """从一批日报记录里挑出目标日期那篇，返回 content 与 extensions.items。

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
        return {
            "items": list((ext or {}).get("items") or []),
            "content": rec.get("content") or "",
            "title": rec.get("title") or "",
            "publish_date": publish_date or date,
        }
    return None


def _extract_items_for_date(records: List[Dict[str, Any]], date: str) -> Optional[List[Dict[str, Any]]]:
    """向后兼容：从一批日报记录里挑出目标日期那篇，返回 extensions.items。"""
    brief = _extract_daily_brief_for_date(records, date)
    return None if brief is None else brief["items"]


def fetch_daily_brief_via_admin(base_url: str, username: str, password: str, date: str) -> Optional[Dict[str, Any]]:
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
        return _extract_daily_brief_for_date(records, date)


def fetch_items_via_admin(base_url: str, username: str, password: str, date: str) -> Optional[List[Dict[str, Any]]]:
    brief = fetch_daily_brief_via_admin(base_url, username, password, date)
    return None if brief is None else brief["items"]


def fetch_daily_brief_via_feed_token(base_url: str, token: str, date: str) -> Optional[Dict[str, Any]]:
    with httpx.Client(base_url=base_url, timeout=30, follow_redirects=True, trust_env=False) as client:
        resp = client.get(
            "/api/public/feed/articles",
            params={"source_ids": DAILY_BRIEF_SOURCE_ID, "publish_date_start": date, "publish_date_end": date},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            raise SystemExit(f"聚合接口拉取失败 HTTP {resp.status_code}: {resp.text[:200]}")
        return _extract_daily_brief_for_date(resp.json().get("items", []), date)


def fetch_items_via_feed_token(base_url: str, token: str, date: str) -> Optional[List[Dict[str, Any]]]:
    brief = fetch_daily_brief_via_feed_token(base_url, token, date)
    return None if brief is None else brief["items"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出某日日报为 shendeng daily-news/batch 上传 JSON。")
    parser.add_argument("--base-url", default=None, help="覆盖脚本顶部的 Dorami base_url")
    parser.add_argument("--date", default=None, help="目标日报日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--username", default=None, help="覆盖脚本顶部的管理员用户名")
    parser.add_argument("--password", default=None, help="覆盖脚本顶部的管理员密码")
    parser.add_argument("--feed-token", default=None, help="覆盖脚本顶部的个人聚合接口令牌 dfeed_…")
    parser.add_argument("-o", "--output", default=None, help="覆盖 JSON 输出路径，支持 {date}")
    parser.add_argument("--markdown-output", default=None, help="同时输出日报 Markdown 正文到指定文件，支持 {date}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = resolve_export_config(args)
    date = cfg["date"]

    if cfg["feed_token"]:
        brief = fetch_daily_brief_via_feed_token(cfg["base_url"], cfg["feed_token"], date)
    elif cfg["username"] and cfg["password"]:
        brief = fetch_daily_brief_via_admin(cfg["base_url"], cfg["username"], cfg["password"], date)
    else:
        raise SystemExit(
            "请提供鉴权：在脚本顶部配置 feed_token，或配置 username/password；"
            "也可用环境变量或命令行参数覆盖。"
        )

    items = None if brief is None else brief["items"]
    if items is None:
        raise SystemExit(f"未找到 {date} 的日报（请确认该日已生成日报）。")
    if not items:
        raise SystemExit(f"{date} 的日报没有结构化条目（items 为空），无可导出内容。")

    batch = items_to_shendeng_batch(items)
    output = cfg["output"]
    with open(output, "w", encoding="utf-8") as f:
        json.dump(batch, f, ensure_ascii=False, indent=2)

    if cfg["markdown_output"]:
        with open(cfg["markdown_output"], "w", encoding="utf-8") as f:
            f.write((brief.get("content") or "").rstrip() + "\n")

    print(f"✅ 已导出 {len(batch)} 条 → {output}")
    if cfg["markdown_output"]:
        print(f"✅ 已导出日报正文 → {cfg['markdown_output']}")
    print("   下一步：将该 JSON 作为 body，手动 POST 到 shendeng 的 daily-news/batch 接口（见脚本顶部说明）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
