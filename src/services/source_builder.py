"""高级目标：URL → 智能分析 → LLM 生成抓取节点配置 → 试抓验证（src/services/source_builder.py）。

输入一个带文章列表的 base URL，本服务：
1. 判页面类型（rss / web / json）；
2. 收集 HTML 结构信号（标题/站点名/候选文章链接/URL 模式候选/条目样例 HTML）；
3. 产出启发式基线配置（LLM 不可用时即可用）；
4. LLM 就绪时精修配置 + 分析一篇样例文章详情页推断正文 Profile；
5. preview_config 用执行端 fetcher（generic_web / generic_rss）试抓样例条目做验证（不落库）。

固化沿用现有 ``POST /api/source-configs``。LLM / crawl4ai 均为可选：缺失时降级启发式 / legacy，绝不抛 500。
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

import config
from llm import client as llm_client
from llm.client import ChatMessage, LLMError, UsageMeta
from llm.prompts import (
    DETAIL_PROFILE_SYSTEM_PROMPT,
    SOURCE_CONFIG_SYSTEM_PROMPT,
    build_detail_profile_user_prompt,
    build_source_config_user_prompt,
)
from services.media_store import SSRFError, ensure_public_host

logger = logging.getLogger("dorami.source_builder")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_NAV_TITLES = {
    "read more", "learn more", "blog", "news", "home", "about", "contact",
    "pricing", "login", "sign in", "sign up", "更多", "首页", "登录",
}


# ============ 抓取 ============

async def _fetch(url: str, *, timeout: int = 20) -> Tuple[str, str, str, Optional[int]]:
    """GET 一个 URL，返回 (text, final_url, content_type, status)。失败时 text 为空。

    请求前先做 SSRF 判定（用户可任意指定 URL）：指向本机/内网时抛 SSRFError，
    由上层转 400——不并入下方 httpx.HTTPError 的静默降级分支，避免内网探测被当「抓取失败」。
    """
    await ensure_public_host(urlparse(url).hostname or "")
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": _UA}, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            ct = resp.headers.get("content-type", "")
            return resp.text, str(resp.url), ct, resp.status_code
    except httpx.HTTPError as e:
        logger.warning("source_builder 抓取失败 [%s]: %s", url, e)
        return "", url, "", None


# ============ 页面类型 ============

def detect_page_type(text: str, content_type: str) -> str:
    """判定页面类型：rss / json / web。"""
    ct = (content_type or "").lower()
    head = (text or "").lstrip()[:600].lower()
    if "json" in ct and "html" not in ct:
        return "json"
    if any(k in ct for k in ("xml", "rss", "atom")) or head.startswith("<?xml") or "<rss" in head or "<feed" in head:
        return "rss"
    return "web"


def find_rss_autodiscovery(soup: BeautifulSoup, base_url: str) -> str:
    """从 HTML 的 <link rel=alternate type=application/rss+xml/atom+xml> 提取 feed 地址。"""
    for link in soup.find_all("link", rel=True):
        rels = link.get("rel") or []
        rels = rels if isinstance(rels, list) else [rels]
        if "alternate" not in [str(r).lower() for r in rels]:
            continue
        ltype = str(link.get("type") or "").lower()
        href = link.get("href")
        if href and ("rss" in ltype or "atom" in ltype):
            return urljoin(base_url, str(href))
    return ""


# ============ 信号收集 ============

def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _registrable(host: str) -> str:
    """取可注册域近似（最后两段），用于判定站内链接。"""
    parts = (host or "").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _candidate_container(link: Tag) -> Tag:
    cur: Tag = link
    for _ in range(4):
        parent = cur.parent
        if not isinstance(parent, Tag):
            return cur
        cur = parent
        if cur.name in {"article", "li", "section"} or cur.find(["h1", "h2", "h3", "h4"]):
            return cur
    return cur


def _link_title(link: Tag) -> str:
    container = _candidate_container(link)
    for h in container.find_all(["h1", "h2", "h3", "h4"], limit=2):
        t = _clean(h.get_text(" ", strip=True))
        if t and t.lower() not in _NAV_TITLES:
            return t
    t = _clean(link.get_text(" ", strip=True))
    return t if t and t.lower() not in _NAV_TITLES else ""


def _looks_article_path(path: str) -> bool:
    last = path.rstrip("/").split("/")[-1]
    return bool(re.search(r"\d{4}", path)) or "-" in last or len(last) >= 10


def derive_article_patterns(links: List[str], base_host: str) -> List[str]:
    """从候选链接归纳「文章详情页 URL 子串」候选，按命中文章型链接的次数排序。"""
    seg_counter: Counter = Counter()
    year_hits = 0
    for url in links:
        p = urlparse(url)
        if _registrable(p.hostname or "") != _registrable(base_host):
            continue
        segs = [s for s in p.path.split("/") if s]
        if not segs:
            continue
        if re.search(r"/20\d{2}/", p.path):
            year_hits += 1
        if _looks_article_path(p.path):
            seg_counter[f"/{segs[0]}/"] += 1
    patterns = [seg for seg, n in seg_counter.most_common(3) if n >= 2]
    if year_hits >= 3:
        patterns.append(f"{_registrable(base_host)}/20")
    # 兜底：取最常见首段
    if not patterns and seg_counter:
        patterns = [seg_counter.most_common(1)[0][0]]
    return patterns


def collect_html_signals(url: str, text: str) -> Dict[str, Any]:
    soup = BeautifulSoup(text, "html.parser")
    parsed = urlparse(url)
    host = parsed.hostname or ""

    page_title = _clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    site_name = ""
    desc = ""
    for meta in soup.find_all("meta"):
        prop = str(meta.get("property") or meta.get("name") or "").lower()
        content = str(meta.get("content") or "")
        if prop == "og:site_name" and not site_name:
            site_name = _clean(content)
        elif prop in {"description", "og:description"} and not desc:
            desc = _clean(content)
    lang = str((soup.find("html") or {}).get("lang", "")) if soup.find("html") else ""

    seen = set()
    links: List[Dict[str, str]] = []
    all_urls: List[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        abs_url = urljoin(url, href)
        p = urlparse(abs_url)
        if p.scheme not in {"http", "https"}:
            continue
        if _registrable(p.hostname or "") != _registrable(host):
            continue
        norm = p._replace(query="", fragment="").geturl()
        all_urls.append(norm)
        if norm in seen:
            continue
        seen.add(norm)
        title = _link_title(a)
        if title:
            links.append({"url": norm, "title": title})

    pattern_candidates = derive_article_patterns(all_urls, host)

    sample_item_html = ""
    if links:
        first = soup.find("a", href=lambda h: h and links[0]["url"].endswith(urlparse(urljoin(url, h)).path.rstrip("/")))
        if isinstance(first, Tag):
            sample_item_html = str(_candidate_container(first))[:1500]

    return {
        "url": url,
        "domain": host,
        "page_title": page_title,
        "site_name": site_name,
        "description": desc,
        "lang": lang,
        "sample_links": links[:30],
        "pattern_candidates": pattern_candidates,
        "sample_item_html": sample_item_html,
    }


# ============ source_id slug ============

def slug_source_id(url: str, existing: Optional[set] = None, *, prefix: str = "web") -> str:
    existing = existing or set()
    p = urlparse(url)
    host = (p.hostname or "site").replace("www.", "")
    host_slug = re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")
    seg = ""
    parts = [s for s in p.path.split("/") if s]
    if parts:
        seg = "_" + re.sub(r"[^a-z0-9]+", "_", parts[0].lower()).strip("_")
    base = f"{prefix}_{host_slug}{seg}".strip("_")[:60]
    candidate = base
    i = 2
    while candidate in existing:
        candidate = f"{base}_{i}"
        i += 1
    return candidate


# ============ 启发式基线配置 ============

def heuristic_config(url: str, signals: Dict[str, Any], *, source_id: str) -> Dict[str, Any]:
    patterns = signals.get("pattern_candidates") or []
    name = signals.get("page_title") or signals.get("site_name") or signals.get("domain") or url
    return {
        "source_id": source_id,
        "name": _clean(name)[:120],
        "source_type": "web",
        "url": url,
        "category": "official_web",
        "description": _clean(signals.get("description") or "")[:300],
        "source_owner": "",
        "source_brand": "",
        "source_scope": "",
        "source_channel": "",
        "provenance_tier": "",
        "content_tags": [],
        "signal_strength": "",
        "noise_risk": "",
        "fetch_reliability": "",
        "params": {
            "site_name": signals.get("site_name") or signals.get("page_title") or "",
            "article_url_patterns": ",".join(patterns),
            "exclude_url_patterns": "",
            "limit": 12,
            "fetch_detail": True,
            "detail_max_chars": 8000,
            "detail_use_browser": False,
            "target_elements": "",
            "excluded_selector": "",
            "wait_for": "",
        },
    }


# ============ LLM 精修 ============

def _csv(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()


async def propose_config_via_llm(signals: Dict[str, Any], llm_config, usage_meta=None) -> Dict[str, Any]:
    content = await llm_client.chat_completion(
        messages=[
            ChatMessage(role="system", content=SOURCE_CONFIG_SYSTEM_PROMPT),
            ChatMessage(role="user", content=build_source_config_user_prompt(signals)),
        ],
        config=llm_config,
        response_json=True,
        usage_meta=usage_meta,
    )
    return llm_client.parse_json_object(content)


def merge_llm_config(base: Dict[str, Any], llm: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 字段非空时覆盖启发式基线；source_id/url/source_type 始终保留基线。"""
    out = {**base}
    for key in ("name", "category", "description", "source_owner", "source_brand",
                "source_scope", "source_channel", "provenance_tier",
                "signal_strength", "noise_risk"):
        val = llm.get(key)
        if val:
            out[key] = str(val).strip()
    tags = llm.get("content_tags")
    if isinstance(tags, list) and tags:
        out["content_tags"] = [str(t).strip() for t in tags if str(t).strip()]

    params = {**out["params"]}
    if llm.get("article_url_patterns"):
        params["article_url_patterns"] = _csv(llm["article_url_patterns"])
    if llm.get("exclude_url_patterns"):
        params["exclude_url_patterns"] = _csv(llm["exclude_url_patterns"])
    listing_css = llm.get("listing_css")
    if isinstance(listing_css, dict) and listing_css:
        import json as _json
        params["listing_css"] = _json.dumps(listing_css, ensure_ascii=False)
    out["params"] = params
    return out


