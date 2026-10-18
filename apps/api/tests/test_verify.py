from olake_api.verify import verify

SOURCES = [
    "Set `max_threads` to control parallel reads. Example: 10.",
    'Set "replication_slot" and "publication" under update_method.',
]
URLS = ["https://olake.io/docs/connectors/mysql/#x", "https://olake.io/docs/connectors/postgres/#y"]


def test_valid_citations_are_kept_and_invalid_removed():
    v = verify("Use `max_threads` [1]. Also slots [2][7]. And [1, 9].", SOURCES, URLS)
    assert v.text == "Use `max_threads` [1]. Also slots [2]. And [1]."
    assert v.citations == [1, 2]
    assert v.status == "answered"


def test_answer_without_citations_is_ungrounded():
    assert verify("OLake is great.", SOURCES, URLS).status == "ungrounded"


def test_invented_identifiers_are_flagged():
    v = verify("Set `max_parallel_readers` [1].", SOURCES, URLS)
    assert v.status == "partial"
    assert "max_parallel_readers" in v.warnings[0]


def test_invented_json_keys_in_code_blocks_are_flagged():
    answer = 'Config [2]:\n```json\n{"replication_slot": "s", "slot_timeout_ms": 5}\n```'
    v = verify(answer, SOURCES, URLS)
    assert "slot_timeout_ms" in v.warnings[0]
    assert "replication_slot" not in v.warnings[0]


def test_citation_like_text_inside_code_is_untouched():
    answer = "Example [1]:\n```go\nx := arr[3]\n```"
    assert "arr[3]" in verify(answer, SOURCES, URLS).text


def test_untrusted_links_are_stripped():
    answer = "See [docs](https://evil.example/x) and [here](https://olake.io/docs/) [1]. http://spam.example/y"
    v = verify(answer, SOURCES, URLS)
    assert "evil.example" not in v.text and "docs" in v.text
    assert "[here](https://olake.io/docs/)" in v.text
    assert "spam.example" not in v.text
