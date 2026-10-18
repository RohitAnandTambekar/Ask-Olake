"""Query log and feedback. Best effort: logging must never break an answer."""

import json
import logging
import uuid

log = logging.getLogger("olake_api.store")


def log_query(
    pool,
    *,
    query_id: uuid.UUID,
    index_version: int | None,
    question: str,
    answer: str | None,
    status: str,
    cited_urls: list[str],
    retrieved: list[dict],
    model: str | None,
    latency_ms: int,
    first_token_ms: int | None,
    client_hash: str | None,
) -> None:
    try:
        with pool.connection() as conn:
            conn.execute(
                """
                insert into query_log (id, index_version, question, answer, status, cited_urls,
                  retrieved, model, latency_ms, first_token_ms, client_hash)
                values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                """,
                (
                    query_id,
                    index_version,
                    question,
                    answer,
                    status,
                    cited_urls,
                    json.dumps(retrieved),
                    model,
                    latency_ms,
                    first_token_ms,
                    client_hash,
                ),
            )
    except Exception:
        log.exception("could not write query_log")


def record_feedback(pool, query_id: uuid.UUID, rating: int, comment: str | None) -> bool:
    with pool.connection() as conn:
        exists = conn.execute("select 1 from query_log where id = %s", (query_id,)).fetchone()
        if not exists:
            return False
        conn.execute(
            "insert into feedback (query_id, rating, comment) values (%s, %s, %s)",
            (query_id, rating, comment),
        )
    return True
