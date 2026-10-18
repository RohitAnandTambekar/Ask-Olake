import importlib

import pytest


@pytest.fixture
def api(indexed_db):
    """Fresh settings, pool and app bound to the test database."""
    from olake_api import config, db

    config.get_settings.cache_clear()
    db.get_pool.cache_clear()
    from olake_api import main

    main = importlib.reload(main)  # rebuild module-level settings, limiter and semaphore
    yield main
    db.get_pool().close()
    db.get_pool.cache_clear()
    config.get_settings.cache_clear()


@pytest.fixture
def answerer(api):
    def make(llm=None):
        a = api.get_answerer()
        a.llm = llm
        return a

    return make
