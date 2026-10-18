"""Stage 2: parse everything `fetch` recorded, chunk it, and write JSONL.

Reads  out/fetch.json
Writes out/chunks.jsonl   one Chunk per line (the shared contract)
       out/symbols.jsonl  one Symbol per line
       out/build.json     per-source counts and any parse errors
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from olake_ingest.chunk.chunker import Piece, chunk_sections, estimate_tokens
from olake_ingest.config import get_settings
from olake_ingest.enrich import metadata as md
from olake_ingest.parse.files import parse_file
from olake_ingest.parse.html_page import parse_page
from olake_ingest.parse.model import ParsedDocument
from olake_ingest.sources import GitSource, SiteSource, load_sources
from olake_shared import Chunk, ChunkMeta, Symbol

RELEASE_TAG_PREFIX = "v"


@dataclass
class SourceStats:
    name: str
    documents: int = 0
    sections: int = 0
    chunks: int = 0
    symbols: int = 0
    tokens: int = 0
    errors: dict[str, str] = field(default_factory=dict)


def _github_url(repo: str, sha: str, path: str, start: int | None, end: int | None) -> str:
    url = f"https://github.com/{repo}/blob/{sha}/{path}"
    if path.lower().endswith((".md", ".markdown")):
        url += "?plain=1"  # line anchors only work on the plain view of Markdown files
    if start:
        url += f"#L{start}" + (f"-L{end}" if end and end != start else "")
    return url


def _page_breadcrumb(product_label: str, doc: ParsedDocument, piece: Piece) -> list[str]:
    crumbs = [product_label, *doc.breadcrumb]
    if doc.title and (not crumbs or crumbs[-1] != doc.title):
        crumbs.append(doc.title)
    crumbs.extend(piece.heading_path[1:])
    if piece.tab:
        crumbs.append(f"[{piece.tab}]")
    return crumbs


def _display_title(doc: ParsedDocument) -> str:
    """'Overview' on its own is useless in a citation; prefix the section it belongs to."""
    if doc.breadcrumb and len(doc.title.split()) <= 2 and doc.breadcrumb[-1] != doc.title:
        return f"{doc.breadcrumb[-1]} › {doc.title}"
    return doc.title


def _chunks_for_document(
    doc: ParsedDocument,
    *,
    source: GitSource | SiteSource,
    repo: str,
    sha: str,
    base_url: str | None,
    git_ref: str | None,
) -> tuple[list[Chunk], list[Symbol], int]:
    product = md.resolve_product(source.product, doc.path)
    source_type = md.resolve_source_type(source.source_type, doc.path)
    version = md.resolve_version(doc.path, git_ref)
    label = md.PRODUCT_LABELS[product]
    pieces = chunk_sections(doc.sections)

    chunks: list[Chunk] = []
    locator_to_id: dict[str, str] = {}
    used_ids: set[str] = set()
    for piece in pieces:
        if base_url is not None:  # site page
            url = base_url + (f"#{piece.anchor}" if piece.anchor else "")
            breadcrumb = _page_breadcrumb(label, doc, piece)
            page = _display_title(doc)
            title = page if piece.locator.startswith("_top") else f"{page}: {piece.title}"
        else:  # repo file
            url = _github_url(repo, sha, doc.path, piece.line_start, piece.line_end)
            breadcrumb = [label, repo.split("/")[-1], doc.path, *piece.heading_path[1:]]
            title = f"{doc.path}: {piece.title}" if piece.title != doc.path else doc.path
        signal = " ".join([doc.path, doc.title, *doc.breadcrumb, *piece.heading_path])
        meta = ChunkMeta(
            product=product,
            source_type=source_type,
            trust_tier=source.trust_tier,
            connector=md.detect_connector(doc.path, signal),
            topics=md.detect_topics(f"{signal} {piece.title}"),
            version=version,
            title=title,
            breadcrumb=breadcrumb,
            url=url,
            repo=repo,
            path=doc.path,
            sha=sha,
            line_start=piece.line_start,
            line_end=piece.line_end,
            symbol=piece.title if source_type.value == "code" else None,
        )
        chunk = Chunk.build(locator=piece.locator, body=piece.body, meta=meta)
        if chunk.id in used_ids:  # defensive: identical locators in one file
            chunk = Chunk.build(
                locator=f"{piece.locator}@{len(used_ids)}", body=piece.body, meta=meta
            )
        used_ids.add(chunk.id)
        chunks.append(chunk)
        for loc in piece.locators:
            locator_to_id.setdefault(loc, chunk.id)

    symbols = [
        Symbol(
            name=s.name,
            kind=s.kind,
            repo=repo,
            path=doc.path,
            line=s.line,
            chunk_id=locator_to_id.get(s.locator),
        )
        for s in doc.symbols
    ]
    return chunks, symbols, len(doc.sections)


def build() -> tuple[list[SourceStats], Path]:
    """Always rebuilds every source, so chunks.jsonl is never a partial index."""
    settings = get_settings()
    manifest_path = settings.outdir / "fetch.json"
    if not manifest_path.exists():
        raise FileNotFoundError("out/fetch.json not found: run `olake-ingest fetch` first")
    manifest = {e["name"]: e for e in json.loads(manifest_path.read_text())["sources"]}

    stats: list[SourceStats] = []
    chunks_path = settings.outdir / "chunks.jsonl"
    symbols_path = settings.outdir / "symbols.jsonl"
    with (
        chunks_path.open("w", encoding="utf-8") as cf,
        symbols_path.open("w", encoding="utf-8") as sf,
    ):
        for source in load_sources():
            st = SourceStats(source.name)
            stats.append(st)
            entry = manifest.get(source.name)
            if entry is None or entry["kind"] != source.kind:
                st.errors["(source)"] = "not in out/fetch.json: run fetch for it"
                continue

            if isinstance(source, SiteSource):
                items = [
                    (p["path"], Path(entry["site_dir"]) / p["file"], p["url"])
                    for p in entry["crawl"]["pages"]
                ]
                repo, sha, git_ref = source.host, "", None
            else:
                items = [
                    (f, Path(entry["checkout_dir"]) / f, None) for f in entry["selection"]["files"]
                ]
                repo, sha = source.repo, entry["sha"]
                resolved = entry["resolved_ref"]
                git_ref = resolved if resolved.startswith(RELEASE_TAG_PREFIX) else None

            for path, file, url in items:
                try:
                    text = file.read_text(encoding="utf-8", errors="replace")
                    doc = parse_page(text, path) if url is not None else parse_file(text, path)
                    chunks, symbols, n_sections = _chunks_for_document(
                        doc, source=source, repo=repo, sha=sha or "", base_url=url, git_ref=git_ref
                    )
                except Exception as exc:  # noqa: BLE001 - one bad file must not stop the build
                    st.errors[path] = f"{type(exc).__name__}: {exc}"
                    continue
                st.documents += 1
                st.sections += n_sections
                st.chunks += len(chunks)
                st.symbols += len(symbols)
                for c in chunks:
                    st.tokens += estimate_tokens(c.text)
                    cf.write(c.model_dump_json() + "\n")
                for s in symbols:
                    sf.write(s.model_dump_json() + "\n")

    report = settings.outdir / "build.json"
    report.write_text(
        json.dumps(
            {
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "sources": [asdict(s) for s in stats],
            },
            indent=2,
        )
    )
    return stats, chunks_path


def read_chunks(path: Path) -> list[Chunk]:
    with path.open(encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]


def read_symbols(path: Path) -> list[Symbol]:
    with path.open(encoding="utf-8") as f:
        return [Symbol.model_validate_json(line) for line in f if line.strip()]
