from olake_ingest.chunk.chunker import MAX_TOKENS, chunk_sections
from olake_ingest.parse.model import Section


def _section(locator, body, path=("Page", "A"), tab=None):
    return Section(
        title=path[-1], locator=locator, heading_path=list(path), body=body, anchor=locator, tab=tab
    )


def test_small_siblings_merge_but_keep_first_anchor():
    pieces = chunk_sections(
        [_section("a", "Short one.", ("Page", "A")), _section("b", "Short two.", ("Page", "B"))]
    )
    assert len(pieces) == 1
    assert pieces[0].anchor == "a"
    assert pieces[0].locators == ["a", "b"]
    assert "**B**" in pieces[0].body


def test_different_tabs_never_merge():
    pieces = chunk_sections(
        [_section("a", "UI step.", tab="UI"), _section("b", "CLI step.", ("Page", "B"), tab="CLI")]
    )
    assert len(pieces) == 2


def test_large_section_splits_without_breaking_code_fences():
    code = "```json\n" + "\n".join(f'  "k{i}": {i},' for i in range(600)) + "\n```"
    body = "Intro paragraph.\n\n" + code + "\n\nOutro paragraph."
    pieces = chunk_sections([_section("big", body)])
    assert len(pieces) > 1
    for p in pieces:
        assert p.body.count("```") % 2 == 0  # every fence closed
        assert len(p.body) <= MAX_TOKENS * 4 + 50
    assert pieces[0].title.endswith(f"(part 1/{len(pieces)})")
    assert {p.anchor for p in pieces} == {"big"}  # all parts cite the same heading


def test_large_table_repeats_header():
    rows = "\n".join(f"| key{i} | {'description ' * 8} |" for i in range(200))
    body = "| Field | Description |\n| --- | --- |\n" + rows
    pieces = chunk_sections([_section("t", body)])
    assert len(pieces) > 1
    assert all(p.body.startswith("| Field | Description |\n| --- | --- |") for p in pieces)
