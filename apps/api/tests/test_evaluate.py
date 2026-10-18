"""The evaluation gate: a candidate index goes live only if it passes (needs Postgres)."""

from rich.console import Console

from olake_api.db import get_pool

GOLDEN = """
thresholds:
  hit_at_5: {threshold}
questions:
  - id: cdc
    category: sync-mode
    question: How do I enable CDC for Postgres?
    relevant: [/docs/connectors/postgres/]
  - id: bread
    category: off-topic
    question: How do I bake sourdough bread?
    answerable: false
"""


def _statuses():
    with get_pool().connection() as conn:
        return dict(conn.execute("select id, status from index_versions").fetchall())


def _candidate(indexed_db) -> int:
    from olake_ingest.store.index import load_index
    from olake_shared.embeddings import HashingEmbedder

    return load_index(
        indexed_db["chunks"], indexed_db["symbols"], HashingEmbedder(1536), activate=False
    ).version_id


def test_failing_candidate_never_goes_live(api, indexed_db, tmp_path):
    from olake_api.evaluate import main

    candidate = _candidate(indexed_db)
    golden = tmp_path / "golden.yaml"
    golden.write_text(GOLDEN.format(threshold=1.01))  # impossible to meet
    ok = main(
        answers=False,
        golden_path=str(golden),
        console=Console(quiet=True),
        version="latest-ready",
        activate_on_pass=True,
    )
    assert not ok
    assert _statuses()[candidate] == "ready"


def test_passing_candidate_is_activated(api, indexed_db, tmp_path):
    from olake_api.evaluate import main

    candidate = _candidate(indexed_db)
    golden = tmp_path / "golden.yaml"
    golden.write_text(GOLDEN.format(threshold=0.5))
    ok = main(
        answers=True,  # extractive mode in tests: answer thresholds are skipped, not failed
        golden_path=str(golden),
        console=Console(quiet=True),
        version="latest-ready",
        activate_on_pass=True,
    )
    assert ok
    statuses = _statuses()
    assert statuses[candidate] == "active"
    assert list(statuses.values()).count("active") == 1
