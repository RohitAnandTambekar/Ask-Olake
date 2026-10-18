"""The full answer pipeline against the test index (needs Postgres)."""

from fakes import ScriptedLLM

from olake_api.db import get_pool
from olake_api.llm import LLMError


def _run(answerer, question, history=None):
    events = list(answerer.run(question, history or []))
    by_type = {}
    for e in events:
        by_type.setdefault(e["event"], []).append(e["data"])
    return events, by_type


def test_grounded_answer_streams_then_verifies(answerer):
    llm = ScriptedLLM("Set `wal_level` to logical and create a replication slot [1].")
    events, ev = _run(answerer(llm), "How do I enable CDC for Postgres?")

    assert [e["event"] for e in events][:2] == ["meta", "sources"]
    assert "".join(d["text"] for d in ev["delta"]).startswith("Set `wal_level`")
    final = ev["final"][0]
    assert final["status"] == "answered"
    assert final["citations"] == [1]
    assert ev["sources"][0]["sources"][0]["url"].endswith("#cdc-prerequisite")
    # the model saw the sources, attached to the question
    assert '<source id="1">' in llm.calls[0][-1]["content"]


def test_not_found_is_never_streamed_and_becomes_an_abstention(answerer):
    llm = ScriptedLLM("NOT_FOUND\nThe sources do not mention Snowflake Snowpipe.")
    _, ev = _run(answerer(llm), "How do I enable CDC for Postgres with Snowpipe?")
    assert "delta" not in ev
    final = ev["final"][0]
    assert final["status"] == "abstained"
    assert "Snowpipe" in final["answer"]  # the model's reason is passed on
    assert "https://olake.io/slack/" in final["answer"]


def test_answer_without_valid_citations_is_withheld(answerer):
    _, ev = _run(
        answerer(ScriptedLLM("CDC works great, trust me [9].")), "How do I enable CDC for Postgres?"
    )
    assert ev["final"][0]["status"] == "abstained"


def test_invented_config_key_is_flagged(answerer):
    llm = ScriptedLLM("Set `max_threads` [1] and `turbo_mode` [1].")
    _, ev = _run(answerer(llm), "what does max_threads do for mysql")
    final = ev["final"][0]
    assert final["status"] == "partial"
    assert "turbo_mode" in final["warnings"][0]


def test_off_topic_question_never_reaches_the_model(answerer):
    llm = ScriptedLLM("should not be called")
    _, ev = _run(answerer(llm), "How do I bake sourdough bread?")
    assert ev["final"][0]["status"] == "abstained"
    assert llm.calls == []


def test_pasted_error_finds_the_code_that_raises_it(answerer):
    _, ev = _run(answerer(None), "sync fails with: database name is required")
    top = ev["sources"][0]["sources"][0]
    assert top["url"].startswith("https://github.com/datazip-inc/olake/blob/")
    assert "Config.Validate" in top["title"]


def test_provider_outage_falls_back_to_passages(answerer):
    _, ev = _run(answerer(ScriptedLLM(fail=LLMError("503"))), "How do I enable CDC for Postgres?")
    final = ev["final"][0]
    assert final["status"] == "partial"
    assert "temporarily unavailable" in final["warnings"][0]


def test_secrets_never_reach_the_model_or_the_log(answerer):
    llm = ScriptedLLM("Create a replication slot [1].")
    _, ev = _run(answerer(llm), "Postgres CDC with postgres://admin:hunter2@db/app fails")
    assert ev["meta"][0]["notices"]
    assert "hunter2" not in str(llm.calls)
    query_id = ev["meta"][0]["query_id"]
    with get_pool().connection() as conn:
        question, status = conn.execute(
            "select question, status from query_log where id = %s", (query_id,)
        ).fetchone()
    assert "hunter2" not in question and "[REDACTED]" in question
    assert status == "answered"


def test_extractive_excerpts_never_leave_a_code_block_open(answerer):
    from olake_api.retrieval import Hit

    long_code = "```go\n" + "x := 1\n" * 300 + "```"
    hit = Hit(
        id="1",
        text=long_code,
        display_text=long_code,
        url="https://x",
        title="T",
        breadcrumb=[],
        product="olake-go",
        source_type="code",
        trust_tier=1,
        connector=None,
    )
    text = answerer(None)._extractive_text([hit, hit])
    assert text.count("```") % 2 == 0
