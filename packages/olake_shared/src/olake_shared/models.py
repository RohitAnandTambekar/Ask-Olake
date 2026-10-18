"""The chunk contract: what ingestion produces and what the API will consume."""

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from olake_shared.taxonomy import Connector, Product, SourceType, SymbolKind, Topic


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def make_chunk_id(repo: str, path: str, locator: str) -> str:
    """Stable id for a chunk.

    `locator` is the heading anchor for docs or the symbol name for code, so
    the id survives edits to the chunk's text but changes if it moves.
    """
    return _sha1(f"{repo}::{path}::{locator}")


def content_hash(text: str) -> str:
    """Hash of the embedded text. Unchanged hash means no re-embedding."""
    return _sha1(text)


class ChunkMeta(BaseModel):
    product: Product
    source_type: SourceType
    trust_tier: Literal[1, 2, 3, 4]
    connector: Connector | None = None
    topics: list[Topic] = Field(default_factory=list)
    version: str = "current"
    title: str
    breadcrumb: list[str] = Field(default_factory=list)
    url: str  # public citation URL: olake.io page#anchor or GitHub blob permalink
    repo: str  # e.g. "datazip-inc/olake-docs"
    path: str  # file path inside the repo
    sha: str  # commit the file was read at
    line_start: int | None = None
    line_end: int | None = None
    symbol: str | None = None


class Chunk(BaseModel):
    id: str
    content_hash: str
    text: str  # what gets embedded: breadcrumb + body
    display_text: str  # what gets shown or quoted: body only
    meta: ChunkMeta

    @classmethod
    def build(cls, *, locator: str, body: str, meta: ChunkMeta) -> "Chunk":
        """Create a chunk and derive id, embedded text and hash consistently."""
        prefix = " › ".join(meta.breadcrumb)
        text = f"{prefix}\n\n{body}" if prefix else body
        return cls(
            id=make_chunk_id(meta.repo, meta.path, locator),
            content_hash=content_hash(text),
            text=text,
            display_text=body,
            meta=meta,
        )


class Symbol(BaseModel):
    """An exact-match handle into the code: a config key, flag, env var or error string."""

    name: str
    kind: SymbolKind
    repo: str
    path: str
    line: int
    chunk_id: str | None = None
