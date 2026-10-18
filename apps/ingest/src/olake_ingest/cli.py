"""Command-line entry point for the ingestion pipeline.

migrate   create/upgrade the database schema
doctor    check settings and database health
fetch     check out repos and crawl the docs site          -> out/fetch.json
build     parse + chunk everything that was fetched         -> out/chunks.jsonl
inspect   write a page for checking chunks by eye           -> out/viewer.html
index     embed chunks and load a new index version, then activate it
run       fetch + build + index in one go (what the nightly job runs)
"""

from typing import Annotated

import psycopg
import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from olake_ingest.config import get_settings
from olake_ingest.fetch.git import GitError
from olake_ingest.fetch.runner import GitFetch, SiteFetch, fetch_sources, write_manifest
from olake_ingest.fetch.site import CrawlError
from olake_ingest.inspect.viewer import write_viewer
from olake_ingest.pipeline.build import build as build_chunks
from olake_ingest.pipeline.build import read_chunks, read_symbols
from olake_ingest.sources import load_sources
from olake_ingest.store import db
from olake_ingest.store.index import IndexBuildError, active_version, load_index
from olake_shared.embeddings import EmbeddingError, make_embedder

app = typer.Typer(
    help="Ingest OLake docs and source code into the search index.", no_args_is_help=True
)
console = Console()

REQUIRED_TABLES = {"index_versions", "chunks", "symbols", "embedding_cache", "query_log"}


@app.command()
def migrate() -> None:
    """Create or upgrade the database schema."""
    applied = db.apply_migrations()
    if applied:
        for name in applied:
            console.print(f"[green]applied[/green] {name}")
    else:
        console.print("Schema is up to date.")


@app.command()
def doctor() -> None:
    """Check settings and database health before running the pipeline."""
    settings = get_settings()
    try:
        info = db.health()
    except psycopg.OperationalError as exc:  # refused, bad credentials, unknown db
        console.print(f"[red]Database unreachable:[/red] {exc}")
        raise typer.Exit(code=1)

    table = Table(show_header=False)
    table.add_row("Postgres", info["server_version"])
    table.add_row("pgvector", info["pgvector"] or "[red]not installed — run migrate[/red]")
    table.add_row("Tables", ", ".join(info["tables"]) or "[yellow]none — run migrate[/yellow]")
    table.add_row(
        "Embeddings",
        f"{settings.embedding_provider}: {settings.embedding_model} ({settings.embedding_dims} dims)",
    )
    console.print(table)

    missing = REQUIRED_TABLES - set(info["tables"])
    if not info["pgvector"] or missing:
        console.print(
            f"[red]Schema not ready[/red] (missing: {sorted(missing) or 'pgvector'}). Run migrate."
        )
        raise typer.Exit(code=1)

    mismatched = {col: n for col, n in info["vector_dims"].items() if n != settings.embedding_dims}
    if mismatched:
        console.print(
            f"[red]Vector size mismatch:[/red] columns {mismatched} vs "
            f"EMBEDDING_DIMS={settings.embedding_dims}. Fix the migration or the setting."
        )
        raise typer.Exit(code=1)

    version = active_version()
    if version is None:
        console.print("[yellow]No active index yet: run `olake-ingest run`.[/yellow]")
    else:
        console.print(
            f"Active index: version {version['id']} ({version['model']}), "
            f"{(version['manifest'] or {}).get('chunks', '?')} chunks, built {version['created_at']:%Y-%m-%d %H:%M}"
        )
        if version["model"] != _embedder_model_name():
            console.print(
                f"[red]Active index was built with {version['model']}, but settings say "
                f"{_embedder_model_name()}. Re-run `olake-ingest index`.[/red]"
            )
            raise typer.Exit(code=1)
    console.print("[green]All checks passed.[/green]")


def _embedder_model_name() -> str:
    s = get_settings()
    return (
        s.embedding_model if s.embedding_provider == "openai" else f"hashing-v1-{s.embedding_dims}"
    )


