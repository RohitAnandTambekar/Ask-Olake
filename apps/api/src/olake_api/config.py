"""API settings, from environment variables or the repo-root .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/src/olake_api/config.py → parents[4] is the repo root
REPO_ROOT = Path(__file__).resolve().parents[4]
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql://olake:olake@localhost:5433/ask_olake"
    db_pool_max: int = 10

    # Must match what the index was built with (the API checks).
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dims: int = 1536
    openai_api_key: str | None = None

    # auto: OpenRouter if OPENROUTER_API_KEY is set, else Anthropic if ANTHROPIC_API_KEY is
    # set, else OpenAI if OPENAI_API_KEY is set, else "extractive" (no LLM: returns the most
    # relevant passages with links). Embeddings always use OpenAI (see embedding_provider).
    llm_provider: Literal["auto", "openrouter", "anthropic", "openai", "extractive"] = "auto"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-2.5-flash-lite"  # must be allowed by the key's guardrail
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str | None = None  # sent as HTTP-Referer for OpenRouter attribution
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    openai_chat_model: str = "gpt-4.1-mini"  # check OpenAI's current model list
    llm_max_output_tokens: int = 1200
    llm_timeout_seconds: float = 60.0

    # Retrieval
    top_k: int = 8  # sources given to the model
    candidates: int = 40  # per retriever, before fusion
    # Answer only if the best vector similarity reaches this (or an exact symbol matched).
    # None = provider default (openai 0.30, hashing 0.12). Tune it with `olake-api eval`.
    min_similarity: float | None = None

    # Abuse and cost controls
    max_question_chars: int = 2000
    max_history_messages: int = 6
    rate_limit_per_minute: int = 10
    rate_limit_burst: int = 5
    max_concurrent_answers: int = 8
    trust_proxy_headers: bool = False  # true behind a load balancer that sets X-Forwarded-For
    client_hash_salt: str = "change-me"
    allowed_origins: str = "*"  # comma-separated, e.g. "https://olake.io"

    @property
    def similarity_floor(self) -> float:
        if self.min_similarity is not None:
            return self.min_similarity
        return 0.12 if self.embedding_provider == "hashing" else 0.30

    @property
    def resolved_llm(self) -> str:
        if self.llm_provider != "auto":
            return self.llm_provider
        if self.openrouter_api_key:
            return "openrouter"
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_api_key:
            return "openai"
        return "extractive"


@lru_cache
def get_settings() -> Settings:
    return Settings()
