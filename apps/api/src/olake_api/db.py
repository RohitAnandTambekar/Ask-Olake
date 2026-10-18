"""Connection pool shared by all requests."""

from functools import lru_cache

from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from olake_api.config import get_settings


def _configure(conn) -> None:
    register_vector(conn)
    conn.autocommit = True


@lru_cache
def get_pool() -> ConnectionPool:
    settings = get_settings()
    return ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=settings.db_pool_max,
        configure=_configure,
        open=True,
        timeout=10,
    )
