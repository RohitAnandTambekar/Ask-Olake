"""Embedding providers shared by ingestion (chunks) and the API (queries).

Both sides must use the same provider and model, or query vectors and chunk vectors
live in different spaces. The index records the model it was built with, and the API
refuses to search an index built with a different model.

- openai:  real semantic embeddings (needs OPENAI_API_KEY).
- hashing: deterministic bag-of-words vectors, no network, no key. For tests and
           offline demos only: it matches words, it does not understand meaning.
"""

import hashlib
import itertools
import math
import re
import time
from typing import Protocol

import httpx

MAX_INPUT_CHARS = 24_000  # well under the 8k-token input limit of OpenAI embedding models
TOKEN = re.compile(r"[a-z0-9]+")


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model: str
    dims: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    url = "https://api.openai.com/v1/embeddings"

    def __init__(self, api_key: str, model: str, dims: int, client: httpx.Client | None = None):
        if not api_key:
            raise EmbeddingError("OPENAI_API_KEY is not set")
        self.model = model
        self.dims = dims
        self._client = client or httpx.Client(timeout=60.0)
        self._headers = {"authorization": f"Bearer {api_key}"}

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": [t[:MAX_INPUT_CHARS] or " " for t in texts],
            "dimensions": self.dims,
        }
        last = ""
        for attempt in range(5):
            try:
                r = self._client.post(self.url, json=payload, headers=self._headers)
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code == 200:
                    data = sorted(r.json()["data"], key=lambda d: d["index"])
                    vectors = [d["embedding"] for d in data]
                    if len(vectors) != len(texts) or any(len(v) != self.dims for v in vectors):
                        raise EmbeddingError("embedding response has the wrong shape")
                    return vectors
                if r.status_code != 429 and r.status_code < 500:
                    raise EmbeddingError(f"OpenAI embeddings: HTTP {r.status_code}: {r.text[:300]}")
                last = f"HTTP {r.status_code}"
            time.sleep(min(2**attempt, 20))
        raise EmbeddingError(f"OpenAI embeddings failed after retries: {last}")


class HashingEmbedder:
    """Feature hashing of words and word pairs into a fixed-size, L2-normalised vector."""

    def __init__(self, dims: int):
        self.model = f"hashing-v1-{dims}"
        self.dims = dims

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        words = TOKEN.findall(text.lower())
        features = words + [f"{a} {b}" for a, b in itertools.pairwise(words)]
        for feature in features:
            h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(h[:4], "little") % self.dims
            vec[index] += 1.0 if h[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


def make_embedder(provider: str, model: str, dims: int, openai_api_key: str | None) -> Embedder:
    if provider == "openai":
        return OpenAIEmbedder(openai_api_key or "", model, dims)
    if provider == "hashing":
        return HashingEmbedder(dims)
    raise EmbeddingError(f"unknown EMBEDDING_PROVIDER {provider!r} (use 'openai' or 'hashing')")
