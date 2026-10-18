"""Index loading: blue/green activation and embedding cache (needs Postgres)."""

import pytest

from olake_ingest.store.db import connect
from olake_ingest.store.index import IndexBuildError, load_index
from olake_shared.embeddings import HashingEmbedder


def test_active_index_is_loaded(indexed_db):
    with connect() as conn:
        versions = conn.execute("select status from index_versions order by id").fetchall()
        n = conn.execute(
            "select count(*) from chunks c join index_versions v on v.id = c.index_version "
            "where v.status = 'active'"
        ).fetchone()[0]
    assert versions[-1][0] == "active"
    assert n == len(indexed_db["chunks"])


def test_rebuild_reuses_cache_and_swaps_atomically(indexed_db):
    result = load_index(indexed_db["chunks"], indexed_db["symbols"], HashingEmbedder(1536))
    assert result.embedded == 0 and result.cached == len(indexed_db["chunks"])
    with connect() as conn:
        statuses = dict(conn.execute("select id, status from index_versions").fetchall())
    assert statuses[result.version_id] == "active"
    assert list(statuses.values()).count("active") == 1


def test_wrong_dimensions_are_refused(indexed_db):
    with pytest.raises(IndexBuildError, match="vector"):
        load_index(indexed_db["chunks"], [], HashingEmbedder(8))


def test_duplicate_ids_are_refused(indexed_db):
    chunk = indexed_db["chunks"][0]
    with pytest.raises(IndexBuildError, match="duplicate"):
        load_index([chunk, chunk], [], HashingEmbedder(1536))
