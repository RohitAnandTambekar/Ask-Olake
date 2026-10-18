"""Cheap, rule-based query analysis: no extra LLM call on the hot path.

Produces:
- the text to search with (a follow-up like "what about MySQL?" is joined to the
  previous question so it can be retrieved on its own),
- connector / product hints used to boost matching chunks,
- identifier-like tokens (max_threads, source.json) for exact matching,
- whether the question looks like a pasted error message.
"""

import re
from dataclasses import dataclass, field

CONNECTORS = {
    "postgres": r"\b(postgres(ql)?|pgoutput|wal2json)\b",
    "mysql": r"\b(mysql|binlog)\b",
    "mongodb": r"\b(mongo(db)?|oplog)\b",
    "oracle": r"\boracle\b",
    "mssql": r"\b(mssql|sql server)\b",
    "db2": r"\bdb2\b",
    "kafka": r"\bkafka\b",
    "s3": r"\bs3 (source|connector|bucket as source)\b",
}
FUSION_WORDS = re.compile(
    r"\b(fusion|compaction|compact|maintenance|optimi[sz]er|expire|snapshot)\b", re.IGNORECASE
)
IDENTIFIER = re.compile(r"`([^`\n]{2,80})`|\b([A-Za-z][A-Za-z0-9]*(?:[_.\-][A-Za-z0-9]+)+)\b")
ERROR_HINT = re.compile(
    r"\b(error|fail(?:s|ed|ing|ure)?|exception|panic|cannot|can't|unable|denied|invalid)\b",
    re.IGNORECASE,
)
FOLLOW_UP = re.compile(r"^(and |what about|how about|same for|also|and for|for )", re.IGNORECASE)
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "could",
    "do",
    "does",
    "did",
    "for",
    "from",
    "has",
    "have",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "should",
    "so",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "this",
    "to",
    "us",
    "use",
    "using",
    "was",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "about",
    "any",
    "get",
    "set",
    "also",
    "not",
    "olake",
    "go",
    "fail",
    "fails",
    "failed",
    "failing",
    "error",
    "errors",
    "issue",
    "issues",
    "problem",
    "help",
    "please",
    "getting",
    "trying",
    "want",
    "need",
    "working",
    "works",
    "work",
    "happen",
    "happens",
}
URL = re.compile(r"[a-z][a-z0-9+.\-]*://\S+", re.IGNORECASE)


@dataclass
class QueryAnalysis:
    question: str
    search_text: str
    connectors: list[str] = field(default_factory=list)
    wants_fusion: bool = False
    identifiers: list[str] = field(default_factory=list)
    looks_like_error: bool = False
    words: list[str] = field(default_factory=list)

    @property
    def keywords(self) -> list[str]:
        """Content words of the question itself (not the joined follow-up context)."""
        own = WORD.findall(URL.sub(" ", self.question).lower())
        return list(
            dict.fromkeys(
                w
                for w in own
                if len(w) > 2 and not w.isdigit() and w not in STOPWORDS and w != "redacted"
            )
        )


def analyze(question: str, history: list[dict] | None = None) -> QueryAnalysis:
    question = question.strip()
    search_text = question
    previous_user = next(
        (m["content"] for m in reversed(history or []) if m.get("role") == "user"), None
    )
    short = len(WORD.findall(question.lower())) <= 6
    if previous_user and (FOLLOW_UP.search(question) or short):
        search_text = f"{previous_user}\n{question}"

    lower = search_text.lower()
    connectors = [name for name, pattern in CONNECTORS.items() if re.search(pattern, lower)]
    identifiers: list[str] = []
    for m in IDENTIFIER.finditer(search_text):
        token = (m.group(1) or m.group(2)).strip()
        if token and token.lower() not in {i.lower() for i in identifiers}:
            identifiers.append(token)

    return QueryAnalysis(
        question=question,
        search_text=search_text,
        connectors=connectors,
        wants_fusion=bool(FUSION_WORDS.search(search_text)),
        identifiers=identifiers[:10],
        looks_like_error=bool(ERROR_HINT.search(question)) and len(question) >= 20,
        words=WORD.findall(lower)[:40],
    )
