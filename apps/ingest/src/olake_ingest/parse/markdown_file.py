"""Split a Markdown file (README, Fusion docs, API contract) into sections by heading."""

from markdown_it import MarkdownIt

from olake_ingest.parse.model import ParsedDocument, Section

_md = MarkdownIt("commonmark").enable("table")
MAX_LEVEL = 4


def parse_markdown(text: str, path: str) -> ParsedDocument:
    lines = text.splitlines()
    tokens = _md.parse(text)

    # (line index, level, title) for every heading, in order.
    headings: list[tuple[int, int, str]] = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map and int(tok.tag[1]) <= MAX_LEVEL:
            title = tokens[i + 1].content.strip() if i + 1 < len(tokens) else ""
            headings.append((tok.map[0], int(tok.tag[1]), title))

    h1 = next((t for _, lvl, t in headings if lvl == 1), None)
    doc_title = h1 or path.rsplit("/", 1)[-1]

    starts = [0] + [line for line, _, _ in headings]
    bounds = list(zip(starts, starts[1:] + [len(lines)], strict=True))
    meta = [(1, doc_title)] + [(lvl, title) for _, lvl, title in headings]

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    for (start, end), (level, title) in zip(bounds, meta, strict=True):
        is_heading = start in {h[0] for h in headings}
        body_lines = lines[start + 1 : end] if is_heading else lines[start:end]
        body = "\n".join(body_lines).strip()
        if is_heading and level > 1:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        heading_path = (
            [doc_title, *(t for _, t in stack)] if is_heading and level > 1 else [doc_title]
        )
        if not body:
            continue
        sections.append(
            Section(
                title=title,
                locator=f"L{start + 1}",
                heading_path=heading_path,
                body=body,
                line_start=start + 1,
                line_end=end,
            )
        )
    return ParsedDocument(path=path, title=doc_title, breadcrumb=[], sections=sections)
