"""Crawler tests against a fake website held in memory (httpx.MockTransport)."""

import json
from pathlib import Path

import httpx
import pytest

from olake_ingest.fetch import site as site_module
from olake_ingest.fetch.site import STATE_FILE, CrawlError, crawl_site, page_file
from olake_ingest.sources import SiteSource


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(site_module.time, "sleep", lambda _s: None)  # keep retries instant


class FakeSite:
    """Serves robots.txt, a sitemap and pages; records every request it receives."""

    def __init__(self):
        self.lastmod = {"/docs/": "2026-01-01", "/docs/a/": "2026-01-01", "/docs/secret/": None}
        self.robots = "User-agent: *\nDisallow: /docs/secret/\n"
        self.pages = {path: f"<html>{path}</html>" for path in self.lastmod}
        self.pages["/blog/x/"] = "<html>blog</html>"
        self.overrides: dict[str, httpx.Response] = {}
        self.requests: list[str] = []

    def sitemap(self) -> str:
        urls = "".join(
            f"<url><loc>https://olake.io{p}</loc>"
            + (f"<lastmod>{m}</lastmod>" if m else "")
            + "</url>"
            for p, m in [*self.lastmod.items(), ("/blog/x/", None)]
        )
        return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path in self.overrides:
            return self.overrides[path]
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if path == "/sitemap.xml":
            return httpx.Response(200, text=self.sitemap())
        if path in self.pages:
            return httpx.Response(200, html=self.pages[path])
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self.handler), headers={"user-agent": "test-bot"}
        )


def _source(**overrides) -> SiteSource:
    base = {
        "name": "docs",
        "kind": "site",
        "site": "https://olake.io",
        "product": "auto",
        "source_type": "auto",
        "trust_tier": 1,
        "include": ["/docs/**"],
    }
    base.update(overrides)
    return SiteSource.model_validate(base)


def test_first_crawl_downloads_selected_pages(tmp_path: Path):
    fake = FakeSite()
    crawl = crawl_site(_source(), fake.client(), tmp_path)

    assert [(p.path, p.status) for p in crawl.pages] == [
        ("/docs/", "downloaded"),
        ("/docs/a/", "downloaded"),
    ]
    assert crawl.disallowed == ["/docs/secret/"]  # robots.txt is honoured
    assert "/docs/secret/" not in fake.requests
    assert "/blog/x/" not in fake.requests  # not included, never fetched
    assert (tmp_path / "docs/a/index.html").read_text() == "<html>/docs/a/</html>"
    assert crawl.pages[1].url == "https://olake.io/docs/a/"


def test_second_crawl_skips_unchanged_pages(tmp_path: Path):
    fake = FakeSite()
    crawl_site(_source(), fake.client(), tmp_path)
    fake.requests.clear()

    fake.lastmod["/docs/a/"] = "2026-02-02"  # only this page changed
    crawl = crawl_site(_source(), fake.client(), tmp_path)

    assert {p.path: p.status for p in crawl.pages} == {
        "/docs/": "unchanged",
        "/docs/a/": "downloaded",
    }
    assert fake.requests == ["/robots.txt", "/sitemap.xml", "/docs/a/"]


def test_pages_without_lastmod_are_always_refetched(tmp_path: Path):
    fake = FakeSite()
    fake.robots = ""
    crawl_site(_source(), fake.client(), tmp_path)
    fake.requests.clear()
    crawl_site(_source(), fake.client(), tmp_path)
    assert "/docs/secret/" in fake.requests  # no lastmod, so we can't know it's unchanged


def test_pages_gone_from_sitemap_are_deleted(tmp_path: Path):
    fake = FakeSite()
    crawl_site(_source(), fake.client(), tmp_path)
    del fake.lastmod["/docs/a/"]

    crawl = crawl_site(_source(), fake.client(), tmp_path)

    assert crawl.removed == ["/docs/a/"]
    assert not (tmp_path / "docs/a/index.html").exists()
    assert "/docs/a/" not in json.loads((tmp_path / STATE_FILE).read_text())


def test_exclude_and_empty_include_patterns_are_reported(tmp_path: Path):
    source = _source(include=["/docs/**", "/learn/*/"], exclude=["/docs/a/"])
    crawl = crawl_site(source, FakeSite().client(), tmp_path)
    assert crawl.excluded == 1
    assert crawl.empty_patterns == ["/learn/*/"]


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(503), "HTTP 503 after 3 attempts"),
        (httpx.Response(200, json={"a": 1}), "not HTML"),
        (httpx.Response(301, headers={"location": "https://evil.example/"}), "redirected"),
    ],
)
def test_bad_pages_fail_without_stopping_the_crawl(tmp_path: Path, response, reason):
    fake = FakeSite()
    fake.overrides["/docs/a/"] = response
    crawl = crawl_site(_source(), fake.client(), tmp_path)

    assert reason in crawl.failed["/docs/a/"]
    assert [p.path for p in crawl.pages] == ["/docs/"]  # the other page still made it


def test_failed_page_keeps_old_copy_and_is_retried_next_time(tmp_path: Path):
    fake = FakeSite()
    crawl_site(_source(), fake.client(), tmp_path)

    fake.lastmod["/docs/a/"] = "2026-03-03"
    fake.overrides["/docs/a/"] = httpx.Response(500)
    crawl = crawl_site(_source(), fake.client(), tmp_path)
    assert "/docs/a/" in crawl.failed
    assert (tmp_path / "docs/a/index.html").exists()  # old copy kept, not deleted

    del fake.overrides["/docs/a/"]
    crawl = crawl_site(_source(), fake.client(), tmp_path)
    assert {p.path: p.status for p in crawl.pages}["/docs/a/"] == "downloaded"


def test_broken_sitemap_is_a_clean_crawl_error(tmp_path: Path):
    fake = FakeSite()
    fake.overrides["/sitemap.xml"] = httpx.Response(200, text="<urlset><url>")
    with pytest.raises(CrawlError, match="invalid sitemap XML"):
        crawl_site(_source(), fake.client(), tmp_path)


def test_missing_robots_txt_allows_everything(tmp_path: Path):
    fake = FakeSite()
    fake.overrides["/robots.txt"] = httpx.Response(404)
    crawl = crawl_site(_source(), fake.client(), tmp_path)
    assert crawl.disallowed == []
    assert len(crawl.pages) == 3


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/docs/", "docs/index.html"),
        ("/docs/connectors/postgres/", "docs/connectors/postgres/index.html"),
        ("/", "index.html"),
        ("/404.html", "404.html"),
    ],
)
def test_page_file_mapping(path: str, expected: str):
    assert page_file(path) == expected


@pytest.mark.parametrize("path", ["docs/x/", "/docs/../../etc/", "/docs//x/", "/docs\\x/"])
def test_page_file_rejects_unsafe_paths(path: str):
    with pytest.raises(ValueError, match="unsafe"):
        page_file(path)
