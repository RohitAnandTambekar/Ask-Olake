"""Hybrid retrieval over the active index version.

Four retrievers, fused with Reciprocal Rank Fusion (RRF):
1. vector:  cosine similarity on embeddings (meaning)
2. words:   English full-text search, OR over the question's words (stemmed)
3. idents:  phrase search for identifiers (max_threads -> 'max <-> threads')
4. symbols: exact config keys / env vars, and Go error strings contained in the question

RRF adds 1/(60 + rank) from each list a chunk appears in, so a chunk ranked well by
several retrievers wins without having to calibrate their raw scores against each
other. Fused scores are then nudged by trust tier and by connector/product hints.

RRF alone buries a chunk that only the vector retriever finds, even when it is the
closest match in meaning (common words lift weaker chunks past it). So the top
RERANK_POOL fused results are re-ranked by a blend: FUSED_SHARE of the fused score and
the rest vector similarity, both scaled to 0-1 within the pool. When the question names
exactly one connector, chunks about a different connector are dropped first.
"""

import re
from dataclasses import dataclass, field

import psycopg
from pgvector import Vector

from olake_api.query import QueryAnalysis

RRF_K = 60
WEIGHTS = {"vector": 1.0, "words": 1.0, "idents": 1.5, "symbols": 1.5, "errors": 3.0}
TRUST = {1: 1.0, 2: 0.92, 3: 0.7, 4: 0.6}
CONNECTOR_BOOST = 1.2
FUSION_BOOST = 1.15
RERANK_POOL = 40
FUSED_SHARE = 0.3  # tuned on eval/golden.yaml plus the team test cases
SIM_TRUST = {1: 1.0, 2: 0.97, 3: 0.9, 4: 0.85}  # gentler than TRUST: similarity already ranks
SAFE_WORD = re.compile(r"^[a-z0-9]+$")
MIN_ERROR_PREFIX = 12


class IndexUnavailable(RuntimeError):
    pass


@dataclass
class Hit:
    id: str
    text: str
    display_text: str
    url: str
    title: str
    breadcrumb: list[str]
    product: str
    source_type: str
    trust_tier: int
    connector: str | None
    score: float = 0.0
    similarity: float | None = None
    matched_by: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    hits: list[Hit]
    index_version: int
    best_similarity: float
    symbol_match: bool
    lexical_match: bool
    coverage: float  # share of the question's keywords found in the top 3 hits combined
    ident_match: bool = (
        False  # an identifier from the question (v0.12.0, max_threads) is in the top 3
    )

    def answerable(self, similarity_floor: float, min_coverage: float = 0.5) -> bool:
        """Cheap pre-filter before calling the LLM: stops clearly off-topic questions.

        Deliberately lenient: the model's own NOT_FOUND rule makes the fine-grained call.
        """
        if not self.hits:
            return False
        if self.symbol_match or self.ident_match:
            return True
        return self.best_similarity >= similarity_floor and self.coverage >= min_coverage


def same_connector(hits: list[Hit], connectors: list[str]) -> list[Hit]:
    """Drop chunks about another connector when the question names exactly one."""
    if len(connectors) != 1:
        return hits
    kept = [h for h in hits if h.connector in (None, connectors[0])]
    return kept or hits


def rerank(hits: list[Hit]) -> list[Hit]:
    """Blend fused score and vector similarity (each scaled to 0-1 within the pool).

    An exact symbol or error-string match counts as full similarity: a pasted error
    reads nothing like the Go code that raises it, but that code is the answer.
    """
    if not hits:
        return hits
    top = max(h.score for h in hits) or 1.0
    sims = [h.similarity or 0.0 for h in hits]
    lo, hi = min(sims), max(sims)
    for h in hits:
        exact = "errors" in h.matched_by or "symbols" in h.matched_by
        sim = 1.0 if exact or hi <= lo else ((h.similarity or 0.0) - lo) / (hi - lo)
        sim *= SIM_TRUST.get(h.trust_tier, 0.85)
        h.score = FUSED_SHARE * (h.score / top) + (1 - FUSED_SHARE) * sim
    return sorted(hits, key=lambda h: h.score, reverse=True)


def index_model(conn, version: int) -> str:
    row = conn.execute(
        "select embedding_model from index_versions where id = %s", (version,)
    ).fetchone()
    if row is None:
        raise IndexUnavailable(f"index version {version} does not exist")
    return row[0]


def active_index(conn) -> tuple[int, str]:
    row = conn.execute(
        "select id, embedding_model from index_versions where status = 'active'"
    ).fetchone()
    if row is None:
        raise IndexUnavailable("no active index: run `olake-ingest run`")
    return row[0], row[1]


def _or_query(words: list[str]) -> str | None:
    terms = [w for w in dict.fromkeys(words) if SAFE_WORD.match(w) and len(w) > 1][:20]
    return " | ".join(terms) or None


def _ident_query(identifiers: list[str]) -> tuple[str, dict] | None:
    """OR of phrase queries, tokenized by Postgres itself (so `v0.12.0` stays one token
    and `max_threads` becomes 'max' followed by 'threads', exactly as in the documents)."""
    params = {f"i{n}": ident for n, ident in enumerate(identifiers[:8])}
    if not params:
        return None
    clause = " || ".join(f"phraseto_tsquery('simple', %({k})s)" for k in params)
    return clause, params


