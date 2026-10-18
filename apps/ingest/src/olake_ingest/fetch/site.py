"""Mirror a website's pages to disk by following its sitemap.

Polite by design: honours robots.txt, sends a clear User-Agent, limits parallelism,
and skips pages whose sitemap `lastmod` has not changed since the last crawl.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.robotparser import RobotFileParser

import httpx

from olake_ingest.fetch.sitemap import SitemapEntry, parse_sitemap, path_matches
from olake_ingest.sources import SiteSource

MAX_PAGE_BYTES = 5_000_000
MAX_NESTED_SITEMAPS = 20
RETRIES = 3
STATE_FILE = "_crawl_state.json"


@dataclass
class PageRecord:
    path: str  # "/docs/connectors/postgres/"
    url: str  # public URL used for citations: site + path
    lastmod: str | None
    file: str  # where the HTML is saved, relative to the site folder
    status: str  # "downloaded" or "unchanged"


@dataclass
class SiteCrawl:
    pages: list[PageRecord] = field(default_factory=list)
    excluded: int = 0  # in the sitemap and matched include, but also matched exclude
    disallowed: list[str] = field(default_factory=list)  # blocked by robots.txt
    failed: dict[str, str] = field(default_factory=dict)  # path -> reason
    removed: list[str] = field(default_factory=list)  # saved before, gone from the sitemap now
    empty_patterns: list[str] = field(default_factory=list)  # include patterns matching nothing


class CrawlError(RuntimeError):
    pass


def page_file(path: str) -> str:
    """Map a URL path to a safe relative file path: /docs/x/ -> docs/x/index.html."""
    if not path.startswith("/") or "\\" in path or "//" in path:
        raise ValueError(f"unsafe URL path: {path!r}")
    parts = [p for p in path.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise ValueError(f"unsafe URL path: {path!r}")
    if parts and parts[-1].endswith(".html"):
        return "/".join(parts)
    return "/".join([*parts, "index.html"])


def _get(client: httpx.Client, url: str) -> httpx.Response:
    """GET with retries on network errors, 429 and 5xx. Redirects are not followed."""
    last_error = ""
    for attempt in range(RETRIES):
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code != 429 and response.status_code < 500:
                return response
            last_error = f"HTTP {response.status_code}"
        if attempt < RETRIES - 1:
            time.sleep(2**attempt)  # 1s, then 2s
    raise CrawlError(f"{url}: {last_error} after {RETRIES} attempts")


def _load_robots(client: httpx.Client, base: str) -> RobotFileParser:
    robots = RobotFileParser()
    response = _get(client, f"{base}/robots.txt")
    if response.status_code == 200:
        robots.parse(response.text.splitlines())
    else:
        robots.allow_all = True  # no robots.txt means no restrictions
    return robots


def _load_sitemap(client: httpx.Client, base: str, path: str) -> list[SitemapEntry]:
    response = _get(client, f"{base}{path}")
    if response.status_code != 200:
        raise CrawlError(f"sitemap {path}: HTTP {response.status_code}")
    try:
        sitemap = parse_sitemap(response.content)
        entries = list(sitemap.entries)
        for child in sitemap.children[:MAX_NESTED_SITEMAPS]:  # one level of sitemap index
            entries.extend(parse_sitemap(_get(client, f"{base}{child}").content).entries)
    except ValueError as exc:
        raise CrawlError(f"sitemap {path}: {exc}") from exc
    return entries


def _download(client: httpx.Client, base: str, path: str, dest: Path) -> None:
    response = _get(client, f"{base}{path}")
    if response.is_redirect:
        raise CrawlError(f"redirected to {response.headers.get('location')}")
    if response.status_code != 200:
        raise CrawlError(f"HTTP {response.status_code}")
    if not response.headers.get("content-type", "").startswith("text/html"):
        raise CrawlError(f"not HTML: {response.headers.get('content-type')!r}")
    if len(response.content) > MAX_PAGE_BYTES:
        raise CrawlError(f"page larger than {MAX_PAGE_BYTES} bytes")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(response.content)
    tmp.replace(dest)  # atomic: a crash never leaves half a page behind


def crawl_site(
    source: SiteSource,
    client: httpx.Client,
    site_dir: Path,
    fetch_base: str | None = None,
    concurrency: int = 4,
) -> SiteCrawl:
    """Mirror the pages `source` selects into `site_dir`. Returns what happened per page."""
    base = (fetch_base or source.site).rstrip("/")
    site_dir.mkdir(parents=True, exist_ok=True)
    state_path = site_dir / STATE_FILE
    previous: dict[str, dict] = (
        json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    )

    robots = _load_robots(client, base)
    entries = _load_sitemap(client, base, source.sitemap)
    agent = client.headers.get("user-agent", "*")

    crawl = SiteCrawl()
    matched_patterns: set[str] = set()
    wanted: list[SitemapEntry] = []
    for entry in sorted(set(entries), key=lambda e: e.path):
        hits = [p for p in source.include if path_matches(p, entry.path)]
        if not hits:
            continue
        matched_patterns.update(hits)
        if any(path_matches(p, entry.path) for p in source.exclude):
            crawl.excluded += 1
        elif not robots.can_fetch(agent, f"{source.site}{entry.path}"):
            crawl.disallowed.append(entry.path)
        else:
            wanted.append(entry)
    crawl.empty_patterns = [p for p in source.include if p not in matched_patterns]

    def handle(entry: SitemapEntry) -> PageRecord | tuple[str, str]:
        try:
            rel = page_file(entry.path)
            dest = site_dir / rel
            before = previous.get(entry.path)
            unchanged = (
                before is not None
                and entry.lastmod is not None
                and before.get("lastmod") == entry.lastmod
                and dest.exists()
            )
            if not unchanged:
                _download(client, base, entry.path, dest)
            return PageRecord(
                path=entry.path,
                url=f"{source.site}{entry.path}",
                lastmod=entry.lastmod,
                file=rel,
                status="unchanged" if unchanged else "downloaded",
            )
        except (CrawlError, ValueError, OSError) as exc:
            return entry.path, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for result in pool.map(handle, wanted):
            if isinstance(result, PageRecord):
                crawl.pages.append(result)
            else:
                crawl.failed[result[0]] = result[1]

    # Pages that left the sitemap are deleted, so the mirror matches the live site.
    current = {p.path for p in crawl.pages} | set(crawl.failed)
    for old_path, info in previous.items():
        if old_path not in current:
            (site_dir / info["file"]).unlink(missing_ok=True)
            crawl.removed.append(old_path)

    # Failed pages keep their old state, so a later run retries them.
    state = {p.path: {"lastmod": p.lastmod, "file": p.file} for p in crawl.pages}
    state.update({p: previous[p] for p in crawl.failed if p in previous})
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return crawl
