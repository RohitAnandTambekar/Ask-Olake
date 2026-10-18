"""Turn a rendered Docusaurus page into sections of clean Markdown.

Why rendered HTML and not MDX: the HTML already contains shared snippets, React
component output, every tab panel, and the exact heading ids the site uses, so
anchors in citations are correct by construction.

Section boundaries are headings that carry an `id` (h2-h4). Headings without an
id (cards, component titles) are kept as bold text instead of splitting the page.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

from olake_ingest.parse.model import ParsedDocument, Section

SECTION_LEVELS = {"h2": 2, "h3": 3, "h4": 4}
DROP_SELECTORS = [
    "script",
    "style",
    "noscript",
    "svg",
    "button",
    "a.hash-link",
    "nav",
    ".theme-doc-toc-mobile",
    ".theme-doc-footer",
    ".pagination-nav",
    "[class*='admonitionIcon']",
]
ADMONITION_LABELS = {
    "note": "Note",
    "tip": "Tip",
    "info": "Info",
    "warning": "Warning",
    "caution": "Caution",
    "danger": "Danger",
    "important": "Important",
}
ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")
SITE_SUFFIX = re.compile(r"\s*\|\s*[^|]+$")


@dataclass
class _Heading:
    level: int
    title: str
    anchor: str
    tab: str | None


@dataclass
class _Text:
    markdown: str
    tab: str | None


def _clean(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", ZERO_WIDTH.sub("", text))


def _classes(tag: Tag) -> list[str]:
    return tag.get("class") or []


def _is_code_block(tag: Tag) -> bool:
    return tag.name == "pre" or "theme-code-block" in _classes(tag)


def _is_admonition(tag: Tag) -> bool:
    return "theme-admonition" in _classes(tag)


def _is_tabs(tag: Tag) -> bool:
    return "tabs-container" in _classes(tag)


BLOCK_TAGS = {
    "p",
    "ul",
    "ol",
    "table",
    "pre",
    "blockquote",
    "details",
    "figure",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "div",
    "section",
    "img",
    "dl",
}


def _is_block(node: Tag) -> bool:
    return node.name in BLOCK_TAGS


def _inline(node) -> str:
    if isinstance(node, NavigableString):
        return _clean(str(node))
    if not isinstance(node, Tag):
        return ""
    name = node.name
    if name == "br":
        return "\n"
    if name == "img":
        return ""
    if name == "code":
        code = ZERO_WIDTH.sub("", node.get_text())
        return f"`{code}`" if code.strip() else ""
    inner = "".join(_inline(c) for c in node.children)
    if name in ("strong", "b") and inner.strip():
        return f"**{inner.strip()}**"
    if name in ("em", "i") and inner.strip():
        return f"*{inner.strip()}*"
    if name in ("ul", "ol"):  # lists inside table cells or inline contexts
        items = [_inline(li).strip() for li in node.find_all("li", recursive=False)]
        return "; ".join(i for i in items if i)
    return inner


def _paragraph(text: str) -> str:
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _code_block(tag: Tag) -> str:
    container = tag if "theme-code-block" in _classes(tag) else tag.parent
    lang = ""
    for cls in _classes(container) + _classes(tag):
        if cls.startswith("language-"):
            lang = cls.removeprefix("language-")
            break
    title_el = container.select_one(".olake-code__title, [class*='codeBlockTitle']")
    code_el = tag.find("code") or tag
    lines = code_el.select(".token-line")
    if lines:
        code = "\n".join(ZERO_WIDTH.sub("", line.get_text()) for line in lines)
    else:
        code = ZERO_WIDTH.sub("", code_el.get_text())
    code = code.strip("\n")
    title = f"File: {title_el.get_text(strip=True)}\n" if title_el else ""
    return f"{title}```{lang}\n{code}\n```"


def _table(tag: Tag) -> str:
    rows: list[list[str]] = []
    for tr in tag.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        row = [_inline(c).replace("\n", " ").replace("|", "\\|").strip() for c in cells]
        row = [re.sub(r"\s+", " ", c) for c in row]
        if any(row):
            rows.append(row)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _list(tag: Tag, depth: int = 0) -> str:
    ordered = tag.name == "ol"
    start = int(tag.get("start", 1)) if str(tag.get("start", "1")).isdigit() else 1
    indent = "   " * depth
    lines: list[str] = []
    for i, li in enumerate(tag.find_all("li", recursive=False)):
        marker = f"{start + i}." if ordered else "-"
        text_parts: list[str] = []
        nested: list[str] = []
        for child in li.children:
            if isinstance(child, Tag) and child.name in ("ul", "ol"):
                nested.append(_list(child, depth + 1))
            elif isinstance(child, Tag) and (_is_code_block(child) or child.name == "table"):
                block = _code_block(child) if _is_code_block(child) else _table(child)
                nested.append("\n".join(f"{indent}   {line}" for line in block.split("\n")))
            elif isinstance(child, Tag) and (_is_admonition(child) or child.name == "details"):
                nested.append(
                    "\n".join(f"{indent}   {line}" for line in _render(child).split("\n"))
                )
            else:
                text_parts.append(_inline(child))
        text = _paragraph("".join(text_parts)).replace("\n", f"\n{indent}   ")
        lines.append(f"{indent}{marker} {text}".rstrip())
        lines.extend(n for n in nested if n.strip())
    return "\n".join(lines)


def _admonition(tag: Tag) -> str:
    kind = next(
        (
            c.removeprefix("theme-admonition-")
            for c in _classes(tag)
            if c.startswith("theme-admonition-")
        ),
        "note",
    )
    label = ADMONITION_LABELS.get(kind, kind.capitalize())
    heading = tag.select_one("[class*='admonitionHeading']")
    content = tag.select_one("[class*='admonitionContent']") or tag
    title = heading.get_text(" ", strip=True) if heading else ""
    head = f"**{label}: {title}**" if title and title.lower() != kind else f"**{label}:**"
    body = _render_children(content)
    return "\n".join(f"> {line}".rstrip() for line in [head, *body.split("\n")])


def _details(tag: Tag) -> str:
    summary = tag.find("summary")
    title = summary.get_text(" ", strip=True) if summary else "Details"
    if summary:
        summary.extract()
    return f"**{title}**\n\n{_render_children(tag)}".strip()


def _render(tag: Tag) -> str:
    """Markdown for one block-level element (headings become bold text here)."""
    name = tag.name
    if _is_code_block(tag):
        return _code_block(tag)
    if _is_admonition(tag):
        return _admonition(tag)
    if name == "table":
        return _table(tag)
    if name in ("ul", "ol"):
        return _list(tag)
    if name == "details":
        return _details(tag)
    if name == "blockquote":
        inner = _render_children(tag)
        return "\n".join(f"> {line}".rstrip() for line in inner.split("\n"))
    if name == "figure":
        img = tag.find("img")
        caption = tag.find("figcaption")
        alt = (img.get("alt") or "").strip() if img else ""
        text = caption.get_text(" ", strip=True) if caption else alt
        return f"(Image: {text})" if text else ""
    if name in ("hr", "img"):
        return ""
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = _paragraph(_inline(tag))
        return f"**{text}**" if text else ""
    if name == "p":
        # MDX sometimes wraps blocks (e.g. <details>) in a <p>; render those as a container.
        if any(isinstance(c, Tag) and _is_block(c) for c in tag.children):
            return _render_children(tag)
        return _paragraph(_inline(tag))
    if name == "dl":
        return _paragraph(tag.get_text("\n"))
    return _render_children(tag)  # div, section and other containers


def _render_children(tag: Tag) -> str:
    parts: list[str] = []
    inline: list[str] = []

    def flush() -> None:
        text = _paragraph("".join(inline))
        if text:
            parts.append(text)
        inline.clear()

    for child in tag.children:
        if isinstance(child, Tag) and _is_block(child):
            flush()
            md = _render(child)
            if md.strip():
                parts.append(md)
        else:
            inline.append(_inline(child))
    flush()
    return "\n\n".join(parts)


def _tab_panels(container: Tag) -> list[tuple[str, Tag]]:
    labels = [
        li.get_text(" ", strip=True)
        for li in container.select("[role=tab]")
        if li.find_parent(class_="tabs-container") is container
    ]
    panels = [
        p
        for p in container.select("[role=tabpanel]")
        if p.find_parent(class_="tabs-container") is container
    ]
    return list(zip(labels, panels, strict=False))


def _walk(container: Tag, tab: str | None) -> Iterator[_Heading | _Text]:
    inline: list[str] = []

    def flush() -> Iterator[_Text]:
        text = _paragraph("".join(inline))
        inline.clear()
        if text:
            yield _Text(text, tab)

    for child in container.children:
        if not isinstance(child, Tag):
            inline.append(_inline(child))
            continue
        if child.name in SECTION_LEVELS and child.get("id"):
            yield from flush()
            title = _paragraph(_inline(child)).replace("\n", " ")
            yield _Heading(SECTION_LEVELS[child.name], title, child["id"], tab)
        elif _is_tabs(child):
            yield from flush()
            for label, panel in _tab_panels(child):
                yield _Text(f"**[Tab: {label}]**", label)
                yield from _walk(panel, label)
        elif child.name in ("div", "section") and not (
            _is_code_block(child) or _is_admonition(child)
        ):
            yield from flush()
            yield from _walk(child, tab)  # plain wrapper: look inside for headings
        elif _is_block(child):
            yield from flush()
            md = _render(child)
            if md.strip():
                yield _Text(md, tab)
        else:
            inline.append(_inline(child))
    yield from flush()


def parse_page(html: str, path: str) -> ParsedDocument:
    soup = BeautifulSoup(html, "lxml")
    root = (
        soup.select_one(".theme-doc-markdown")
        or soup.select_one("article .markdown")
        or soup.select_one("main article")
    )
    if root is None:
        raise ValueError(f"{path}: no article content found")

    h1 = soup.select_one("article h1") or root.find("h1")
    page_title = h1.get_text(" ", strip=True) if h1 else ""
    if not page_title and soup.title:
        page_title = SITE_SUFFIX.sub("", soup.title.get_text(strip=True))
    crumbs_nav = soup.select_one("nav.theme-doc-breadcrumbs, nav[aria-label=Breadcrumbs]")
    breadcrumb = [
        li.get_text(" ", strip=True)
        for li in (crumbs_nav.select("li") if crumbs_nav else [])
        if li.get_text(strip=True)
    ]
    description_el = soup.select_one("meta[name=description]")
    time_el = soup.select_one("article time[datetime]") or soup.select_one("time[datetime]")

    for selector in DROP_SELECTORS:
        for el in root.select(selector):
            el.decompose()
    for h in root.find_all("h1"):
        h.decompose()

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    current = Section(title=page_title, locator="_top", heading_path=[page_title], body="")
    parts: list[str] = []

    def close() -> None:
        current.body = "\n\n".join(parts).strip()
        sections.append(current)
        parts.clear()

    for block in _walk(root, None):
        if isinstance(block, _Heading):
            close()
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.title))
            current = Section(
                title=block.title,
                locator=block.anchor,
                anchor=block.anchor,
                heading_path=[page_title, *(t for _, t in stack)],
                body="",
                tab=block.tab,
            )
        else:
            parts.append(block.markdown)
    close()

    return ParsedDocument(
        path=path,
        title=page_title,
        breadcrumb=breadcrumb,
        sections=[s for s in sections if s.body],
        description=description_el.get("content") if description_el else None,
        updated=time_el.get("datetime") if time_el else None,
    )
