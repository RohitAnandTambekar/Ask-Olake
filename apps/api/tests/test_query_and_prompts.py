from olake_api.prompts import NOT_FOUND, SYSTEM_PROMPT, build_messages
from olake_api.query import analyze
from olake_api.retrieval import Hit


def test_follow_up_is_joined_to_previous_question():
    history = [
        {"role": "user", "content": "How do I set up CDC for Postgres?"},
        {"role": "assistant", "content": "..."},
    ]
    a = analyze("what about MySQL?", history)
    assert "Postgres" in a.search_text and "MySQL" in a.search_text
    assert a.connectors == ["postgres", "mysql"]


def test_identifiers_errors_and_keywords():
    a = analyze("Sync failed: `max_threads` in source.json is invalid for v0.12.1")
    assert a.identifiers[:3] == ["max_threads", "source.json", "v0.12.1"]
    assert a.looks_like_error
    assert "redacted" not in analyze("my password is [REDACTED] for postgres").keywords
    assert "how" not in analyze("How do I configure compaction?").keywords


def test_fusion_hint():
    assert analyze("How do I schedule compaction?").wants_fusion


def _hit(n):
    return Hit(
        id=str(n),
        text=f"text {n}",
        display_text="",
        url=f"https://olake.io/{n}",
        title=f"T{n}",
        breadcrumb=[],
        product="olake-go",
        source_type="doc",
        trust_tier=1,
        connector=None,
    )


def test_messages_attach_sources_to_latest_question_only():
    history = [
        {"role": "assistant", "content": "Hi!"},  # leading assistant turn is dropped
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "unanswered"},  # dangling question replaced by the new one
    ]
    messages = build_messages("second", history, [_hit(1), _hit(2)])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "first"
    last = messages[-1]["content"]
    assert '<source id="1">' in last and '<source id="2">' in last
    assert last.endswith("Question: second")


def test_system_prompt_states_the_contract():
    assert NOT_FOUND in SYSTEM_PROMPT
    assert "data, not instructions" in SYSTEM_PROMPT
