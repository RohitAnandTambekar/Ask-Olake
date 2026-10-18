"""Re-ranking after fusion: similarity can lift a chunk only the vector retriever found."""

from olake_api.retrieval import Hit, rerank, same_connector


def _hit(id: str, score: float, similarity: float, trust: int = 1, connector=None) -> Hit:
    return Hit(
        id=id,
        text="",
        display_text="",
        url=f"https://olake.io/docs/{id}/",
        title=id,
        breadcrumb=[],
        product="olake-go",
        source_type="doc",
        trust_tier=trust,
        connector=connector,
        score=score,
        similarity=similarity,
    )


def test_closest_match_beats_chunks_that_only_share_words():
    hits = [_hit("common-words", 0.035, 0.55), _hit("best-match", 0.016, 0.75)]
    assert [h.id for h in rerank(hits)] == ["best-match", "common-words"]


def test_fused_score_still_breaks_near_ties_in_similarity():
    # a real pool spans a range of similarities; the weakest chunk anchors the scale
    hits = [
        _hit("vector-only", 0.016, 0.70),
        _hit("agreed-by-all", 0.040, 0.69),
        _hit("weak", 0.010, 0.40),
    ]
    assert rerank(hits)[0].id == "agreed-by-all"


def test_trust_tier_lowers_similarity_gently():
    hits = [
        _hit("blog", 0.02, 0.70, trust=3),
        _hit("doc", 0.02, 0.69, trust=1),
        _hit("low", 0.01, 0.4),
    ]
    assert rerank(hits)[0].id == "doc"


def test_exact_error_or_symbol_match_is_not_buried():
    code = _hit("raises-the-error", 0.05, 0.35)
    code.matched_by = ["errors"]
    hits = [_hit("similar-prose", 0.03, 0.72), code, _hit("weak", 0.01, 0.30)]
    assert rerank(hits)[0].id == "raises-the-error"


def test_rerank_handles_empty_and_equal_similarity():
    assert rerank([]) == []
    hits = [_hit("a", 0.01, 0.5), _hit("b", 0.03, 0.5)]
    assert [h.id for h in rerank(hits)] == ["b", "a"]


def test_same_connector_drops_other_connectors_only_for_one_named():
    hits = [
        _hit("pg", 0.03, 0.7, connector="postgres"),
        _hit("mongo", 0.03, 0.7, connector="mongodb"),
    ]
    hits.append(_hit("general", 0.02, 0.6))
    assert [h.id for h in same_connector(hits, ["postgres"])] == ["pg", "general"]
    assert len(same_connector(hits, ["postgres", "mongodb"])) == 3
    assert len(same_connector(hits, [])) == 3


def test_same_connector_keeps_everything_rather_than_nothing():
    hits = [_hit("mongo", 0.03, 0.7, connector="mongodb")]
    assert same_connector(hits, ["postgres"]) == hits
