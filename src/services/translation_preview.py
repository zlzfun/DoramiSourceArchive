"""Restore independently complete Markdown blocks from fenced checkpoints.

Model chunks are transport units, never rendering boundaries. Markdown block
maps are computed on restored source, then mapped to protected checkpoint lines.
A block is revealed only when every chunk contributing prose to it is valid.
Nothing in this module publishes a partial body to the final translation cache.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from markdown_it import MarkdownIt

from services.translation_markdown import TranslationPlan, TranslationStructureError, _TOKEN

MAX_PREVIEW_BLOCKS = 20_000


def _restore(text: str, plan: TranslationPlan) -> str:
    # Resolve only tokens present here. Scanning every document literal for
    # every line/block makes formula-heavy previews quadratic in source size.
    active: set[str] = set()

    def expand(match):
        token = match.group(0)
        if token in active or token not in plan.literals:
            raise TranslationStructureError("译文含未还原内容，暂不能展示")
        active.add(token)
        try:
            return _TOKEN.sub(expand, plan.literals[token])
        finally:
            active.remove(token)

    return _TOKEN.sub(expand, text)


@dataclass(frozen=True)
class PreviewBlock:
    id: str
    kind: str
    source: str
    # Each line is a sequence of local literal separators or (chunk, line) refs.
    lines: tuple[tuple[str | tuple[int, int], ...], ...]
    dependencies: frozenset[int]
    references: str = ""


@dataclass(frozen=True)
class PreviewLayout:
    blocks: tuple[PreviewBlock, ...]


def _source_lines(plan: TranslationPlan) -> list[list[str | tuple[int, int]]]:
    lines: list[list[str | tuple[int, int]]] = [[]]
    for index, chunk in enumerate(plan.chunks):
        for part_index, part in enumerate(chunk.separator.split("\n")):
            if part_index:
                lines.append([])
            if part:
                lines[-1].append(part)
        for line_index, line in enumerate(chunk.text.split("\n")):
            if line_index:
                lines.append([])
            if line:
                lines[-1].append((index, line_index))
    return lines


def _join_lines(lines, values: dict[int, list[str]]) -> str:
    return "\n".join("".join(part if isinstance(part, str) else values[part[0]][part[1]]
                            for part in line) for line in lines)


def _labels(tokens) -> set[str]:
    result = set()
    for token in tokens:
        label = token.meta.get("label")
        if label:
            result.add(label)
        if token.children:
            result.update(_labels(token.children))
    return result


def layout_for(plan: TranslationPlan) -> PreviewLayout:
    parts = _source_lines(plan)
    originals = {i: chunk.text.split("\n") for i, chunk in enumerate(plan.chunks)}
    masked = _join_lines(parts, originals)
    # Parse the *restored source*, preserving the indentation/container of
    # multiline literals. Map physical source lines back to protected lines;
    # all lines inside a code/math literal are one indivisible dependency.
    restored_lines, origins = [], []
    for index, line in enumerate(masked.split("\n")):
        expanded = _restore(line, plan).split("\n")
        restored_lines.extend(expanded)
        origins.extend([index] * len(expanded))
    environment = {}
    tokens = MarkdownIt("commonmark", {"store_labels": True}).enable("table").parse(
        "\n".join(restored_lines), environment)
    definitions = {
        label: "\n".join(restored_lines[reference["map"][0]:reference["map"][1]])
        for label, reference in environment.get("references", {}).items()
    }
    groups = []
    # Top-level open/leaf tokens each cover a complete Markdown container.
    # CommonMark has no block-math rule: if two parsed blocks share the same
    # protected literal line, merge them rather than revealing half a formula.
    for position, token in enumerate(tokens):
        if token.level != 0 or token.map is None or token.nesting < 0:
            continue
        start, end = token.map
        if start >= len(origins) or end <= start:
            continue
        low, high = origins[start], origins[min(end, len(origins)) - 1] + 1
        children = []
        for child_index in range(position + 1, len(tokens)):
            child = tokens[child_index]
            if child.level == 0 and child.nesting >= 0:
                break
            if child.children:
                children.extend(child.children)
        labels = _labels([token, *children])
        if groups and low < groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], high)
            groups[-1][2] = "other"
            groups[-1][3].update(labels)
        else:
            groups.append([low, high, token.type, labels])
    blocks = []
    for start, end, token_type, labels in groups:
        block_lines = tuple(tuple(line) for line in parts[start:end])
        source = _restore(_join_lines(block_lines, originals), plan).strip("\n")
        if not source.strip():
            continue
        kind = {
            "paragraph_open": "paragraph", "heading_open": "heading",
            "bullet_list_open": "list", "ordered_list_open": "list",
            "blockquote_open": "quote", "table_open": "table",
            "fence": "code", "code_block": "code",
        }.get(token_type, "other")
        stripped = source.lstrip()
        if stripped.startswith(("```", "~~~")):
            kind = "code"
        elif stripped.startswith(("$$", "\\[")):
            kind = "math"
        elif re.fullmatch(r"!\[[^\]]*\]\([^\n]+\)", source.strip()):
            kind = "image"
        dependencies = frozenset(part[0] for line in block_lines for part in line if not isinstance(part, str))
        # Only used definitions: appending every document reference per block
        # would turn a linear preview into quadratic response bytes.
        references = "\n".join(definitions[label] for label in sorted(labels) if label in definitions)
        blocks.append(PreviewBlock(f"b{len(blocks)}", kind, source, block_lines, dependencies, references))
    if len(blocks) > MAX_PREVIEW_BLOCKS:
        source = _restore(masked, plan)
        blocks = [PreviewBlock("b0", "other", source, tuple(tuple(line) for line in parts),
                               frozenset(range(len(plan.chunks))))]
    return PreviewLayout(tuple(blocks))


def _cursor_done(cursor: str | None, job_id: str, count: int) -> set[int] | None:
    if not cursor or len(cursor) > 512:
        return None
    prefix, separator, bitmap = cursor.partition(":")
    if not separator or prefix != job_id or not re.fullmatch(r"[0-9a-f]+", bitmap):
        return None
    value = int(bitmap, 16)
    if value.bit_length() > count:
        return None
    return {index for index in range(count) if value & (1 << index)}


def project(plan: TranslationPlan, completed: dict[int, str], *, job_id: str,
            input_key: str, cursor: str | None = None) -> dict:
    # Checkpoint bytes may be stale/corrupt despite having a 'completed' count.
    # Never trust the count or a client's cursor to establish block readiness.
    valid: dict[int, list[str]] = {}
    for index, value in completed.items():
        if index < 0 or index >= len(plan.chunks) or not value:
            continue
        try:
            checked = plan.chunks[index].validate(value).split("\n")
            if any(source.strip() and not translated.strip()
                   for source, translated in zip(plan.chunks[index].text.split("\n"), checked)):
                continue
            valid[index] = checked
        except TranslationStructureError:
            continue
    done = set(valid)
    previous = _cursor_done(cursor, job_id, len(plan.chunks))
    reset = previous is None or not previous.issubset(done)
    previous = set() if reset else previous
    layout = layout_for(plan)
    result = []
    for block in layout.blocks:
        ready = block.dependencies.issubset(done)
        if not reset and (not ready or block.dependencies.issubset(previous)):
            continue
        translated = None
        if ready:
            translated = _restore(_join_lines(block.lines, valid), plan).strip("\n")
            if block.references:
                translated += "\n\n" + block.references
        item = {"id": block.id, "translation": translated}
        if reset:
            item.update(source=block.source, kind=block.kind)
        result.append(item)
    bitmap = sum(1 << index for index in done)
    return {
        "job_id": job_id, "input_key": input_key,
        "cursor": f"{job_id}:{bitmap:x}", "reset": reset,
        "total_blocks": len(layout.blocks), "body_complete": len(done) == len(plan.chunks),
        "blocks": result,
    }
