
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
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag


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
    # 零宽字符(ZWSP/ZWNJ/word joiner/BOM)是排版控制符,混进正文会隔断分词与搜索命中
    text = re.sub("[\\u200b\\u200c\\u2060\\ufeff]", "", text or "")
    # ZWJ 组合 emoji 需保留,只清独占一行的孤立 ZWJ(Webflow 富文本的空行占位残留)
    text = re.sub("(?m)^\\u200d+$", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_MD_BLOCK_CONTAINERS = {
    "div", "section", "article", "main", "figure", "header", "footer", "aside", "ul", "ol",
}
_MD_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# summary 是 <details> 折叠控件的开关标签("View details");video/audio 的子树是
# 播放器兜底内容与控件——均属 UI 件非正文
_MD_SKIP_TAGS = {"script", "style", "noscript", "svg", "form", "button", "iframe", "summary", "video", "audio"}


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


# 非正文元素的 class 特征:前三个是视觉隐藏的无障碍辅助文案(如 OpenAI 外链的
# "(opens in a new window)");kg-*-player 是 Ghost 博客的播放器控件条(时间码
# "0:00 / 1:34" 等,testingcatalog 抽检混入正文)
_HIDDEN_CLASS_HINTS = (
    "sr-only",
    "visually-hidden",
    "screen-reader",
    "kg-video-player",
    "kg-audio-player",
)


def _is_visually_hidden(node: Tag) -> bool:
    classes = " ".join(node.get("class") or []).lower()
    return any(hint in classes for hint in _HIDDEN_CLASS_HINTS)


def _unwrap_redirect_url(href: str) -> str:
    """解开跳转包裹链接(Google Docs 导出的 google.com/url?q=真实地址)。"""
    try:
        parsed = urlparse(href)
        if parsed.hostname in ("www.google.com", "google.com") and parsed.path == "/url":
            target = (parse_qs(parsed.query).get("q") or [""])[0]
            if target.startswith(("http://", "https://")):
                return target
    except ValueError:
        pass
    return href


def _wrap_boundary(raw: str, core: str) -> str:
    """给转换后的行内片段还原原始首尾空白(折叠为单个空格)。

    嵌套行内元素(<a>/<b>/<span>)的边界空格常在元素内侧,strip 后直接拼接
    会把前后单词黏死("take shape inAbilene")——须把边界空白垫回标记外侧。
    """
    lead = " " if raw[:1].isspace() else ""
    trail = " " if raw[-1:].isspace() else ""
    return f"{lead}{core}{trail}" if core else (lead or trail)


def _inline_render(node: Tag, base_url: str) -> str:
    """行内内容的递归渲染:**保留原始空白**,由顶层 `_inline_markdown` 统一折叠。"""
    parts: List[str] = []
    for child in node.children:
        if isinstance(child, Comment):
            continue  # HTML 注释(如 Reddit feed 的 SC_OFF/SC_ON)不是正文;Comment 是 NavigableString 子类,须先判
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name in _MD_SKIP_TAGS or _is_visually_hidden(child):
            continue
        if name == "br":
            parts.append("\n")
        elif name == "img":
            md = _img_markdown(child, base_url)
            if md:
                parts.append(f" {md} ")
        elif name == "a":
            raw = _inline_render(child, base_url)
            text = " ".join(raw.split())
            # 标题自链锚(Hugo/Docusaurus 的 [#]/¶/§ permalink)不是正文
            if text in ("#", "¶", "§"):
                parts.append(_wrap_boundary(raw, ""))
                continue
            href = _unwrap_redirect_url(urljoin(base_url or "", (child.get("href") or "").strip()))
            if text and href.startswith(("http://", "https://")):
                parts.append(_wrap_boundary(raw, f"[{text}]({href})"))
            else:
                parts.append(_wrap_boundary(raw, text))
        elif name in ("strong", "b", "em", "i"):
            raw = _inline_render(child, base_url)
            text = " ".join(raw.split())
            marker = "**" if name in ("strong", "b") else "*"
            # 内容自带标记(如 <strong><b> 嵌套)时不重复包裹
            if text and not (text.startswith(marker) and text.endswith(marker)):
                text = f"{marker}{text}{marker}"
            parts.append(_wrap_boundary(raw, text))
        else:
            parts.append(_inline_render(child, base_url))
    return "".join(parts)


def _inline_markdown(node: Tag, base_url: str) -> str:
    """把行内内容（文本 + <a> + 行内 <img> + <br>）转成单段 markdown 文本。"""
    text = _inline_render(node, base_url)
    # 折叠行内多余空白，但保留 <br> 引入的换行
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


# 块级探针:容器内出现任一此类后代即视为"结构容器"须递归;否则是"行内-only 容器",
# 整体当一个段落渲染(避免文本节点/<code>/<a> 被 walk 拆散成各自的 block——
# DeepSeek changelog 的 "set to `deepseek-v4-pro` or …" 曾散成 4 块)。
_BLOCK_PROBE_TAGS = sorted(
    (_MD_BLOCK_CONTAINERS | _MD_HEADINGS | {"p", "blockquote", "figcaption", "pre", "table", "li"})
)


def _is_inline_only(node: Tag) -> bool:
    # img 不算行内-only:交给 walk 的 img 分支以维持 seen_imgs 去重
    return node.find(_BLOCK_PROBE_TAGS) is None and node.find("img") is None


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
    # 根节点本身是段落级/行内-only 容器时整体按单段渲染——walk 只按"子节点"分发,
    # 直接传入 <p>text <code>x</code></p> 会把行内内容散成多个 block(DeepSeek changelog)
    root_name = (getattr(root, "name", "") or "").lower()
    if root_name == "table":
        return _table_markdown(root, base_url)
    if root_name in ("p", "figcaption", "pre") or (
        root_name not in ("ul", "ol", "blockquote") and _is_inline_only(root)
    ):
        return _inline_markdown(root, base_url)

    blocks: List[str] = []
    seen_imgs: set = set()

    def emit(text: str) -> None:
        text = (text or "").strip()
        if text:
            blocks.append(text)

    def walk(el: Tag) -> None:
        for child in el.children:
            if isinstance(child, Comment):
                continue  # HTML 注释(如 Reddit feed 的 SC_OFF/SC_ON)不是正文;Comment 是 NavigableString 子类,须先判
            if isinstance(child, NavigableString):
                stray = re.sub(r"[ \t ]+", " ", str(child)).strip()
                if stray:
                    emit(stray)
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if name in _MD_SKIP_TAGS or _is_visually_hidden(child):
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
                if _is_inline_only(child):
                    emit(_inline_markdown(child, base_url))
                else:
                    walk(child)
            else:
                # 其余块级元素（tr/td 等）：纯行内内容整体按段落渲染，
                # 否则退化为递归取内容
                if _is_inline_only(child):
                    emit(_inline_markdown(child, base_url))
                else:
                    walk(child)

    walk(root)
    return "\n\n".join(blocks)


def keep_dominant_child_html(html: str, container: str = "article") -> str:
    """把首个 ``container`` 元素收敛为其文本最长的直接子块,剔除头尾兄弟镶边。

    适用于「容器 = 头部横幅 + 正文块 + 相关推荐/署名」三段式版式且类名无语义
    (Tailwind 哈希/混淆类)无法选择器化的站点(openai.com/cloud.google.com)。
    标题/日期等元数据由页面 meta 单独提取,不依赖头部横幅。失败/结构不符原样返回。
    """
    if not html:
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
        node = soup.select_one(container)
        if node is None:
            return html
        children = [c for c in node.find_all(recursive=False) if isinstance(c, Tag)]
        if len(children) < 2:
            return html
        body = max(children, key=lambda c: len(c.get_text(" ", strip=True)))
        for child in children:
            if child is not body:
                child.decompose()
        return str(soup)
    except Exception:
        return html


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
                # 逐行折叠行内空白但保留换行段落结构——此前 " ".join(split()) 把
                # \r\n\r\n 段落分隔一并压平,整篇塌成一段(the_decoder 2026-07 抽检)
                paragraphs = [
                    " ".join(part.split())
                    for part in re.split(r"(?:\r?\n)+", str(article_body))
                ]
                return "\n\n".join(p for p in paragraphs if p)
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