# ============ 详情 Profile 智能分析 ============

async def _sample_article_html(article_url: str) -> str:
    """取一篇样例文章页 HTML：crawl4ai 可用则渲染（拿 JS 后 DOM），否则 httpx。"""
    try:
        from fetchers.web_content.crawl4ai_backend import Crawl4AIContentBackend

        if Crawl4AIContentBackend.is_available():
            backend = Crawl4AIContentBackend()
            try:
                await backend.__aenter__()
                if getattr(backend, "available", False):
                    html = await backend.render_html(article_url)
                    if html:
                        return html
            finally:
                await backend.__aexit__(None, None, None)
    except Exception as e:  # noqa: BLE001
        logger.warning("crawl4ai 样例渲染失败，回退 httpx: %s", e)
    text, _, _, _ = await _fetch(article_url)
    return text


async def propose_detail_profile(article_url: str, llm_config, usage_meta=None) -> Dict[str, Any]:
    html = await _sample_article_html(article_url)
    if not html:
        return {}
    content = await llm_client.chat_completion(
        messages=[
            ChatMessage(role="system", content=DETAIL_PROFILE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=build_detail_profile_user_prompt(html)),
        ],
        config=llm_config,
        response_json=True,
        usage_meta=usage_meta,
    )
    return llm_client.parse_json_object(content)


