"""Parser tests against a real rendered OLake docs page (trimmed to the article)."""

from pathlib import Path

import pytest

from olake_ingest.parse.html_page import parse_page

FIXTURE = Path(__file__).parent / "fixtures" / "postgres_connector.html"


@pytest.fixture(scope="module")
def doc():
    return parse_page(FIXTURE.read_text(), "/docs/connectors/postgres/")


def _section(doc, anchor):
    return next(s for s in doc.sections if s.anchor == anchor)


def test_title_and_breadcrumb(doc):
    assert doc.title == "Overview"
    assert doc.breadcrumb == ["Sources", "Postgres"]


def test_anchors_are_the_sites_own_ids(doc):
    anchors = [s.anchor for s in doc.sections]
    assert "sync-modes-supported" in anchors
    # the duplicate heading in the CLI tab got Docusaurus' "-1" suffix
    assert "2-provide-configuration-details" in anchors
    assert "2-provide-configuration-details-1" in anchors
    assert len(anchors) == len(set(anchors))


def test_tab_panels_are_kept_and_labelled(doc):
    ui = _section(doc, "1-navigate-to-the-source-configuration-page")
    cli = _section(doc, "1-create-configuration-file")
    assert ui.tab == "Use OLake UI for Postgres"
    assert cli.tab == "Use OLake CLI for Postgres"  # the hidden panel is not lost


def test_heading_path_nests(doc):
    s = _section(doc, "change-data-capture-cdc")
    assert s.heading_path == [
        "Overview",
        "Configuration",
        "2. Provide Configuration Details",
        "Change data capture (CDC)",
    ]


def test_code_block_and_table_survive(doc):
    body = _section(doc, "2-provide-configuration-details-1").body
    assert "File: source.json\n```json\n{" in body
    assert '"replication_slot": "postgres_slot"' in body
    assert "| Field | Description | Example Value | Type |" in body


def test_react_component_inside_details_is_structured(doc):
    body = _section(doc, "2-provide-configuration-details-1").body
    assert "**SSL Mode Details**" in body
    assert '"mode": "verify-ca"' in body  # rendered by a JSX component, not in the MDX
    assert "- **Required parameters**: `server_ca`" in body


def test_admonition_becomes_labelled_quote(doc):
    body = _section(doc, "additional-configuration").body
    assert "> **Warning: Important: Unique Replication Slot Required**" in body


def test_no_site_chrome_leaks_in(doc):
    text = "\n".join(s.body for s in doc.sections)
    assert "Direct link to" not in text
    assert "\u200b" not in text
