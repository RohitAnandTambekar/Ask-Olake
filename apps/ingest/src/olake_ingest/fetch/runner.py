"""Fetch every configured source and write out/fetch.json for the next stage."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx

from olake_ingest.config import get_settings
from olake_ingest.fetch.git import checkout, resolve_ref
from olake_ingest.fetch.select import Selection, select_files
from olake_ingest.fetch.site import SiteCrawl, crawl_site
from olake_ingest.sources import GitSource, SiteSource


@dataclass
class GitFetch:
    name: str
    repo: str
    ref: str  # as written in sources.yaml
    resolved_ref: str  # concrete branch/tag/SHA that was fetched
    sha: str
    checkout_dir: str
    selection: Selection
    kind: Literal["git"] = "git"


@dataclass
class SiteFetch:
    name: str
    site: str
    fetched_from: str  # same as site, unless SITE_FETCH_BASE points at a local build
    site_dir: str
    crawl: SiteCrawl
    kind: Literal["site"] = "site"


def checkout_dir_for(repo: str) -> Path:
    owner, name = repo.split("/")
    return get_settings().workdir / "repos" / owner / name


def site_dir_for(source_name: str) -> Path:
    # One folder per source, so two sources on the same site never touch each other's pages.
    return get_settings().workdir / "sites" / source_name


def _fetch_git(sources: list[GitSource]) -> list[GitFetch]:
    """Check out each repo once, then select files for every source on it."""
    checked_out: dict[str, tuple[str, str]] = {}  # repo -> (resolved_ref, sha)
    results: list[GitFetch] = []
    for source in sources:
        dest = checkout_dir_for(source.repo)
        if source.repo not in checked_out:
            resolved = resolve_ref(source.clone_url, source.ref)
            checked_out[source.repo] = (resolved, checkout(source.clone_url, resolved, dest))
        resolved, sha = checked_out[source.repo]
        results.append(
            GitFetch(
                name=source.name,
                repo=source.repo,
                ref=source.ref,
                resolved_ref=resolved,
                sha=sha,
                checkout_dir=str(dest),
                selection=select_files(dest, source.include, source.exclude),
            )
        )
    return results


def _fetch_sites(sources: list[SiteSource]) -> list[SiteFetch]:
    settings = get_settings()
    headers = {"user-agent": settings.crawl_user_agent}
    results: list[SiteFetch] = []
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=False) as client:
        for source in sources:
            dest = site_dir_for(source.name)
            fetch_base = settings.site_fetch_base or source.site
            crawl = crawl_site(
                source,
                client,
                dest,
                fetch_base=fetch_base,
                concurrency=settings.crawl_concurrency,
            )
            results.append(
                SiteFetch(
                    name=source.name,
                    site=source.site,
                    fetched_from=fetch_base,
                    site_dir=str(dest),
                    crawl=crawl,
                )
            )
    return results


def fetch_sources(sources: list[GitSource | SiteSource]) -> list[GitFetch | SiteFetch]:
    git = _fetch_git([s for s in sources if isinstance(s, GitSource)])
    sites = _fetch_sites([s for s in sources if isinstance(s, SiteSource)])
    by_name = {r.name: r for r in [*git, *sites]}
    return [by_name[s.name] for s in sources]  # keep the order of sources.yaml


def write_manifest(results: list[GitFetch | SiteFetch]) -> Path:
    """Update out/fetch.json. Sources not fetched this run (`--only`) keep their last entry."""
    outdir = get_settings().outdir
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "fetch.json"
    now = datetime.now(UTC).isoformat(timespec="seconds")
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    entries = {e["name"]: e for e in previous.get("sources", [])}
    for r in results:
        entries[r.name] = {**asdict(r), "fetched_at": now}
    payload = {
        "updated_at": now,
        "sources": list(entries.values()),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