def _fetch(only: list[str] | None) -> bool:
    sources = load_sources(only=only)
    try:
        results = fetch_sources(sources)
    except (GitError, CrawlError) as exc:
        console.print(f"[red]Fetch failed:[/red] {exc}")
        return False

    problems: list[str] = []
    git = [r for r in results if isinstance(r, GitFetch)]
    sites = [r for r in results if isinstance(r, SiteFetch)]

    if git:
        table = Table("Source", "Repo", "Ref", "Commit", "Files", "Excluded", "Skipped")
        for r in git:
            sel = r.selection
            ref = r.ref if r.ref == r.resolved_ref else f"{r.ref} → {r.resolved_ref}"
            skipped = len(sel.skipped_large) + len(sel.skipped_symlinks)
            repo = r.repo.split("/")[1]
            table.add_row(
                r.name, repo, ref, r.sha[:7], str(len(sel.files)), str(sel.excluded), str(skipped)
            )
            if not sel.files:
                problems.append(f"{r.name}: no files selected")
            for pattern in sel.empty_patterns:
                problems.append(f"{r.name}: include pattern {pattern!r} matched nothing")
        console.print(table)

    if sites:
        table = Table("Source", "Site", "Pages", "New", "Same", "Excluded", "Blocked", "Removed")
        for r in sites:
            c = r.crawl
            downloaded = sum(p.status == "downloaded" for p in c.pages)
            table.add_row(
                r.name,
                r.site.removeprefix("https://"),
                str(len(c.pages)),
                str(downloaded),
                str(len(c.pages) - downloaded),
                str(c.excluded),
                str(len(c.disallowed)),
                str(len(c.removed)),
            )
            if not c.pages:
                problems.append(f"{r.name}: no pages selected")
            for pattern in c.empty_patterns:
                problems.append(f"{r.name}: include pattern {pattern!r} matched nothing")
            for path, reason in c.failed.items():
                problems.append(f"{r.name}: {path} failed: {reason}")
        console.print(table)
        for r in sites:
            if r.fetched_from != r.site:
                console.print(f"[yellow]{r.name}: fetched from {r.fetched_from}[/yellow]")

    console.print(f"Manifest written to {write_manifest(results)}")
    for p in problems:
        console.print(f"[red]✗[/red] {p}")
    return not problems


def _build() -> bool:
    try:
        stats, path = build_chunks()
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return False
    table = Table("Source", "Documents", "Sections", "Chunks", "Symbols", "≈Tokens", "Errors")
    for s in stats:
        table.add_row(
            s.name,
            str(s.documents),
            str(s.sections),
            str(s.chunks),
            str(s.symbols),
            f"{s.tokens:,}",
            str(len(s.errors)),
        )
    console.print(table)
    console.print(f"Chunks written to {path}")
    ok = True
    for s in stats:
        for where, err in list(s.errors.items())[:10]:
            console.print(f"[red]✗[/red] {s.name}: {where}: {err}")
            ok = False
        if s.chunks == 0:
            console.print(f"[red]✗[/red] {s.name}: produced no chunks")
            ok = False
    return ok


def _index(activate: bool) -> bool:
    settings = get_settings()
    out = settings.outdir
    try:
        embedder = make_embedder(
            settings.embedding_provider,
            settings.embedding_model,
            settings.embedding_dims,
            settings.openai_api_key,
        )
        chunks = read_chunks(out / "chunks.jsonl")
        symbols = read_symbols(out / "symbols.jsonl")
        with Progress(console=console, transient=True) as progress:
            task = progress.add_task("Embedding new chunks", total=None)

            def update(done: int, total: int) -> None:
                progress.update(task, completed=done, total=total)

            result = load_index(
                chunks,
                symbols,
                embedder,
                batch_size=settings.embed_batch_size,
                activate=activate,
                progress=update,
            )
    except (EmbeddingError, IndexBuildError, FileNotFoundError) as exc:
        console.print(f"[red]Index failed:[/red] {exc}")
        return False
    state = "activated" if result.activated else "ready (not activated)"
    console.print(
        f"Index version {result.version_id} {state}: {result.chunks} chunks, {result.symbols} symbols; "
        f"{result.embedded} embedded now, {result.cached} reused from cache."
    )
    if settings.embedding_provider == "hashing":
        console.print("[yellow]Hashing embeddings are for tests and offline demos only.[/yellow]")
    return True


@app.command()
def fetch(
    only: Annotated[
        list[str] | None, typer.Option(help="Fetch just these source names (repeatable).")
    ] = None,
) -> None:
    """Check out source repos and crawl source sites; record what to index."""
    if not _fetch(only):
        raise typer.Exit(code=1)


@app.command()
def build() -> None:
    """Parse and chunk everything fetch recorded."""
    if not _build():
        raise typer.Exit(code=1)


@app.command()
def inspect() -> None:
    """Write out/viewer.html to check chunks by eye."""
    out = get_settings().outdir
    path = write_viewer(read_chunks(out / "chunks.jsonl"), out / "viewer.html")
    console.print(f"Open {path} in your browser.")


@app.command()
def index(
    activate: Annotated[
        bool, typer.Option(help="Make the new version live once it validates.")
    ] = True,
) -> None:
    """Embed chunks and load them into a new index version."""
    if not _index(activate):
        raise typer.Exit(code=1)


@app.command()
def run() -> None:
    """fetch + build + index. Stops at the first failing stage, leaving the live index untouched."""
    for name, stage in (
        ("fetch", lambda: _fetch(None)),
        ("build", _build),
        ("index", lambda: _index(True)),
    ):
        console.rule(name)
        if not stage():
            console.print(f"[red]Stopped at {name}. The active index was not changed.[/red]")
            raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
