from pathlib import Path

from olake_ingest.parse.files import parse_file
from olake_shared import SymbolKind

FIXTURES = Path(__file__).parent / "fixtures"


def test_go_sections_and_symbols():
    doc = parse_file(
        (FIXTURES / "postgres_config.go").read_text(), "drivers/postgres/internal/config.go"
    )
    titles = [s.title for s in doc.sections]
    assert "Config" in titles and "Config.Validate" in titles
    validate = next(s for s in doc.sections if s.title == "Config.Validate")
    assert validate.body.startswith("```go\n")
    assert validate.line_start < validate.line_end

    keys = {s.name for s in doc.symbols if s.kind == SymbolKind.CONFIG_KEY}
    assert {"max_threads", "replication_slot", "update_method", "jdbc_url_params"} <= keys
    errors = {s.name for s in doc.symbols if s.kind == SymbolKind.ERROR_STRING}
    assert "database name is required" in errors
    # every symbol points at the section that contains its line
    by_locator = {s.locator: s for s in doc.sections}
    for sym in doc.symbols:
        sec = by_locator[sym.locator]
        assert sec.line_start <= sym.line <= sec.line_end


def test_go_splits_long_declarations():
    body = "\n".join(f"    x{i} := {i}" for i in range(200))
    doc = parse_file(f"package p\n\nfunc Big() {{\n{body}\n}}\n", "big.go")
    assert [s.title for s in doc.sections] == ["Big (part 1/3)", "Big (part 2/3)", "Big (part 3/3)"]
    assert all(s.body.startswith("```go") and s.body.endswith("```") for s in doc.sections)


YAML = """# Global settings shared by all components.
global:
  # Storage class for job data.
  storageClass: standard
  image:
    tag: v0.12.1

# The OLake UI deployment.
olakeUI:
  replicaCount: 1
"""


def test_yaml_keeps_comments_with_their_key():
    doc = parse_file(YAML, "helm/olake/values.yaml")
    assert [s.title for s in doc.sections] == ["global", "olakeUI"]
    assert doc.sections[0].body.startswith("```yaml\n# Global settings")
    assert doc.sections[1].line_start == 8
    assert {s.name for s in doc.symbols} == {"global", "olakeUI"}


def test_yaml_splits_big_blocks_by_second_level_key():
    big = "root:\n" + "".join(f"  key{i}:\n    value: {i}\n" for i in range(80))
    doc = parse_file(big, "values.yaml")
    assert len(doc.sections) > 1
    assert "root.key0" in {s.name for s in doc.symbols}


def test_markdown_sections_have_line_ranges():
    text = "# Title\n\nIntro.\n\n## Install\n\nRun it.\n\n## Usage\n\nUse it.\n"
    doc = parse_file(text, "README.md")
    assert doc.title == "Title"
    assert [(s.title, s.line_start) for s in doc.sections] == [
        ("Title", 1),
        ("Install", 5),
        ("Usage", 9),
    ]
    assert doc.sections[1].heading_path == ["Title", "Install"]
