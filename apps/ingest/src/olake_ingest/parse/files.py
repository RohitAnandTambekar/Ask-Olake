"""Pick a parser by file type."""

from olake_ingest.parse.go_code import parse_go
from olake_ingest.parse.markdown_file import parse_markdown
from olake_ingest.parse.model import ParsedDocument, Section
from olake_ingest.parse.yaml_file import parse_yaml

PLAIN_WINDOW = 120


def _parse_plain(text: str, path: str) -> ParsedDocument:
    lines = text.splitlines()
    sections = []
    for s in range(0, max(len(lines), 1), PLAIN_WINDOW):
        e = min(s + PLAIN_WINDOW, len(lines))
        body = "\n".join(lines[s:e]).strip()
        if body:
            sections.append(
                Section(
                    title=path,
                    locator=f"L{s + 1}",
                    heading_path=[path],
                    body=f"```\n{body}\n```",
                    line_start=s + 1,
                    line_end=e,
                )
            )
    return ParsedDocument(path=path, title=path, breadcrumb=[], sections=sections)


def parse_file(text: str, path: str) -> ParsedDocument:
    lower = path.lower()
    if lower.endswith(".go"):
        return parse_go(text, path)
    if lower.endswith((".md", ".markdown")):
        return parse_markdown(text, path)
    if lower.endswith((".yaml", ".yml")):
        return parse_yaml(text, path)
    return _parse_plain(text, path)
