import pytest
from pydantic import ValidationError

from olake_shared import Chunk, ChunkMeta, Product, SourceType


def _meta(**overrides) -> ChunkMeta:
    base = {
        "product": Product.OLAKE_GO,
        "source_type": SourceType.DOC,
        "trust_tier": 1,
        "title": "CDC setup",
        "breadcrumb": ["OLake Go", "Sources", "Postgres", "CDC setup"],
        "url": "https://olake.io/docs/connectors/postgres/#cdc-setup",
        "repo": "datazip-inc/olake-docs",
        "path": "docs/connectors/postgres/index.mdx",
        "sha": "abc123",
    }
    base.update(overrides)
    return ChunkMeta(**base)


def test_build_prefixes_breadcrumb_and_keeps_display_text_clean():
    chunk = Chunk.build(locator="cdc-setup", body="Enable logical replication.", meta=_meta())
    assert chunk.text.startswith("OLake Go › Sources › Postgres › CDC setup")
    assert chunk.display_text == "Enable logical replication."


def test_id_is_stable_when_text_changes_but_hash_is_not():
    a = Chunk.build(locator="cdc-setup", body="Version one.", meta=_meta())
    b = Chunk.build(locator="cdc-setup", body="Version two.", meta=_meta())
    assert a.id == b.id
    assert a.content_hash != b.content_hash


def test_unknown_connector_is_rejected():
    with pytest.raises(ValidationError):
        _meta(connector="cassandra")
