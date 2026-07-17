
# 正文硬上限(参数退场波,2026-07):detail_max_chars 用户参数已退场——内置节点恒抓全文,
# 下游(阅读器/翻译/QA/向量化)吃完整正文。此常量仅作病态页兜底(提取失败把导航/评论
# 吞进正文的超长文本),正常文章永不触顶;它不是给用户调的旋钮。
# 40K → 200K(2026-07-17):Lil'Log 的深度长文正文 45K+ 字符被 40K 截断(用户抽检,
# 结尾戛然而止)——40K 低估了正常长文上界;200K 仍拦得住病态页(其量级通常 MB 级)。
DETAIL_HARD_CAP = 200_000

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag


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


_MD_BLOCK_CONTAINERS = {
    "div", "section", "article", "main", "figure", "header", "footer", "aside", "ul", "ol",
}
_MD_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_MD_SKIP_TAGS = {"script", "style", "noscript", "svg", "form", "button", "iframe"}


# 懒加载图片的真实地址常放在这些属性里，src 多为占位图
_LAZY_IMG_ATTRS = (
    "data-original",
    "data-src",
    "data-actualsrc",
    "data-lazy-src",
    "data-echo",
)
# src 命中这些特征时判定为占位图（懒加载占位 / 透明像素），需回退到懒加载属性
_IMG_PLACEHOLDER_HINTS = (
    "images/v2/t.png",
    "/blank.",
    "placeholder",
    "spacer",
    "1x1.",
    "grey.gif",
    "loading.gif",
)


def _is_placeholder_img(url: str) -> bool:
    low = url.lower()
    return any(hint in low for hint in _IMG_PLACEHOLDER_HINTS)


def _abs_image_url(base_url: str, src: str) -> str:
    """把图片 src 解析为绝对 URL，过滤 data-uri / 空值。"""
    src = (src or "").strip()
    if not src or src.startswith("data:"):
        return ""
    return urljoin(base_url or "", src)


def _pick_image_src(node: Tag) -> str:
    """选出图片真实地址：懒加载属性优先，src 仅作兜底（且过滤占位图）。"""
    for attr in _LAZY_IMG_ATTRS:
        value = (node.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return value
    src = (node.get("src") or "").strip()
    if src and not _is_placeholder_img(src):
        return src
    return ""


def _img_markdown(node: Tag, base_url: str) -> str:
    url = _abs_image_url(base_url, _pick_image_src(node))
    if not url:
        return ""
    alt = " ".join((node.get("alt") or "").split())
    return f"![{alt}]({url})"


def _inline_markdown(node: Tag, base_url: str) -> str:
    """把行内内容（文本 + <a> + 行内 <img> + <br>）转成单段 markdown 文本。"""
    parts: List[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name in _MD_SKIP_TAGS:
            continue
        if name == "br":
            parts.append("\n")
        elif name == "img":
            md = _img_markdown(child, base_url)
            if md:
                parts.append(f" {md} ")
        elif name == "a":
            text = _inline_markdown(child, base_url).strip()
            href = urljoin(base_url or "", (child.get("href") or "").strip())
            if text and href.startswith(("http://", "https://")):
                parts.append(f"[{text}]({href})")
            else:
                parts.append(text)
        else:
            parts.append(_inline_markdown(child, base_url))
    text = "".join(parts)
    # 折叠行内多余空白，但保留 <br> 引入的换行
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _table_markdown(table: Tag, base_url: str) -> str:
    """把 <table> 转成 GFM 表格(单块文本,行间单换行)。

    单元格用行内渲染(保 code/链接),内部 <br>/换行折叠为「; 」——GFM 单元格
    不允许换行。首个数据行之后强制补分隔行(GFM 要求表头才渲染为表格;源表格
    无 <th> 时把首行当表头,与主流转换器一致)。嵌套表格经行内渲染退化为文本。
    """
    lines: List[str] = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue  # 嵌套表格的行:已由外层单元格行内渲染退化,防重复
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        texts = []
        for cell in cells:
            text = _inline_markdown(cell, base_url).replace("\n", " ; ")
            text = re.sub(r"[ \t]+", " ", text).strip().replace("|", "\\|")
            texts.append(text or " ")
        if not any(t.strip() for t in texts):
            continue
        lines.append("| " + " | ".join(texts) + " |")
        if len(lines) == 1:
            lines.append("|" + "|".join([" --- "] * len(texts)) + "|")
    return "\n".join(lines) if len(lines) >= 2 else ""


def node_to_markdown(root: Tag, base_url: str = "") -> str:
    """把一个正文容器节点转成 markdown-ish 文本：保留图片、段落、列表与标题。

    设计目标是在不引入额外依赖的前提下，让 IT之家/新智元/changelog 等来源的正文
    保留图片(`![](url)`)与换行结构，供前端 react-markdown 渲染。
    """
    blocks: List[str] = []
    seen_imgs: set = set()

    def emit(text: str) -> None:
        text = (text or "").strip()
        if text:
            blocks.append(text)

    def walk(el: Tag) -> None:
        for child in el.children:
            if isinstance(child, NavigableString):
                stray = re.sub(r"[ \t ]+", " ", str(child)).strip()
                if stray:
                    emit(stray)
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if name in _MD_SKIP_TAGS:
                continue
            if name == "img":
                md = _img_markdown(child, base_url)
                if md and md not in seen_imgs:
                    seen_imgs.add(md)
                    emit(md)
            elif name in _MD_HEADINGS:
                text = _inline_markdown(child, base_url)
                if text:
                    emit("#" * int(name[1]) + " " + text.replace("\n", " "))
            elif name in ("p", "blockquote", "figcaption", "pre"):
                inner_imgs = child.find_all("img")
                text = _inline_markdown(child, base_url)
                if name == "blockquote" and text:
                    text = "\n".join("> " + ln for ln in text.split("\n"))
                emit(text)
                # 记录行内已渲染的图片，避免容器递归时重复
                for img in inner_imgs:
                    md = _img_markdown(img, base_url)
                    if md:
                        seen_imgs.add(md)
            elif name in ("ul", "ol"):
                for li in child.find_all("li", recursive=False):
                    text = _inline_markdown(li, base_url)
                    if text:
                        for ln in text.split("\n"):
                            emit("- " + ln)
            elif name == "table":
                # 表格转 GFM 语法(前端 remark-gfm 渲染):整表作为**单个 block**
                # emit——GFM 表格行之间不能有空行。此前表格落到递归兜底,每个
                # 文本节点/<code> 被逐个散块(2026-07-17 Lil'Log 用户抽检)。
                table_md = _table_markdown(child, base_url)
                if table_md:
                    emit(table_md)
            elif name in _MD_BLOCK_CONTAINERS:
                walk(child)
            else:
                # 其余块级元素（tr/td 等）退化为递归取内容
                walk(child)

    walk(root)
    return "\n\n".join(blocks)


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


def extract_detail_from_html(
    html: str, max_chars: int, detail_min_chars: int = 200, base_url: str = ""
) -> ArticleDetail:
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
                text = node_to_markdown(node, base_url)
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
    detail = extract_detail_from_html(html, max_chars, detail_min_chars, base_url=page_url)
    if len(detail.text) >= detail_min_chars:
        return detail

    spa_detail = await extract_spa_markdown_detail(client, safe_get, page_url, html, max_chars)
    if spa_detail.text and len(spa_detail.text) > len(detail.text):
        if not spa_detail.title:
            spa_detail.title = detail.title
        return spa_detail

    return detail
