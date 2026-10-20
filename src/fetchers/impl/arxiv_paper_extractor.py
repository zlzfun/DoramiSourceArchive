"""LaTeXML HTML -> Markdown, keeping one TeX representation per equation.

No PDF runtime, raw HTML rendering, or network calls in the converter. Unknown
structures are reported rather than silently advertised as complete text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString

from fetchers.impl.article_extractor import DETAIL_HARD_CAP, node_to_markdown

CONVERTER_VERSION = "arxiv-html-v1"
MAX_HTML_BYTES = 5 * 1024 * 1024


def _compatible_tex(latex: str) -> str:
    # LaTeXML emits xcolor's optional RGB argument, which KaTeX does not accept.
    # Convert only bounded numeric RGB colors; preserve all mathematical text.
    def color(match):
        values = [float(x.strip()) for x in match.group(1).split(",")]
        if len(values) != 3 or any(not 0 <= x <= 1 for x in values):
            return match.group(0)
        rgb = "".join(f"{round(x * 255):02x}" for x in values)
        return "\\color{#" + rgb + "}"

    latex = re.sub(r"\\color\[rgb\]\{([0-9.,\s]+)\}", color, latex)
    latex = re.sub(r"(\\begin\{array\})\[(?:[tbc])?\]", r"\1", latex)
    return latex


def _table_math_value(latex: str, value: str) -> tuple[str, bool]:
    # GFM's math tokenizer does not unescape \|, which TeX treats as a double
    # bar. Column-format bars are layout syntax, so show those as source instead
    # of silently changing their mathematical or alignment meaning.
    for match in re.finditer(r"\\begin\{(?:array|tabular)\}(?:\[[^\]]*\])?\s*\{", latex):
        depth = 1
        end = match.end()
        while end < len(latex) and depth:
            depth += (latex[end] == "{") - (latex[end] == "}")
            end += 1
        if "|" in latex[match.end():end - 1]:
            fence = "`" * (max((len(m[0]) for m in re.finditer(r"`+", latex)), default=0) + 1)
            return f"{fence} {latex.replace('|', chr(92) + '|')} {fence}", True

    def bar(match):
        slashes = match.group(1)
        # An odd slash count ends in a TeX \| norm; an even count leaves a
        # literal bar after any row breaks. Preserve both meanings.
        return (slashes[:-1] + r"\Vert " if len(slashes) % 2
                else slashes + r"\vert ")

    return re.sub(r"(\\*)\|", bar, value), False


@dataclass(frozen=True)
class PaperExtraction:
    content: str
    abstract: str
    source_url: str
    arxiv_id: str
    license_url: str
    math_count: int
    image_count: int
    table_count: int
    warnings: tuple[str, ...]


def _safe_url(base: str, value: str) -> str:
    url = urljoin(base, value)
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username:
            return ""
    except ValueError:
        return ""
    # Markdown destinations must not escape their delimiters.
    return url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def extract_arxiv_html(html: str, source_url: str) -> PaperExtraction:
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("html_too_large")
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("article.ltx_document")
    if root is None or not root.select_one(".ltx_section"):
        raise ValueError("not_paper_html")
    resolved_id = urlsplit(source_url).path.removeprefix("/html/").rstrip("/")
    # Prefer the versioned identity advertised by arXiv when /html/<id> stays
    # unversioned after redirect. Never manufacture a revision number.
    for node in soup.select('a[href], link[href]'):
        href = _safe_url(source_url, str(node.get("href") or ""))
        match = re.search(r"/(?:html|abs)/(\d{4}\.\d{4,5}v[1-9]\d*)(?:$|[#?])", href)
        if match and match.group(1).split("v")[0] == resolved_id.split("v")[0]:
            resolved_id = match.group(1)
            break
    license_url = ""
    for node in soup.select('a[href]'):
        href = _safe_url(source_url, str(node.get("href") or ""))
        host = urlsplit(href).hostname
        if host in {"creativecommons.org", "arxiv.org"} and (
            "/licenses/" in href or "/publicdomain/" in href
            or "/license/" in href or "/help/license" in href
        ):
            license_url = href
            break
    warnings: set[str] = set()
    replacements: dict[str, str] = {}
    table_math: dict[str, str] = {}

    def token(value: str) -> str:
        key = f"DORAMIPAPERTOKEN{len(replacements)}END"
        replacements[key] = value
        return key

    # Avoid collision with literal paper prose (or malicious source markup).
    if "DORAMIPAPERTOKEN" in html:
        raise ValueError("reserved_marker_in_source")
    # LaTeXML can leak unsupported document setup (e.g. \reportnumber 001)
    # before the title. Keep the title as the first content block so Reader's
    # existing duplicate-heading guard works; never trim actual body sections.
    title = root.select_one(".ltx_title_document")
    if title is not None:
        for node in list(title.find_previous_siblings()):
            if node.select_one(".ltx_ERROR.undefined"):
                node.decompose()
    for node in root.select("script, style, nav, .ltx_authors, .ltx_dates"):
        node.decompose()
    # Escape prose dollar signs before inserting TeX delimiters.
    for node in list(root.find_all(string=True)):
        if not node.find_parent(["math", "pre", "code"]):
            node.replace_with(NavigableString(str(node).replace("$", "\\$")))

    math_nodes = list(root.find_all("math"))
    for node in math_nodes:
        is_tex = False
        annotation = node.find("annotation", attrs={"encoding": "application/x-tex"})
        latex = str(node.get("alttext") or (annotation.get_text() if annotation else "")).strip()
        if not latex:
            warnings.add("math_without_tex")
            latex = node.get_text(" ", strip=True)
            value = f"[公式原文：{latex}]"
        else:
            display = node.get("display") == "block"
            # Inline TeX cannot contain newlines. Delimiters stay outside TeX.
            latex = _compatible_tex(latex)
            if re.search(r"\\(?:includegraphics|begin\{picture\})", latex):
                warnings.add("unsupported_tex_graphic")
                # External graphics inside TeX cannot be enabled with KaTeX's
                # trust=false. Keep their source visibly readable.
                fence = "`" * (max((len(m[0]) for m in re.finditer(r"`+", latex)), default=2) + 1)
                value = f"\n\n{fence}latex\n{latex}\n{fence}\n\n" if display else f" `{latex}` "
            else:
                is_tex = True
                if not display:
                    latex = re.sub(r"\s*\n\s*", " ", latex)
                width = max((len(m[0]) for m in re.finditer(r"\$+", latex)), default=0) + 1
                fence = "$" * max(2 if display else 1, width)
                # Whitespace separates adjacent MathML nodes, preventing their
                # delimiters from becoming a different $$ math span.
                value = (f"\n\n{fence}\n{latex}\n{fence}\n\n" if display
                         else f" {fence}{latex}{fence} ")
        key = token(value)
        parent_table = node.find_parent("table")
        if is_tex and parent_table is not None and "ltx_eqn_table" not in parent_table.get("class", []):
            table_math[key], limited = _table_math_value(latex, value)
            if limited:
                warnings.add("table_tex_layout")
        node.replace_with(NavigableString(key))
    # Equation layout tables are not data tables. Retain equation numbers.
    for table in root.select("table.ltx_eqn_table"):
        block = soup.new_tag("p")
        block.string = table.get_text(" ", strip=True)
        table.replace_with(block)

    image_count = 0
    for node in list(root.find_all(["img", "object"])):
        src = str((node.get("src") if node.name == "img" else node.get("data")) or "").strip()
        url = _safe_url(source_url, src)
        # Paper graphics must stay on the official origin; normal text links
        # below may still reference any HTTP(S) site.
        if not src or urlsplit(url).hostname != "arxiv.org":
            warnings.add("unsupported_image")
            node.replace_with(NavigableString("[图片请见原文]"))
            continue
        caption = str(node.get("alt") or "论文配图").replace("[", "").replace("]", "")
        node.replace_with(NavigableString(token(f"![{caption}]({url})")))
        image_count += 1
    if root.find("svg"):
        warnings.add("inline_svg")
        for node in root.find_all("svg"):
            node.replace_with(NavigableString("[矢量图请见原文]"))
    # All references point to the original document, never the app's hash router.
    for node in root.find_all("a", href=True):
        url = _safe_url(source_url, str(node["href"]))
        if url:
            node["href"] = url
        else:
            del node["href"]

    # Expand spans into a rectangular GFM grid. Repeated labels retain the
    # association that would otherwise shift columns or erase row headings.
    tables = list(root.find_all("table"))
    for table in reversed(tables):
        if table.find_parent("table"):
            warnings.add("nested_table")
        grid: dict[tuple[int, int], str] = {}
        rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        if len(rows) > 500:
            raise ValueError("table_too_large")
        for r, row in enumerate(rows):
            col = 0
            for cell in row.find_all(["th", "td"], recursive=False):
                while (r, col) in grid:
                    col += 1
                try:
                    rowspan = max(1, int(cell.get("rowspan", 1)))
                    colspan = max(1, int(cell.get("colspan", 1)))
                except (TypeError, ValueError):
                    rowspan = colspan = 1
                    warnings.add("invalid_table_span")
                if col + colspan > 80 or r + rowspan > 500:
                    raise ValueError("table_too_large")
                text = node_to_markdown(cell, source_url).replace("\n", " ; ").replace("|", "\\|")
                for rr in range(r, r + rowspan):
                    for cc in range(col, col + colspan):
                        if (rr, cc) in grid:
                            warnings.add("overlapping_table_span")
                        grid[rr, cc] = text
                col += colspan
        if not grid:
            table.decompose()
            continue
        width = max(c for _, c in grid) + 1
        height = max(r for r, _ in grid) + 1
        lines = ["| " + " | ".join(grid.get((r, c), "") for c in range(width)) + " |" for r in range(height)]
        lines.insert(1, "|" + " --- |" * width)
        # Use TeX-equivalent bar commands inside math; escaping a literal bar
        # with GFM's backslash would change it into the norm operator.
        table_md = "\n".join(lines)
        for key, value in list(replacements.items()):
            if key in table_md:
                cell_value = table_math.get(key, value.replace("|", "\\|"))
                table_md = table_md.replace(key, cell_value.strip().replace("\n", " "))
        block = soup.new_tag("p")
        block.string = token(table_md)
        table.replace_with(block)

    abstract_node = root.select_one(".ltx_abstract")
    abstract = node_to_markdown(abstract_node, source_url) if abstract_node else ""
    content = node_to_markdown(root, source_url)
    for key, value in reversed(list(replacements.items())):
        content = content.replace(key, value)
        abstract = abstract.replace(key, value)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    if len(content) < 500:
        raise ValueError("paper_too_short")
    if len(content) > DETAIL_HARD_CAP:
        raise ValueError("paper_too_long")
    return PaperExtraction(
        content=content, abstract=abstract, source_url=source_url, arxiv_id=resolved_id,
        license_url=license_url, math_count=len(math_nodes), image_count=image_count,
        table_count=len(tables), warnings=tuple(sorted(warnings)),
    )
