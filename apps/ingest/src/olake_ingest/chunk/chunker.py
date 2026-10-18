"""Turn parsed sections into retrieval-sized pieces.

- Sections larger than MAX_TOKENS are split at block boundaries (blank lines), never
  inside a fenced code block. A single oversized block is split by table rows
  (header repeated), code lines (fences re-added) or sentences.
- Neighbouring sections smaller than MIN_TOKENS are merged when they share a parent
  heading and tab, so a three-line function or a one-sentence heading is not a chunk
  on its own.

Token counts are estimated as characters / 4: good enough for sizing, and needs no
tokenizer download.
"""

import re
from dataclasses import dataclass, field

from olake_ingest.parse.model import Section

MAX_TOKENS = 700
MIN_TOKENS = 120
FENCE = re.compile(r"^\s*(```|~~~)")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class Piece:
    """One future chunk: a section (or part of one, or several merged)."""

    title: str
    locator: str
    heading_path: list[str]
    body: str
    anchor: str | None
    tab: str | None
    line_start: int | None
    line_end: int | None
    locators: list[str] = field(default_factory=list)  # every section locator it covers


def _blocks(body: str) -> list[str]:
    """Split on blank lines, keeping fenced code blocks whole."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in body.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _split_block(block: str, limit_chars: int) -> list[str]:
    lines = block.split("\n")
    if lines[0].lstrip().startswith("|") and len(lines) > 2:  # Markdown table
        header, rows = lines[:2], lines[2:]
        parts, current = [], []
        for row in rows:
            if current and len("\n".join(header + current + [row])) > limit_chars:
                parts.append("\n".join(header + current))
                current = []
            current.append(row)
        parts.append("\n".join(header + current))
        return parts
    fence_open = next((i for i, ln in enumerate(lines) if FENCE.match(ln)), None)
    if fence_open is not None and FENCE.match(lines[-1]):  # fenced code
        prefix = lines[: fence_open + 1]
        code = lines[fence_open + 1 : -1]
        parts, current = [], []
        for ln in code:
            if current and len("\n".join(prefix + current + [ln, "```"])) > limit_chars:
                parts.append("\n".join(prefix + current + ["```"]))
                current = []
            current.append(ln)
        parts.append("\n".join(prefix + current + ["```"]))
        return parts
    sentences = re.split(r"(?<=[.!?])\s+", block)  # prose
    parts, current = [], ""
    for s in sentences:
        if current and len(current) + len(s) + 1 > limit_chars:
            parts.append(current)
            current = ""
        current = f"{current} {s}".strip()
    if current:
        parts.append(current)
    # a single "sentence" can still be huge (minified text); hard-wrap as a last resort
    return [p[i : i + limit_chars] for p in parts for i in range(0, len(p), limit_chars)]


def _split_section(section: Section) -> list[Piece]:
    limit = MAX_TOKENS * 4
    if len(section.body) <= limit:
        bodies = [section.body]
    else:
        bodies, current = [], ""
        for block in _blocks(section.body):
            pieces = [block] if len(block) <= limit else _split_block(block, limit)
            for p in pieces:
                if current and len(current) + len(p) + 2 > limit:
                    bodies.append(current)
                    current = ""
                current = f"{current}\n\n{p}" if current else p
        if current:
            bodies.append(current)

    total = len(bodies)
    return [
        Piece(
            title=section.title if total == 1 else f"{section.title} (part {i}/{total})",
            locator=section.locator if total == 1 else f"{section.locator}~{i}",
            heading_path=section.heading_path,
            body=body,
            anchor=section.anchor,
            tab=section.tab,
            line_start=section.line_start,
            line_end=section.line_end,
            locators=[section.locator],
        )
        for i, body in enumerate(bodies, start=1)
    ]


def _can_merge(a: Piece, b: Piece) -> bool:
    if a.tab != b.tab:
        return False
    if estimate_tokens(a.body) >= MIN_TOKENS and estimate_tokens(b.body) >= MIN_TOKENS:
        return False
    if estimate_tokens(a.body) + estimate_tokens(b.body) > MAX_TOKENS:
        return False
    parent_a, parent_b = a.heading_path[:-1], b.heading_path[:-1]
    # siblings, or b is a child of a
    return parent_a == parent_b or b.heading_path[: len(a.heading_path)] == a.heading_path


def chunk_sections(sections: list[Section]) -> list[Piece]:
    pieces = [p for s in sections for p in _split_section(s) if p.body.strip()]
    merged: list[Piece] = []
    for piece in pieces:
        last = merged[-1] if merged else None
        if (
            last is not None
            and "~" not in last.locator
            and "~" not in piece.locator
            and _can_merge(last, piece)
        ):
            last.body = f"{last.body}\n\n**{piece.title}**\n\n{piece.body}"
            last.locators.extend(piece.locators)
            if last.line_start is not None and piece.line_end is not None:
                last.line_end = piece.line_end
            continue
        merged.append(piece)
    return merged
