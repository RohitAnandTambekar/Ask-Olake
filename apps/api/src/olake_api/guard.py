"""Input guardrails: secret scrubbing and per-client rate limiting.

People paste connection strings and config files into support chats. Anything that
looks like a credential is replaced with [REDACTED] *before* it is logged, embedded
or sent to the LLM provider, and the user is told it happened.
"""

import hashlib
import re
import threading
import time
from dataclasses import dataclass

REDACTED = "[REDACTED]"

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
    ),
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "API key",
        re.compile(
            r"\b(sk-(ant-)?[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[abprs]-[A-Za-z0-9-]{10,})\b"
        ),
    ),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer token", re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}")),
]
# key/value pairs: password=..., "secret_key": "...", aws_secret_access_key: ...
KV_SECRET = re.compile(
    r"""(?ix)
    (?P<key>["']?(?:password|passwd|pwd|secret|secret[_-]?key|access[_-]?key|aws_secret_access_key|
        api[_-]?key|token|client[_-]?secret|private[_-]?key)["']?\s*[:=]\s*)
    (?P<value>"[^"\n]{1,200}"|'[^'\n]{1,200}'|[^\s,}\n]{1,200})
    """
)
# credentials inside URLs: postgres://user:pass@host
URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.\-]*://[^\s:/@]+:)(?P<password>[^\s@/]+)(?P<at>@)", re.IGNORECASE
)


@dataclass
class ScrubResult:
    text: str
    found: list[str]


def scrub(text: str) -> ScrubResult:
    found: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            found.append(label)
            if label == "bearer token":
                text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
            else:
                text = pattern.sub(REDACTED, text)
    if URL_CREDENTIALS.search(text):
        found.append("password in URL")
        text = URL_CREDENTIALS.sub(lambda m: f"{m['scheme']}{REDACTED}{m['at']}", text)

    def kv(m: re.Match) -> str:
        value = m["value"]
        if REDACTED in value or value.strip("\"'") in ("", "null", "none", "<password>"):
            return m.group(0)
        found.append("credential value")
        quote = value[0] if value[0] in "\"'" else ""
        return f"{m['key']}{quote}{REDACTED}{quote}"

    text = KV_SECRET.sub(kv, text)
    return ScrubResult(text=text, found=list(dict.fromkeys(found)))


def client_hash(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:16]


class RateLimiter:
    """Token bucket per client: `rate` requests per minute, bursts up to `burst`.

    In-process only: with several API replicas, put this in Redis (or the load
    balancer) so limits are shared.
    """

    def __init__(self, per_minute: int, burst: int, max_clients: int = 50_000):
        self.rate = per_minute / 60.0
        self.burst = float(burst)
        self.max_clients = max_clients
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            if len(self._buckets) > self.max_clients:  # bound memory: drop the oldest half
                for k, _ in sorted(self._buckets.items(), key=lambda kv: kv[1][1])[
                    : self.max_clients // 2
                ]:
                    del self._buckets[k]
            return True
