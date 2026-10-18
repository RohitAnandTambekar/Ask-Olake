from pathlib import Path

from olake_ingest.fetch.select import MAX_FILE_BYTES, select_files


def _write(root: Path, rel: str, text: str = "x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_include_exclude_and_sorting(tmp_path: Path):
    for rel in [
        "docs/intro.mdx",
        "docs/connectors/postgres/index.mdx",
        "docs/shared/snippet.mdx",
        "docs/notes.txt",
    ]:
        _write(tmp_path, rel)

    sel = select_files(tmp_path, ["docs/**/*.mdx"], ["docs/shared/**"])

    assert sel.files == ["docs/connectors/postgres/index.mdx", "docs/intro.mdx"]
    assert sel.excluded == 1
    assert sel.empty_patterns == []


def test_reports_patterns_that_match_nothing(tmp_path: Path):
    _write(tmp_path, "README.md")
    sel = select_files(tmp_path, ["README.md", "helm/olake/values.yaml"], [])
    assert sel.files == ["README.md"]
    assert sel.empty_patterns == ["helm/olake/values.yaml"]


def test_skips_git_dir_large_files_and_symlinks(tmp_path: Path):
    _write(tmp_path, ".git/config.md")
    _write(tmp_path, "big.md", "x" * (MAX_FILE_BYTES + 1))
    _write(tmp_path, "ok.md")
    (tmp_path / "link.md").symlink_to("/etc/hostname")

    sel = select_files(tmp_path, ["**/*.md"], [])

    assert sel.files == ["ok.md"]
    assert sel.skipped_large == ["big.md"]
    assert sel.skipped_symlinks == ["link.md"]