def apply_detail_profile(cfg: Dict[str, Any], profile: Dict[str, Any]) -> None:
    params = cfg["params"]
    if profile.get("target_elements"):
        params["target_elements"] = _csv(profile["target_elements"])
        params["detail_use_browser"] = True
    if profile.get("excluded_selector"):
        params["excluded_selector"] = _csv(profile["excluded_selector"])
    if profile.get("wait_for"):
        params["wait_for"] = str(profile["wait_for"]).strip()
    if isinstance(profile.get("use_browser"), bool):
        params["detail_use_browser"] = profile["use_browser"] or params.get("detail_use_browser", False)


# ============ 主入口：分析 ============

async def analyze_url(
    url: str, *, session, existing_ids: Optional[set] = None, usage_username: Optional[str] = None
) -> Dict[str, Any]:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL 需以 http(s):// 开头"}

    existing_ids = existing_ids or set()
    warnings: List[str] = []
    text, final_url, content_type, status = await _fetch(url)
    if not text:
        return {"ok": False, "error": f"无法抓取该 URL（status={status}）"}

    page_type = detect_page_type(text, content_type)

    # RSS：直接产出 rss 配置
    if page_type == "rss":
        sid = slug_source_id(final_url, existing_ids, prefix="rss")
        # feed 是 XML，用正则取 <title> 避免用 HTML 解析器解析 XML 的告警
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        title = _clean(re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1))) if m else ""
        cfg = {
            "source_id": sid, "name": title or urlparse(final_url).hostname or "RSS 源",
            "source_type": "rss", "url": final_url, "category": "official",
            "description": "", "source_owner": "", "source_brand": "", "source_scope": "",
            "source_channel": "blog", "provenance_tier": "", "content_tags": [],
            "signal_strength": "", "noise_risk": "", "fetch_reliability": "",
            "params": {"limit": 12, "fetch_detail_if_missing": True},
        }
        return {"ok": True, "page_type": "rss", "llm_used": False, "detail_profiled": False,
                "proposed_config": cfg, "signals_summary": {"title": title}, "warnings": warnings}

    if page_type == "json":
        warnings.append("检测到 JSON 接口，自动分析有限，请手动补全 listing/字段映射。")

    # HTML：收集信号 + RSS 自发现提示
    signals = collect_html_signals(final_url, text)
    soup = BeautifulSoup(text, "html.parser")
    feed = find_rss_autodiscovery(soup, final_url)
    if feed:
        warnings.append(f"该页提供 RSS 订阅，通常更稳定：{feed}")

    sid = slug_source_id(final_url, existing_ids, prefix="web")
    cfg = heuristic_config(final_url, signals, source_id=sid)

    llm_used = False
    detail_profiled = False
    try:
        from services.daily_brief import resolve_llm_config

        llm_config = resolve_llm_config(session)
    except Exception:  # noqa: BLE001
        llm_config = None

    if llm_config is not None and getattr(llm_config, "configured", False):
        usage_cfg_meta = UsageMeta(purpose="source_config", username=usage_username or "system")
        usage_detail_meta = UsageMeta(purpose="detail_profile", username=usage_username or "system")
        try:
            llm_cfg = await propose_config_via_llm(signals, llm_config, usage_cfg_meta)
            cfg = merge_llm_config(cfg, llm_cfg)
            llm_used = True
        except LLMError as e:
            warnings.append(f"LLM 配置精修失败，已用启发式基线：{e}")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"LLM 配置精修异常，已用启发式基线：{e}")

        # 详情 Profile：取一条命中文章模式的样例链接分析
        sample_url = _first_article_url(signals, cfg["params"].get("article_url_patterns", ""))
        if sample_url:
            try:
                profile = await propose_detail_profile(sample_url, llm_config, usage_detail_meta)
                if profile:
                    apply_detail_profile(cfg, profile)
                    detail_profiled = True
            except LLMError as e:
                warnings.append(f"详情 Profile 分析失败：{e}")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"详情 Profile 分析异常：{e}")
    else:
        warnings.append("未配置 LLM，已用启发式基线配置（可手动调整后预览）。")

    return {
        "ok": True,
        "page_type": page_type,
        "llm_used": llm_used,
        "detail_profiled": detail_profiled,
        "proposed_config": cfg,
        "signals_summary": {
            "page_title": signals.get("page_title"),
            "site_name": signals.get("site_name"),
            "pattern_candidates": signals.get("pattern_candidates"),
            "candidate_links": len(signals.get("sample_links", [])),
        },
        "warnings": warnings,
    }


