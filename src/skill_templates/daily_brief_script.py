#!/usr/bin/env python3
"""
哆啦美·AI资讯日报生成脚本
用法：python daily_brief.py [--date YYYY-MM-DD] [--days N] [--content-types TYPE1,TYPE2] [--with-commentary] [--lang en] [--output FILE]

依赖：pip install httpx
平台地址：{BASE_URL}
"""

import argparse
import sys
from datetime import date, timedelta
from typing import Optional

try:
    import httpx
except ImportError:
    print("缺少依赖，请先运行: pip install httpx", file=sys.stderr)
    sys.exit(1)

BASE_URL = "{BASE_URL}"

CATEGORY_MAP = {
    "arxiv": ("📄", "学术论文"),
    "tech_conference": ("🎤", "技术大会"),
    "github_release": ("🔧", "开源动态"),
    "wechat_article": ("📱", "行业资讯"),
    "rss": ("🌐", "资讯聚合"),
    "social_post": ("💬", "社交动态"),
}


def fetch_articles(
    start_date: str,
    end_date: str,
    content_types: Optional[list[str]] = None,
    limit: int = 200,
) -> list[dict]:
    params = {
        "publish_date_start": start_date,
        "publish_date_end": end_date,
        "include_content": "true",
        "limit": limit,
    }
    if content_types:
        params["content_types"] = ",".join(content_types)
    resp = httpx.get(f"{BASE_URL}/api/dify/articles", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def categorize(articles: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for a in articles:
        ct = a.get("content_type", "other")
        groups.setdefault(ct, []).append(a)
    # sort each group by publish_date desc
    for ct in groups:
        groups[ct].sort(key=lambda x: x.get("publish_date", ""), reverse=True)
    return groups


def render_article(a: dict, with_commentary: bool, lang: str) -> str:
    title = a.get("title", "（无标题）")
    url = a.get("source_url", "")
    source = a.get("source_id", "")
    pub_date = (a.get("publish_date") or "")[:10]
    content = (a.get("content") or "").strip()

    lines = []
    link = f"[{title}]({url})" if url else title
    lines.append(f"### {link}")
    lines.append(f"**来源**：{source} · {pub_date}")
    if content:
        # Use first 200 chars as summary
        summary = content[:200].replace("\n", " ").strip()
        if len(content) > 200:
            summary += "…"
        lines.append(summary)
    if with_commentary:
        if lang == "en":
            lines.append("> 💡 *Commentary: (AI commentary not available in script mode)*")
        else:
            lines.append("> 💡 *点评：（脚本模式暂不支持AI点评，请使用Skill.md配合LLM生成）*")
    return "\n".join(lines)


def render_brief(
    articles: list[dict],
    target_date: str,
    with_commentary: bool = False,
    lang: str = "zh",
) -> str:
    if not articles:
        return f"# 哆啦美 AI 资讯日报 · {target_date}\n\n> 该日期暂无收录资讯。\n"

    groups = categorize(articles)
    total = len(articles)

    lines = []
    if lang == "en":
        lines.append(f"# 🤖 Dorami AI Daily Brief · {target_date}")
        lines.append(f"\n> {total} articles collected, {len(groups)} categories\n")
    else:
        lines.append(f"# 🤖 哆啦美 AI 资讯日报 · {target_date}")
        lines.append(f"\n> 共收录 {total} 条资讯，涵盖 {len(groups)} 个分类\n")

    # Render in fixed category order, then any unknown types
    ordered_types = ["arxiv", "tech_conference", "github_release", "wechat_article", "rss", "social_post"]
    seen = set()
    for ct in ordered_types + [k for k in groups if k not in ordered_types]:
        if ct not in groups or ct in seen:
            continue
        seen.add(ct)
        items = groups[ct]
        emoji, name = CATEGORY_MAP.get(ct, ("📌", "其他资讯"))
        if lang == "en":
            section_title = f"{emoji} {ct.replace('_', ' ').title()} ({len(items)})"
        else:
            section_title = f"{emoji} {name}（{len(items)} 条）"
        lines.append(f"---\n\n## {section_title}\n")
        for a in items:
            lines.append(render_article(a, with_commentary=with_commentary, lang=lang))
            lines.append("")

    lines.append(f"\n---\n\n*由哆啦美·归档中枢生成 · {BASE_URL}*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="哆啦美·AI资讯日报生成脚本")
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--days", type=int, default=1, help="往前N天（默认1=仅今天）")
    parser.add_argument("--content-types", help="内容类型过滤，逗号分隔，如 arxiv,github_release")
    parser.add_argument("--with-commentary", action="store_true", help="开启AI点评（脚本模式为占位符）")
    parser.add_argument("--lang", default="zh", choices=["zh", "en"], help="输出语言")
    parser.add_argument("--output", help="输出文件路径，默认输出到 stdout")
    parser.add_argument("--base-url", default=BASE_URL, help=f"平台地址，默认 {BASE_URL}")
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = args.base_url.rstrip("/")

    end_dt = date.fromisoformat(args.date) if args.date else date.today()
    start_dt = end_dt - timedelta(days=args.days - 1)
    start_str, end_str = start_dt.isoformat(), end_dt.isoformat()
    target_label = end_str if args.days == 1 else f"{start_str} ~ {end_str}"

    content_types = [t.strip() for t in args.content_types.split(",")] if args.content_types else None

    print(f"正在获取 {target_label} 的资讯...", file=sys.stderr)
    try:
        articles = fetch_articles(start_str, end_str, content_types=content_types)
    except httpx.HTTPError as e:
        print(f"请求失败：{e}", file=sys.stderr)
        sys.exit(1)

    print(f"获取到 {len(articles)} 条资讯，正在生成日报...", file=sys.stderr)
    brief = render_brief(articles, target_label, with_commentary=args.with_commentary, lang=args.lang)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(brief)
        print(f"日报已保存至：{args.output}", file=sys.stderr)
    else:
        print(brief)


if __name__ == "__main__":
    main()
