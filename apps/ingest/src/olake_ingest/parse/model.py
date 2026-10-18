"""What every parser returns: a document split into sections, plus exact-match symbols."""

from dataclasses import dataclass, field

from olake_shared import SymbolKind


@dataclass
class Section:
    title: str
    locator: str  # heading anchor for pages; symbol or line range for files. Unique per document.
    heading_path: list[str]  # page title first, then ancestor headings, then this heading
    body: str  # Markdown
    anchor: str | None = None  # URL fragment to cite (#anchor), if the section has one
    tab: str | None = None  # label of the tab this section sits in, if any
    line_start: int | None = None  # 1-based, for file sources
    line_end: int | None = None


@dataclass
class ParsedSymbol:
    name: str
    kind: SymbolKind
    line: int
    locator: str  # which section it belongs to


@dataclass
class ParsedDocument:
    path: str  # repo-relative file path, or URL path for site pages
    title: str
    breadcrumb: list[str]
    sections: list[Section]
    symbols: list[ParsedSymbol] = field(default_factory=list)
    description: str | None = None
    updated: str | None = None  # last-modified date if the source states one
