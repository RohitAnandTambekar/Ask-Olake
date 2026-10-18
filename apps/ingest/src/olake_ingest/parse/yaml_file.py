"""Split YAML (Helm values, compose files, server config) by key, keeping comments.

Comments in Helm values files are the documentation, so the split is line-based:
a top-level key plus the comment lines directly above it form one section. Large
top-level blocks are split again at their second-level keys.
"""

import re

from olake_ingest.parse.model import ParsedDocument, ParsedSymbol, Section
from olake_shared import SymbolKind

KEY = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z0-9_.\-\"']+)\s*:(\s|$)")
MAX_LINES = 120


def _blocks(lines: list[str], indent: int, start: int, end: int) -> list[tuple[int, int, str]]:
    """(first line, end line exclusive, key) for each key at exactly `indent` spaces."""
    keys: list[tuple[int, str]] = []
    for i in range(start, end):
        m = KEY.match(lines[i])
        if m and len(m["indent"]) == indent:
            # pull directly-preceding comment lines into this block
            j = i
            while j - 1 >= start and lines[j - 1].strip().startswith("#"):
                j -= 1
            keys.append((j, m["key"].strip("\"'")))
    if not keys:
        return [(start, end, "")]
    blocks = []
    if keys[0][0] > start:
        blocks.append((start, keys[0][0], ""))  # header comments before the first key
    for n, (s, key) in enumerate(keys):
        e = keys[n + 1][0] if n + 1 < len(keys) else end
        blocks.append((s, e, key))
    return blocks


def parse_yaml(text: str, path: str) -> ParsedDocument:
    lines = text.splitlines()
    sections: list[Section] = []
    symbols: list[ParsedSymbol] = []

    def add(s: int, e: int, key_path: list[str]) -> None:
        body = "\n".join(lines[s:e]).strip("\n")
        if not body.strip():
            return
        title = ".".join(key_path) or "(file header)"
        locator = f"L{s + 1}"
        sections.append(
            Section(
                title=title,
                locator=locator,
                heading_path=[path, title],
                body=f"```yaml\n{body}\n```",
                line_start=s + 1,
                line_end=e,
            )
        )

    for s, e, key in _blocks(lines, 0, 0, len(lines)):
        if key:
            symbols.append(ParsedSymbol(key, SymbolKind.CONFIG_KEY, s + 1, f"L{s + 1}"))
        if e - s <= MAX_LINES or not key:
            add(s, e, [key] if key else [])
            continue
        child_indent = next(
            (
                len(m["indent"])
                for i in range(s + 1, e)
                if (m := KEY.match(lines[i])) and m["indent"]
            ),
            2,
        )
        for cs, ce, child in _blocks(lines, child_indent, s, e):
            if child:
                dotted = f"{key}.{child}"
                symbols.append(ParsedSymbol(dotted, SymbolKind.CONFIG_KEY, cs + 1, f"L{cs + 1}"))
            add(cs, ce, [key, child] if child else [key])

    return ParsedDocument(path=path, title=path, breadcrumb=[], sections=sections, symbols=symbols)
