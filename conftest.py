"""Shared fixtures for tests that need Postgres.

Tests that use `indexed_db` run against a separate database (ask_olake_test by default,
override with TEST_DATABASE_URL), loaded with a tiny, known index built with the
hashing embedder. If Postgres is not reachable, those tests are skipped, not failed.
"""

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://olake:olake@localhost:5433/ask_olake_test"
)


def _admin_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/postgres"))


def _make_chunks():
    from olake_shared import Chunk, ChunkMeta, Connector, Product, SourceType, SymbolKind, Topic
    from olake_shared.models import Symbol

    def meta(**kw):
        base = {
            "product": Product.OLAKE_GO,
            "source_type": SourceType.DOC,
            "trust_tier": 1,
            "sha": "",
            "repo": "olake.io",
        }
        base.update(kw)
        return ChunkMeta(**base)

    chunks = [
        Chunk.build(
            locator="cdc",
            body="To enable CDC for Postgres, set wal_level to logical, create a publication and a "
            "logical replication slot, then set `replication_slot` and `publication` under "
            "`update_method` in source.json.",
            meta=meta(
                title="Postgres › Overview: CDC Prerequisite",
                breadcrumb=["OLake Go", "Sources", "Postgres", "CDC Prerequisite"],
                url="https://olake.io/docs/connectors/postgres/#cdc-prerequisite",
                path="/docs/connectors/postgres/",
                connector=Connector.POSTGRES,
                topics=[Topic.SYNC_MODE],
            ),
        ),
        Chunk.build(
            locator="threads",
            body="`max_threads` sets the maximum number of worker threads the MySQL connector uses "
            "for parallel chunk reads. Default example value: 10.",
            meta=meta(
                title="MySQL › Overview: Additional configuration",
                breadcrumb=["OLake Go", "Sources", "MySQL", "Additional configuration"],
                url="https://olake.io/docs/connectors/mysql/#additional-configuration",
                path="/docs/connectors/mysql/",
                connector=Connector.MYSQL,
                topics=[Topic.CONFIG],
            ),
        ),
        Chunk.build(
            locator="Config.Validate",
            body='```go\nfunc (c *Config) Validate() error {\n\tif c.Database == "" {\n'
            '\t\treturn fmt.Errorf("database name is required")\n\t}\n\treturn nil\n}\n```',
            meta=meta(
                title="drivers/postgres/internal/config.go: Config.Validate",
                breadcrumb=[
                    "OLake Go",
                    "olake",
                    "drivers/postgres/internal/config.go",
                    "Config.Validate",
                ],
                url="https://github.com/datazip-inc/olake/blob/abc/drivers/postgres/internal/config.go#L37-L44",
                path="drivers/postgres/internal/config.go",
                repo="datazip-inc/olake",
                source_type=SourceType.CODE,
                connector=Connector.POSTGRES,
            ),
        ),
        Chunk.build(
            locator="compaction",
            body="OLake Fusion runs compaction on a cron schedule you set per table, rewriting small "
            "files into larger ones to keep Iceberg queries fast.",
            meta=meta(
                title="Fusion: Compaction configuration",
                breadcrumb=["OLake Fusion", "Compaction", "Configuration"],
                url="https://olake.io/docs/fusion/compaction/configuration/",
                path="/docs/fusion/compaction/configuration/",
                product=Product.FUSION,
                topics=[Topic.MAINTENANCE],
            ),
        ),
    ]
    symbols = [
        Symbol(
            name="database name is required",
            kind=SymbolKind.ERROR_STRING,
            repo="datazip-inc/olake",
            path="drivers/postgres/internal/config.go",
            line=39,
            chunk_id=chunks[2].id,
        ),
        Symbol(
            name="max_threads",
            kind=SymbolKind.CONFIG_KEY,
            repo="datazip-inc/olake",
            path="drivers/mysql/internal/config.go",
            line=20,
            chunk_id=chunks[1].id,
        ),
    ]
    return chunks, symbols


@pytest.fixture(scope="session")
def indexed_db():
    """A fresh test database with the schema and a 4-chunk active index."""
    db_name = urlsplit(TEST_DATABASE_URL).path.lstrip("/")
    try:
        with psycopg.connect(
            _admin_url(TEST_DATABASE_URL), autocommit=True, connect_timeout=3
        ) as admin:
            admin.execute(f'drop database if exists "{db_name}" with (force)')
            admin.execute(f'create database "{db_name}"')
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable for integration tests: {exc}")

    env = {
        "DATABASE_URL": TEST_DATABASE_URL,
        "EMBEDDING_PROVIDER": "hashing",
        "EMBEDDING_DIMS": "1536",
        "LLM_PROVIDER": "extractive",
        "RATE_LIMIT_PER_MINUTE": "1000",
        "RATE_LIMIT_BURST": "1000",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)

    from olake_ingest import config as ingest_config

    ingest_config.get_settings.cache_clear()
    from olake_ingest.store.db import apply_migrations
    from olake_ingest.store.index import load_index
    from olake_shared.embeddings import HashingEmbedder

    apply_migrations()
    chunks, symbols = _make_chunks()
    load_index(chunks, symbols, HashingEmbedder(1536))
    yield {"chunks": chunks, "symbols": symbols}

    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    ingest_config.get_settings.cache_clear()
