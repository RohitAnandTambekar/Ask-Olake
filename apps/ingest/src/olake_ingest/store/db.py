"""Database connection and a minimal, forward-only migration runner."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from olake_ingest.config import MIGRATIONS_DIR, get_settings


@contextmanager
def connect(*, vector: bool = True) -> Iterator[psycopg.Connection]:
    """Open a connection. `vector=False` is for before the extension exists."""
    with psycopg.connect(get_settings().database_url) as conn:
        if vector:
            register_vector(conn)
        yield conn


def apply_migrations() -> list[str]:
    """Run every migrations/*.sql file not yet recorded, in filename order."""
    applied_now: list[str] = []
    with connect(vector=False) as conn:
        conn.execute(
            """
            create table if not exists schema_migrations (
              filename   text primary key,
              applied_at timestamptz not null default now()
            )
            """
        )
        done = {row[0] for row in conn.execute("select filename from schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("insert into schema_migrations (filename) values (%s)", (path.name,))
            applied_now.append(path.name)
    return applied_now


def health() -> dict:
    """Facts `doctor` prints: server version, pgvector version, tables, vector dims."""
    with connect(vector=False) as conn:
        server = conn.execute("show server_version").fetchone()[0]
        ext = conn.execute(
            "select extversion from pg_extension where extname = 'vector'"
        ).fetchone()
        tables = [
            r[0]
            for r in conn.execute(
                "select tablename from pg_tables where schemaname = 'public' order by tablename"
            )
        ]
        # For a vector(N) column, atttypmod stores N directly.
        dims = {
            f"{r[0]}.{r[1]}": r[2]
            for r in conn.execute(
                """
                select c.relname, a.attname, a.atttypmod
                from pg_attribute a
                join pg_class c on c.oid = a.attrelid
                join pg_type t on t.oid = a.atttypid
                where t.typname = 'vector' and a.attnum > 0 and not a.attisdropped
                order by 1, 2
                """
            )
        }
    return {
        "server_version": server,
        "pgvector": ext[0] if ext else None,
        "tables": tables,
        "vector_dims": dims,
    }
