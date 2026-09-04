"""正文 markdown → 净化 HTML(Issue #17 小程序端 `rich-text` 供给)。

小程序没有 DOM,前端 react-markdown 链路整体不可用,正文改由服务端一次渲染:

- **解析**:markdown-it-py,commonmark + 表格/删除线,`breaks=True` 镜像前端 remark-breaks;
  `html=False`——正文里的原始 HTML 一律按文本转义,解析器产出的标签集合本身就是受控的。
- **净化**:再过一遍标签/属性白名单(`rich-text` 支持的子集),`script/style/iframe` 之类
  连内容一起丢;属性只留 `href`(a)/`src`+`alt`(img)/`class`(仅 code 的 `language-*`)/`start`(ol)。
- **图链改写**:`<img src>` 一律经 `image_url_mapper` 改成签名公开图链(api.media_signing),
  映射结果为空即整图丢弃(data:/svg/非 http)——客户端拿不到能直连的原图。
- **公式**:`$…$` 不解析,按原文文本保留(rich-text 排不了 KaTeX);首版接受,记 backlog。
- **重复标题剥离**:正文首个非空行是与文章标题同名的 ATX 标题时剥掉——与前端
  `utils/markdownTitle.js` 的 `stripDuplicateLeadingHeading` 同一规则(归一化键去标记/
  emoji/空白与分隔标点后比较);归档正文本身不动,只在「标题已由页面画出」的渲染场景生效。

渲染现算不落库(与媒体热点图同思路);正文 markdown 在库中不变,导出契约零影响。
"""

from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional, Tuple

from markdown_it import MarkdownIt

# rich-text 支持的标签子集(微信文档「rich-text 组件 · 受信任的 HTML 节点」),
# 只列正文渲染会用到的;不在名单里的标签剥壳保留子内容。
ALLOWED_TAGS = frozenset({
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "del", "ins", "mark", "small", "sub", "sup",
    "code", "pre",
    "blockquote",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "a", "img",
    "div", "span",
})
# 连子内容一起丢弃的标签(markdown-it html=False 下本不会出现,纵深防御)。
DROP_WITH_CONTENT = frozenset({"script", "style", "iframe", "object", "embed", "video", "audio", "svg", "math"})
VOID_TAGS = frozenset({"br", "hr", "img"})
_LANGUAGE_CLASS_RE = re.compile(r"^language-[A-Za-z0-9_+.#-]{1,32}$")

ImageMapper = Callable[[str], str]


def _make_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True, "typographer": False})
    md.enable(["table", "strikethrough"])
    return md


_PARSER = _make_parser()


# ---------- 重复标题剥离(与前端 markdownTitle.js 同规则) ----------

_HEADING_RE = re.compile(r"^#{1,6}\s+")
_SEPARATOR_RE = re.compile(r"[\s·•\-—–|:：,，.。、/\\]")


def _normalize_heading_key(text: str) -> str:
    value = _HEADING_RE.sub("", str(text or "").strip(), count=1)
    value = re.sub(r"[*_`~]", "", value)
    # emoji/符号:Unicode 类别 So(Symbol, other)+ 变体选择符/零宽连接符
    value = "".join(
        ch for ch in value
        if unicodedata.category(ch) != "So" and ch not in ("️", "‍")
    )
    value = _SEPARATOR_RE.sub("", value)
    return value.lower()


def strip_duplicate_leading_heading(body: str, title: str) -> str:
    """正文首个非空行是与 title 同名的 ATX 标题 → 剥离该行及其后空行;否则原样返回。"""
    if not body or not title:
        return body or ""
    key = _normalize_heading_key(title)
    if not key:
        return body
    lines = body.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return body
    first = lines[i].strip()
    if not _HEADING_RE.match(first):
        return body
    if _normalize_heading_key(first) != key:
        return body
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    return "\n".join(lines[j:])


# ---------- HTML 净化 ----------

