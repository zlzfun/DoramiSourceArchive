"""Bounded Markdown translation with lossless literal protection.

The model sees prose and opaque placeholders. Formulae, code and destinations
are restored locally, only after every chunk and placeholder has been checked.
This is a conservative splitter, not a general Markdown renderer: an oversized
table row or indivisible word fails explicitly instead of being sliced.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import uuid

from services.paper_content import strip_layout_markers


class TranslationStructureError(ValueError):
    pass


_TOKEN = re.compile(r"DORAMITR[0-9a-f]{12}X\d+Z")
_FENCE = re.compile(r"(?m)^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)")
_WITHIN_PARAGRAPH = r"(?:(?!\n[ \t]*\n)[\s\S])*?"
_INLINE_CODE = re.compile(r"(`+)(?!`)" + _WITHIN_PARAGRAPH + r"(?<!`)\1(?!`)")
_BLOCK_MATH = re.compile(
    r"(?m)^ {0,3}(?P<dollars>\${2,})[ \t]*\n[\s\S]*?^ {0,3}(?P=dollars)[ \t]*(?=\n|$)"
    r"|^ {0,3}\\\[[ \t]*\n[\s\S]*?^ {0,3}\\\][ \t]*(?=\n|$)"
)
_MATH = re.compile(
    r"(?<![\\$])(?P<wide>\${2,})(?!\$)" + _WITHIN_PARAGRAPH + r"(?<!\$)(?P=wide)(?!\$)"
    r"|(?<!\\)\\\[" + _WITHIN_PARAGRAPH + r"\\\]"
    r"|(?<!\\)\\\(" + _WITHIN_PARAGRAPH + r"\\\)"
    # Pandoc-style adjacency avoids treating '$5 ... $10' as a formula.
    r"|(?<![\\$])\$(?![\s$])(?:\\.|[^$\\\n])*?(?<!\s)\$(?![\d$])"
)
_DESTINATION = re.compile(
    r"""(?P<target><[^<>\n]*>|[^\s<>]+)(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*$"""
)
_PIPES = re.compile(r"(?<!\\)\|")
_ALIGNMENT = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _valid_destination(value: str) -> bool:
    match = _DESTINATION.fullmatch(value)
    if not match:
        return False
    target = match["target"]
    if target.startswith("<"):
        return True
    depth, escaped = 0, False
    for char in target:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _table_shape(text: str) -> tuple[int, ...]:
    # Inline code, formulae and link destinations have already been masked.
    return tuple(len(_PIPES.findall(line)) for line in text.splitlines() if _PIPES.search(line))


@dataclass(frozen=True)
class TranslationChunk:
    text: str
    separator: str = ""

    @property
    def needs_translation(self) -> bool:
        return bool(_TOKEN.sub("", self.text).strip())

    def validate(self, translated: str) -> str:
        translated = translated.strip("\r\n")
        if not translated.strip():
            raise TranslationStructureError("翻译片段为空，整篇译文未保存")
        if _TOKEN.findall(translated) != _TOKEN.findall(self.text):
            raise TranslationStructureError("译文中的公式、代码或链接不完整，整篇译文未保存")
        if _table_shape(translated) != _table_shape(self.text):
            raise TranslationStructureError("译文表格结构发生变化，整篇译文未保存")
        return translated


@dataclass(frozen=True)
class TranslationPlan:
    chunks: tuple[TranslationChunk, ...]
    literals: dict[str, str]

    def assemble(self, translations: list[str]) -> str:
        if len(translations) != len(self.chunks):
            raise TranslationStructureError("翻译片段缺失，整篇译文未保存")
        body = "".join(chunk.separator + chunk.validate(value)
                       for chunk, value in zip(self.chunks, translations))
        # Later protected spans can contain earlier tokens (e.g. a code span
        # inside a link destination); restore in reverse insertion order.
        for token, original in reversed(self.literals.items()):
            body = body.replace(token, original)
        return body


def _protect(body: str) -> tuple[str, dict[str, str]]:
    if _TOKEN.search(body):
        raise TranslationStructureError("正文含翻译保留标记，无法安全处理")
    literals: dict[str, str] = {}
    prefix = f"DORAMITR{uuid.uuid4().hex[:12]}X"

    def save(value: str) -> str:
        token = f"{prefix}{len(literals)}Z"
        literals[token] = value
        return token

    # Fenced code can contain blank lines and other Markdown delimiters.
    parts, cursor = [], 0
    while match := _FENCE.search(body, cursor):
        marker = match[1]
        close = re.compile(r"(?m)^ {0,3}" + re.escape(marker[0]) +
                           "{" + str(len(marker)) + r",}[ \t]*(?=\n|$)").search(body, match.end())
        end = close.end() if close else len(body)
        parts.extend((body[cursor:match.start()], save(body[match.start():end])))
        cursor = end
    parts.append(body[cursor:])
    body = "".join(parts)
    body = _BLOCK_MATH.sub(lambda match: save(match[0]), body)
    body = _INLINE_CODE.sub(lambda match: save(match[0]), body)

    # Balanced Markdown destinations: regex alone cuts URLs containing '(...)'.
    parts, cursor, search = [], 0, 0
    while (start := body.find("](", search)) >= 0:
        depth, end, quote, angle = 1, start + 2, "", False
        while end < len(body) and depth:
            char = body[end]
            if char == "\n" and re.match(r"\n[ \t]*\n", body[end:]):
                break
            if char == "\\":
                end += 2
                continue
            if quote:
                if char == quote:
                    quote = ""
            elif angle:
                if char == ">":
                    angle = False
            elif char in "\"'" and end > start + 2 and body[end - 1].isspace():
                quote = char
            elif char == "<":
                angle = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            end += 1
        destination = body[start + 2:end - 1]
        if depth or not _valid_destination(destination):
            search = start + 2
            continue
        parts.extend((body[cursor:start + 2], save(destination), ")"))
        cursor, search = end, end
    parts.append(body[cursor:])
    body = "".join(parts)
    body = re.sub(r"(?m)^ {0,3}\[[^\]\n]+\]:[ \t]*(?P<destination>[^\n]*)",
                  lambda match: save(match[0]) if _valid_destination(match["destination"])
                  else match[0], body)
    body = re.sub(r"https?://[^\s<>]+", lambda match: save(match[0]), body)
    body = _MATH.sub(lambda match: save(match[0]), body)
    # Keep alignment rows verbatim; each data row remains an indivisible unit.
    body = "\n".join(save(line) if _ALIGNMENT.fullmatch(line) else line
                     for line in body.split("\n"))
    return body, literals


def _split_line(line: str, limit: int) -> list[tuple[str, str]]:
    """Return (separator, text) pieces without cutting a word or placeholder."""
    if len(line) <= limit:
        return [("", line)]
    if _PIPES.search(line):
        raise TranslationStructureError("表格单行过长，暂不能安全翻译；原文保持可读")
    pieces, separator = [], ""
    while len(line) > limit:
        # A link label may contain spaces, but cannot straddle two requests.
        depth, escaped, safe = 0, False, set()
        for position, char in enumerate(line[:limit + 1]):
            if depth == 0:
                safe.add(position)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth = max(0, depth - 1)
        # Prefer sentence boundaries; fall back to whitespace between words.
        boundaries = list(re.finditer(
            r"(?<=[.!?;])\s+|(?<=[。！？；])(?:\s+|(?=\S))", line[:limit + 1]
        ))
        boundaries = [m for m in boundaries if 0 < m.start() <= limit and m.start() in safe]
        if not boundaries:
            boundaries = [m for m in re.finditer(r"\s+", line[:limit + 1])
                          if 0 < m.start() <= limit and m.start() in safe]
        if not boundaries:
            raise TranslationStructureError("正文含超长不可拆分内容，暂不能安全翻译")
        boundary = boundaries[-1]
        pieces.append((separator, line[:boundary.start()]))
        separator, line = boundary[0], line[boundary.end():]
    if line:
        pieces.append((separator, line))
    return pieces


def plan_translation(body: str, *, segment_chars: int = 3500) -> TranslationPlan:
    if segment_chars < 64:
        raise ValueError("segment_chars must be at least 64")
    body, literals = _protect(strip_layout_markers(body).strip())
    chunks: list[TranslationChunk] = []
    buffer, separator = "", ""
    # Blank lines, list item boundaries and table row boundaries are retained
    # as exact separators, including when they cross a request boundary.
    pending = ""
    for item in re.split(r"(\n+)", body):
        if not item:
            continue
        if item.startswith("\n"):
            pending += item
            continue
        for index, (within, piece) in enumerate(_split_line(item, segment_chars)):
            before = pending if index == 0 else within
            if buffer and len(buffer) + len(before) + len(piece) > segment_chars:
                chunks.append(TranslationChunk(buffer, separator))
                buffer, separator = piece, before
            elif buffer:
                buffer += before + piece
            else:
                buffer, separator = piece, before
        pending = ""
    if buffer:
        chunks.append(TranslationChunk(buffer, separator))
    return TranslationPlan(tuple(chunks), literals)