def search(
    conn,
    query: QueryAnalysis,
    query_vector: list[float],
    *,
    top_k: int,
    candidates: int,
    version: int | None = None,
) -> RetrievalResult:
    """Search the active index, or a specific version (used to evaluate a candidate)."""
    if version is None:
        version, _model = active_index(conn)
    ranked: dict[str, list[str]] = {}
    similarity: dict[str, float] = {}

    with conn.transaction():
        conn.execute("set local hnsw.ef_search = 100")
        try:
            with conn.transaction():  # savepoint: a failure here must not abort the outer block
                conn.execute("set local hnsw.iterative_scan = relaxed_order")  # pgvector >= 0.8
        except psycopg.Error:  # older pgvector: ef_search alone is enough at this size
            pass
        rows = conn.execute(
            """
            select id, 1 - (embedding <=> %(q)s) as sim
            from chunks where index_version = %(v)s
            order by embedding <=> %(q)s limit %(k)s
            """,
            {"q": Vector(query_vector), "v": version, "k": candidates},
        ).fetchall()
    ranked["vector"] = [r[0] for r in rows]
    similarity.update({r[0]: float(r[1]) for r in rows})

    words_q = _or_query(query.words)
    if words_q:
        ranked["words"] = [
            r[0]
            for r in conn.execute(
                """
                select id from chunks, to_tsquery('english', %(q)s) q
                where index_version = %(v)s and tsv_en @@ q
                order by ts_rank_cd(tsv_en, q) desc limit %(k)s
                """,
                {"q": words_q, "v": version, "k": candidates},
            )
        ]

    idents = _ident_query(query.identifiers)
    if idents:
        clause, params = idents
        ranked["idents"] = [
            r[0]
            for r in conn.execute(
                f"""
                select id from chunks, (select {clause} as q) s
                where index_version = %(v)s and tsv_ident @@ s.q
                order by ts_rank_cd(tsv_ident, s.q) desc limit %(k)s
                """,  # clause is built from fixed text only; values are bound parameters
                {**params, "v": version, "k": candidates},
            )
        ]

    symbol_ids: list[str] = []
    if query.identifiers:
        symbol_ids = [
            r[0]
            for r in conn.execute(
                """
                select distinct on (chunk_id) chunk_id from symbols
                where index_version = %(v)s and chunk_id is not null
                  and kind in ('config_key', 'env_var', 'struct', 'func')
                  and lower(name) = any(%(names)s)
                limit 10
                """,
                {"v": version, "names": [i.lower() for i in query.identifiers]},
            )
        ]
    if symbol_ids:
        ranked["symbols"] = list(dict.fromkeys(symbol_ids))
    if len(query.question) >= MIN_ERROR_PREFIX:
        # Always checked (one cheap scan): if the static part of a Go error message
        # (before the first %v/%s) appears in what the user pasted, that points
        # straight at the code that raised it.
        error_ids = [
            r[0]
            for r in conn.execute(
                """
                select chunk_id from (
                  select chunk_id, length(split_part(name, '%%', 1)) as l from symbols
                  where index_version = %(v)s and kind = 'error_string' and chunk_id is not null
                    and length(split_part(name, '%%', 1)) >= %(min)s
                    and position(lower(split_part(name, '%%', 1)) in lower(%(q)s)) > 0
                ) s order by l desc limit 5
                """,
                {"v": version, "q": query.question, "min": MIN_ERROR_PREFIX},
            )
        ]
        if error_ids:
            ranked["errors"] = list(dict.fromkeys(error_ids))

    scores: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for name, ids in ranked.items():
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + WEIGHTS[name] / (RRF_K + rank)
            matched.setdefault(chunk_id, []).append(name)

    if not scores:
        return RetrievalResult([], version, 0.0, False, False, 0.0)

    pool = sorted(scores, key=scores.get, reverse=True)[: max(RERANK_POOL, top_k)]
    rows = conn.execute(
        """
        select id, text, display_text, url, meta, product, source_type, trust_tier, connector,
               1 - (embedding <=> %(q)s) as sim
        from chunks where index_version = %(v)s and id = any(%(ids)s)
        """,
        {"v": version, "ids": pool, "q": Vector(query_vector)},
    ).fetchall()

    hits: list[Hit] = []
    for r in rows:
        meta = r[4]
        score = scores[r[0]] * TRUST.get(r[7], 0.7)
        if query.connectors and r[8] in query.connectors:
            score *= CONNECTOR_BOOST
        if query.wants_fusion and r[5] == "fusion":
            score *= FUSION_BOOST
        hits.append(
            Hit(
                id=r[0],
                text=r[1],
                display_text=r[2],
                url=r[3],
                title=meta.get("title", ""),
                breadcrumb=meta.get("breadcrumb", []),
                product=r[5],
                source_type=r[6],
                trust_tier=r[7],
                connector=r[8],
                score=score,
                similarity=float(r[9]),
                matched_by=matched.get(r[0], []),
            )
        )
    hits = rerank(same_connector(hits, query.connectors))[:top_k]
    keywords = query.keywords
    coverage = 1.0
    if keywords:
        top_text = " ".join(h.text.lower() for h in hits[:3])
        coverage = sum(1 for k in keywords if k in top_text) / len(keywords)
    return RetrievalResult(
        hits=hits,
        index_version=version,
        best_similarity=max((h.similarity or 0.0) for h in hits),
        symbol_match=bool(ranked.get("errors") or ranked.get("symbols")),
        lexical_match=bool(ranked.get("words") or ranked.get("idents")),
        coverage=coverage,
        ident_match=any("idents" in h.matched_by for h in hits[:3]),
    )