class _Sanitizer(HTMLParser):
    def __init__(self, image_url_mapper: ImageMapper):
        super().__init__(convert_charrefs=True)
        self._map_image = image_url_mapper
        self._out: List[str] = []
        self._drop_depth = 0  # 处于 DROP_WITH_CONTENT 子树内的深度
        self._open: List[Optional[str]] = []  # None = 被剥壳的标签(不输出闭合)
        self.image_urls: List[str] = []  # 原始图链(保序去重),供调用方审计

    # -- 属性过滤 --
    def _filter_attrs(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> Optional[Dict[str, str]]:
        kept: Dict[str, str] = {}
        for name, value in attrs:
            value = value or ""
            if tag == "a" and name == "href":
                if value.lower().startswith(("http://", "https://")):
                    kept["href"] = value
            elif tag == "img" and name == "src":
                mapped = self._map_image(value)
                if not mapped:
                    return None  # 图链不可用 → 整图丢弃
                if value not in self.image_urls:
                    self.image_urls.append(value)
                kept["src"] = mapped
            elif tag == "img" and name == "alt":
                kept["alt"] = value
            elif tag == "code" and name == "class":
                classes = [c for c in value.split() if _LANGUAGE_CLASS_RE.match(c)]
                if classes:
                    kept["class"] = classes[0]
            elif tag == "ol" and name == "start" and value.isdigit():
                kept["start"] = value
            elif tag in ("th", "td") and name == "style":
                # markdown-it 表格对齐用 style="text-align:…";rich-text 支持 style,仅放行该项
                match = re.fullmatch(r"\s*text-align\s*:\s*(left|center|right)\s*;?\s*", value)
                if match:
                    kept["style"] = f"text-align:{match.group(1)}"
        if tag == "img" and "src" not in kept:
            return None
        return kept

    def _emit_open(self, tag: str, attrs: Dict[str, str], self_closing: bool) -> None:
        parts = [f"<{tag}"]
        for name, value in attrs.items():
            parts.append(f' {name}="{html.escape(value, quote=True)}"')
        parts.append(" />" if self_closing else ">")
        self._out.append("".join(parts))

    # -- 事件 --
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if self._drop_depth:
            if tag not in VOID_TAGS:
                self._drop_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self._drop_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            if tag not in VOID_TAGS:
                self._open.append(None)
            return
        kept = self._filter_attrs(tag, attrs)
        if tag in VOID_TAGS:
            if kept is not None:
                self._emit_open(tag, kept, self_closing=True)
            return
        self._open.append(tag)
        self._emit_open(tag, kept or {}, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            self.handle_starttag(tag, attrs)
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._drop_depth:
            if tag not in VOID_TAGS:
                self._drop_depth -= 1
            return
        if tag in VOID_TAGS:
            return
        # 找最近一个同名或被剥壳的开标签(markdown-it 输出结构良好,通常就是栈顶)
        for idx in range(len(self._open) - 1, -1, -1):
            opened = self._open[idx]
            if opened == tag or (opened is None and tag not in ALLOWED_TAGS):
                del self._open[idx]
                if opened is not None:
                    self._out.append(f"</{tag}>")
                return

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self._out.append(html.escape(data, quote=False))

    def result(self) -> str:
        # 收尾:未闭合的开标签补齐(防御)
        for tag in reversed(self._open):
            if tag is not None:
                self._out.append(f"</{tag}>")
        self._open.clear()
        return "".join(self._out)


def sanitize_html(raw_html: str, image_url_mapper: ImageMapper) -> Tuple[str, List[str]]:
    """白名单净化 + 图链改写;返回 (html, 原始图链列表)。"""
    parser = _Sanitizer(image_url_mapper)
    parser.feed(raw_html or "")
    parser.close()
    return parser.result(), parser.image_urls


# ---------- 入口 ----------

def render_markdown(markdown: str, image_url_mapper: ImageMapper, *, title: str = "") -> Tuple[str, List[str]]:
    """markdown 正文 → 净化 HTML。`title` 非空时先剥离与之重复的首行标题。

    返回 (html, 正文中被改写的原始图链列表)。
    """
    body = strip_duplicate_leading_heading(markdown or "", title) if title else (markdown or "")
    if not body.strip():
        return "", []
    raw = _PARSER.render(body)
    return sanitize_html(raw, image_url_mapper)
