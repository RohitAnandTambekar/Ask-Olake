"""Parse sitemap.xml files and match URL paths against glob patterns."""

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from defusedxml import DefusedXmlException, ElementTree

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass(frozen=True)
class SitemapEntry:
    path: str  # "/docs/connectors/postgres/"
    lastmod: str | None  # as written in the sitemap, e.g. "2026-10-09T12:00:00.000Z"


@dataclass(frozen=True)
class Sitemap:
    entries: list[SitemapEntry]  # from a <urlset>
    children: list[str]  # paths of nested sitemaps, from a <sitemapindex>


def parse_sitemap(xml: bytes) -> Sitemap:
    """Read a <urlset> or a <sitemapindex>. defusedxml blocks entity-expansion attacks."""
    try:
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise ValueError(f"invalid sitemap XML: {exc}") from exc
    if root.tag == f"{NS}sitemapindex":
        children = [urlsplit(loc.text.strip()).path for loc in root.iter(f"{NS}loc") if loc.text]
        return Sitemap(entries=[], children=children)
    if root.tag != f"{NS}urlset":
        raise ValueError(f"not a sitemap: root element is {root.tag!r}")

    entries: list[SitemapEntry] = []
    for url in root.iter(f"{NS}url"):
        loc = url.find(f"{NS}loc")
        if loc is None or not loc.text:
            continue
        lastmod = url.find(f"{NS}lastmod")
        entries.append(
            SitemapEntry(
                path=urlsplit(loc.text.strip()).path or "/",
                lastmod=lastmod.text.strip() if lastmod is not None and lastmod.text else None,
            )
        )
    return Sitemap(entries=entries, children=[])


@lru_cache(maxsize=256)
def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """`**` = anything (including /), `*` = anything within one path segment."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def path_matches(pattern: str, path: str) -> bool:
    return _pattern_regex(pattern).fullmatch(path) is not None
