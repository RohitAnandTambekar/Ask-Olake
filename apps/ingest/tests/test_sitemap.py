import pytest

from olake_ingest.fetch.sitemap import SitemapEntry, parse_sitemap, path_matches

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://olake.io/docs/</loc><lastmod>2026-10-09T12:00:00.000Z</lastmod></url>
  <url><loc> https://olake.io/docs/connectors/postgres/ </loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://olake.io/sitemap-docs.xml</loc></sitemap>
</sitemapindex>"""


def test_parse_urlset_keeps_paths_and_lastmod():
    sitemap = parse_sitemap(URLSET)
    assert sitemap.entries == [
        SitemapEntry("/docs/", "2026-10-09T12:00:00.000Z"),
        SitemapEntry("/docs/connectors/postgres/", None),
    ]
    assert sitemap.children == []


def test_parse_sitemap_index_returns_children():
    assert parse_sitemap(INDEX).children == ["/sitemap-docs.xml"]


def test_rejects_non_sitemap_xml():
    with pytest.raises(ValueError, match="not a sitemap"):
        parse_sitemap(b"<html></html>")


def test_rejects_entity_expansion_attack():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset>&a;</urlset>'
    with pytest.raises(ValueError, match="invalid sitemap XML"):  # defusedxml refuses it
        parse_sitemap(bomb)


def test_rejects_broken_xml():
    with pytest.raises(ValueError, match="invalid sitemap XML"):
        parse_sitemap(b"<urlset><url>")


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/docs/**", "/docs/", True),
        ("/docs/**", "/docs/connectors/postgres/", True),
        ("/docs/**", "/blog/x/", False),
        ("/blog/*/", "/blog/why-iceberg/", True),
        ("/blog/*/", "/blog/", False),  # the listing page itself
        ("/blog/*/", "/blog/2024/05/x/", False),  # * stays within one segment
        ("/docs/archive/**", "/docs/archive/kubernetes/", True),
        ("/docs/a.b/", "/docs/aXb/", False),  # dots are literal, not regex wildcards
    ],
)
def test_path_matches(pattern: str, path: str, expected: bool):
    assert path_matches(pattern, path) is expected