def _first_article_url(signals: Dict[str, Any], patterns_csv: str) -> str:
    patterns = [p.strip() for p in (patterns_csv or "").split(",") if p.strip()]
    for item in signals.get("sample_links", []):
        u = item.get("url", "")
        if not patterns or any(p in u for p in patterns):
            return u
    return ""


# ============ 主入口：试抓预览（不落库）============

async def preview_config(payload: Dict[str, Any], *, max_entries: int = 5) -> Dict[str, Any]:
    source_type = str(payload.get("source_type") or "web").lower()
    params: Dict[str, Any] = dict(payload.get("params") or {})
    params.setdefault("source_id", payload.get("source_id") or "preview_tmp")
    params["source_id"] = payload.get("source_id") or params["source_id"]
    params["category"] = payload.get("category") or params.get("category") or ""
    params["limit"] = min(int(params.get("limit") or max_entries), max_entries)

    # 预览会用建议配置真去抓 listing/detail，先按 SSRF 判定挡住指向内网的目标。
    target_url = str(
        payload.get("url") or params.get("listing_url") or params.get("feed_url") or ""
    )
    if target_url:
        await ensure_public_host(urlparse(target_url).hostname or "")

    if source_type in {"web", "webpage"}:
        from fetchers.impl.configurable_web_fetcher import ConfigurableWebFetcher

        params["listing_url"] = payload.get("url") or params.get("listing_url") or ""
        params["site_name"] = params.get("site_name") or payload.get("name") or ""
        fetcher = ConfigurableWebFetcher()
    elif source_type in {"rss", "atom"}:
        from fetchers.impl.rss_fetcher import GenericRssFetcher

        params["feed_url"] = payload.get("url") or params.get("feed_url") or ""
        params["feed_name"] = payload.get("name") or params.get("feed_name") or ""
        fetcher = GenericRssFetcher()
    else:
        return {"ok": False, "error": f"不支持的 source_type：{source_type}"}

    entries: List[Dict[str, Any]] = []
    has_content = 0
    try:
        async for item in fetcher.fetch(**params):
            body = item.content or ""
            if body.strip():
                has_content += 1
            entries.append({
                "title": item.title,
                "url": item.source_url,
                "publish_date": item.publish_date,
                "method": (item.raw_data or {}).get("detail_extraction_method", ""),
                "content_preview": body[:200],
            })
            if len(entries) >= max_entries:
                break
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"试抓失败：{e}", "entries": entries}

    return {
        "ok": True,
        "count": len(entries),
        "has_content_count": has_content,
        "entries": entries,
    }
