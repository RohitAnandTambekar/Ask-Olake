"""Runtime settings, read from environment variables or the repo-root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/ingest/src/olake_ingest/config.py → parents[4] is the repo root
REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = Path(__file__).resolve().parent / "store" / "migrations"
SOURCES_FILE = APP_ROOT / "config" / "sources.yaml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://olake:olake@localhost:5433/ask_olake"

    # Must match the vector(N) size in the migrations. Pick the model before migrating.
    # EMBEDDING_PROVIDER=hashing needs no key: for tests and offline demos only.
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dims: int = 1536
    openai_api_key: str | None = None
    embed_batch_size: int = 64

    workdir: Path = APP_ROOT / ".work"  # cloned repos live here
    outdir: Path = APP_ROOT / "out"  # JSONL output from each stage

    github_token: str | None = None  # optional; raises GitHub rate limits

    # Website crawling (docs and blog are fetched as rendered pages from the live site).
    crawl_user_agent: str = "ask-olake-indexer/0.1"
    crawl_concurrency: int = 4  # parallel page downloads; keep it polite
    # Fetch from here instead of the site's own address, e.g. a local build at
    # http://localhost:3000. Citations still use the real site URL.
    site_fetch_base: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
