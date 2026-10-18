"""Stage 3: embed chunks and load them into a new index version, then activate it.

Blue/green: the new version is built beside the active one with status 'building'.
Only after its row counts check out is it switched to 'active' (and the old one to
'retired') in a single transaction, so the API never sees a half-loaded index.
Embeddings are cached by content hash, so a rebuild only pays for changed text.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from pgvector import Vector

from olake_ingest.store.db import connect
from olake_shared import Chunk, Symbol
from olake_shared.embeddings import Embedder

KEEP_RETIRED = 2  # older retired versions are deleted (their chunks cascade)
INSERT_BATCH = 500


class IndexBuildError(RuntimeError):
    pass


@dataclass
class IndexResult:
    version_id: int
    chunks: int
    symbols: int
    embedded: int  # new embeddings paid for this run
    cached: int  # reused from embedding_cache
    activated: bool


def _vector_column_dims(conn) -> int:
    row = conn.execute(
        """
        select a.atttypmod from pg_attribute a join pg_class c on c.oid = a.attrelid
        where c.relname = 'chunks' and a.attname = 'embedding'
        """
    ).fetchone()
    return row[0] if row else -1


def load_index(
    chunks: list[Chunk],
    symbols: list[Symbol],
    embedder: Embedder,
    *,
    batch_size: int = 64,
    activate: bool = True,
    manifest: dict | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> IndexResult:
    if not chunks:
        raise IndexBuildError("no chunks to index: run `olake-ingest build` first")
    ids = [c.id for c in chunks]
    if len(set(ids)) != len(ids):
        raise IndexBuildError("duplicate chunk ids in chunks.jsonl")

    with connect() as conn:
        dims = _vector_column_dims(conn)
        if dims != embedder.dims:
            raise IndexBuildError(
                f"embedder makes {embedder.dims}-dim vectors but chunks.embedding is vector({dims})"
            )

        version_id = conn.execute(
            "insert into index_versions (embedding_model, dims, status) values (%s, %s, 'building') returning id",
            (embedder.model, embedder.dims),
        ).fetchone()[0]
        conn.commit()

        # 1. Embeddings: reuse cached vectors, embed the rest in batches (committed per batch).
        hashes = list({c.content_hash for c in chunks})
        cached = {
            h: v
            for h, v in conn.execute(
                "select content_hash, embedding from embedding_cache where model = %s and content_hash = any(%s)",
                (embedder.model, hashes),
            )
        }
        text_by_hash = {c.content_hash: c.text for c in chunks}
        missing = [h for h in hashes if h not in cached]
        for start in range(0, len(missing), batch_size):
            batch = missing[start : start + batch_size]
            vectors = embedder.embed([text_by_hash[h] for h in batch])
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into embedding_cache (content_hash, model, embedding) values (%s, %s, %s) "
                    "on conflict do nothing",
                    [(h, embedder.model, Vector(v)) for h, v in zip(batch, vectors, strict=True)],
                )
            conn.commit()
            cached.update(zip(batch, (Vector(v) for v in vectors), strict=True))
            if progress:
                progress(min(start + batch_size, len(missing)), len(missing))

        # 2. Chunks and symbols for this version.
        rows = [
            (
                c.id,
                version_id,
                c.content_hash,
                c.text,
                c.display_text,
                c.meta.model_dump_json(),
                c.meta.product.value,
                c.meta.connector.value if c.meta.connector else None,
                c.meta.source_type.value,
                c.meta.trust_tier,
                c.meta.url,
                cached[c.content_hash],
            )
            for c in chunks
        ]
        with conn.cursor() as cur:
            for start in range(0, len(rows), INSERT_BATCH):
                cur.executemany(
                    "insert into chunks (id, index_version, content_hash, text, display_text, meta, product, "
                    "connector, source_type, trust_tier, url, embedding) "
                    "values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)",
                    rows[start : start + INSERT_BATCH],
                )
            cur.executemany(
                "insert into symbols (name, kind, repo, path, line, chunk_id, index_version) "
                "values (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (s.name, s.kind.value, s.repo, s.path, s.line, s.chunk_id, version_id)
                    for s in symbols
                ],
            )
        conn.commit()

        # 3. Validate, then swap atomically.
        n_chunks, n_null = conn.execute(
            "select count(*), count(*) filter (where embedding is null) from chunks where index_version = %s",
            (version_id,),
        ).fetchone()
        if n_chunks != len(chunks) or n_null:
            conn.execute(
                "update index_versions set status = 'retired' where id = %s", (version_id,)
            )
            conn.commit()
            raise IndexBuildError(
                f"validation failed: {n_chunks}/{len(chunks)} rows, {n_null} without vectors"
            )

        report = {**(manifest or {}), "chunks": n_chunks, "symbols": len(symbols)}
        with conn.transaction():
            conn.execute(
                "update index_versions set status = 'ready', manifest = %s::jsonb where id = %s",
                (json.dumps(report), version_id),
            )
            if activate:
                conn.execute("update index_versions set status = 'retired' where status = 'active'")
                conn.execute(
                    "update index_versions set status = 'active' where id = %s", (version_id,)
                )
        conn.execute(
            """
            delete from index_versions where status = 'retired' and id not in (
              select id from index_versions where status = 'retired' order by id desc limit %s)
            """,
            (KEEP_RETIRED,),
        )
        conn.commit()

    return IndexResult(
        version_id=version_id,
        chunks=n_chunks,
        symbols=len(symbols),
        embedded=len(missing),
        cached=len(hashes) - len(missing),
        activated=activate,
    )


def active_version() -> dict | None:
    with connect(vector=False) as conn:
        row = conn.execute(
            "select id, created_at, embedding_model, dims, manifest from index_versions where status = 'active'"
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "created_at": row[1], "model": row[2], "dims": row[3], "manifest": row[4]}
