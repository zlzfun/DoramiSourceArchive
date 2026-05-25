import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


SafeGet = Callable[[httpx.AsyncClient, str], Awaitable[Optional[httpx.Response]]]


@dataclass
class ArticleDetail:
    title: str = ""
    text: str = ""
    method: str = ""
    url: str = ""


def clean_text(text: str, separator: str = "\n") -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(separator=separator, strip=True)


def compact_text(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detail_title(soup: BeautifulSoup) -> str:
    for selector in ["meta[property='og:title']", "meta[name='twitter:title']"]:
        node = soup.select_one(selector)
        if node:
            title = " ".join(str(node.get("content", "")).split())
            if title:
                return title

    heading = soup.find("h1")
    if heading:
        title = " ".join(heading.get_text(" ", strip=True).split())
        if title:
            return title

    if soup.title:
        return " ".join(soup.title.get_text(" ", strip=True).split())
    return ""


def json_ld_article_body(soup: BeautifulSoup) -> str:
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(node.string or "")
        except (TypeError, json.JSONDecodeError):
            continue

        payloads = data if isinstance(data, list) else [data]
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            article_body = payload.get("articleBody")
            if article_body:
                return " ".join(str(article_body).split())
    return ""


def extract_detail_from_html(html: str, max_chars: int, detail_min_chars: int = 200) -> ArticleDetail:
    soup = BeautifulSoup(html, "html.parser")
    title = detail_title(soup)
    article_body = json_ld_article_body(soup)
    if article_body:
        return ArticleDetail(title=title, text=article_body[:max_chars], method="json_ld")

    for tag in soup.find_all([
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "header",
        "footer",
        "form",
        "button",
    ]):
        tag.decompose()

    for selector in [
        "aside",
        "[role='navigation']",
        ".newsletter",
        ".related",
        ".share",
        ".comments",
        ".cookie",
    ]:
        for node in soup.select(selector):
            node.decompose()

    selector_groups = [
        ["[itemprop='articleBody']", ".article-body", ".article-content", ".entry-content", ".post-content"],
        [".rich-text", ".markdown", ".prose", ".blog-post", ".BlogContent"],
        [".article-module", ".module--text"],
        ["article"],
        ["main", "[role='main']"],
    ]
    candidates: List[str] = []
    for selector_group in selector_groups:
        group_texts: List[str] = []
        for selector in selector_group:
            for node in soup.select(selector):
                text = clean_text(str(node))
                if text:
                    group_texts.append(text)
        if group_texts:
            joined = "\n\n".join(dict.fromkeys(group_texts))
            candidates.append(joined)
            if len(joined) >= detail_min_chars:
                break

    if not candidates and soup.body:
        candidates.append(clean_text(str(soup.body)))

    detail_text = compact_text(max(candidates, key=len) if candidates else "")
    method = "html_selector" if detail_text else ""
    return ArticleDetail(title=title, text=detail_text[:max_chars], method=method)


def markdown_to_text(markdown_text: str) -> str:
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", markdown_text or "", flags=re.DOTALL)
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "- ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"\[\^[^\]]+\]:\s*", "", text)
    text = re.sub(r"\[\^[^\]]+\]", "", text)
    return compact_text(text)


def markdown_frontmatter(markdown_text: str) -> Dict[str, str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", markdown_text or "", flags=re.DOTALL)
    if not match:
        return {}
    data: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def page_slug(url: str) -> str:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    return path_parts[-1] if path_parts else ""


def same_origin_url(origin_url: str, path: str) -> str:
    parsed = urlparse(origin_url)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def script_urls(page_url: str, html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: List[str] = []
    for node in soup.find_all(["script", "link"]):
        src = node.get("src") or node.get("href")
        if not src:
            continue
        if node.name == "link" and node.get("rel") and "modulepreload" not in node.get("rel", []):
            continue
        absolute_url = urljoin(page_url, str(src))
        if absolute_url.endswith(".js") and absolute_url not in urls:
            urls.append(absolute_url)
    return urls


def markdown_candidates_from_js(page_url: str, slug: str, js_text: str) -> List[str]:
    candidates: List[str] = []
    for prefix, suffix in re.findall(r"fetch\(\s*`([^`]*?)\$\{[^}]+\}([^`]*)`", js_text):
        if suffix.endswith(".md"):
            candidates.append(urljoin(page_url, f"{prefix}{slug}{suffix}"))

    for literal in re.findall(r"['\"]([^'\"]*?\.md)['\"]", js_text):
        if slug in literal:
            candidates.append(urljoin(page_url, literal))

    return list(dict.fromkeys(candidates))


async def fetch_markdown_detail(
    client: httpx.AsyncClient,
    safe_get: SafeGet,
    markdown_url: str,
    max_chars: int,
) -> ArticleDetail:
    response = await safe_get(client, markdown_url)
    if not response:
        return ArticleDetail()
    content_type = response.headers.get("content-type", "")
    text = response.text
    if response.status_code >= 400 or ("text/html" in content_type and "<html" in text[:500].lower()):
        return ArticleDetail()

    body = markdown_to_text(text)
    if not body:
        return ArticleDetail()
    metadata = markdown_frontmatter(text)
    return ArticleDetail(
        title=metadata.get("title", ""),
        text=body[:max_chars],
        method="markdown_asset",
        url=markdown_url,
    )


async def extract_spa_markdown_detail(
    client: httpx.AsyncClient,
    safe_get: SafeGet,
    page_url: str,
    html: str,
    max_chars: int,
) -> ArticleDetail:
    slug = page_slug(page_url)
    if not slug:
        return ArticleDetail()

    direct_candidates = [
        same_origin_url(page_url, f"/assets/blog-posts/{slug}.md"),
        same_origin_url(page_url, f"/assets/blog/{slug}.md"),
        same_origin_url(page_url, f"/assets/posts/{slug}.md"),
    ]
    for markdown_url in direct_candidates:
        detail = await fetch_markdown_detail(client, safe_get, markdown_url, max_chars)
        if detail.text:
            return detail

    for js_url in script_urls(page_url, html)[:8]:
        response = await safe_get(client, js_url)
        if not response:
            continue
        for markdown_url in markdown_candidates_from_js(page_url, slug, response.text):
            detail = await fetch_markdown_detail(client, safe_get, markdown_url, max_chars)
            if detail.text:
                return detail

    return ArticleDetail()


async def extract_article_detail(
    client: httpx.AsyncClient,
    safe_get: SafeGet,
    page_url: str,
    html: str,
    max_chars: int,
    detail_min_chars: int = 200,
) -> ArticleDetail:
    detail = extract_detail_from_html(html, max_chars, detail_min_chars)
    if len(detail.text) >= detail_min_chars:
        return detail

    spa_detail = await extract_spa_markdown_detail(client, safe_get, page_url, html, max_chars)
    if spa_detail.text and len(spa_detail.text) > len(detail.text):
        if not spa_detail.title:
            spa_detail.title = detail.title
        return spa_detail

    return detail
