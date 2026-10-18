import subprocess
from pathlib import Path

import pytest

from olake_ingest.fetch.git import GitError, checkout, pick_latest_release


def test_pick_latest_release_compares_numbers_and_skips_prereleases():
    tags = ["v0.9.9", "v0.10.0", "v0.12.1", "v0.13.0-rc1", "v0.3.2.rc", "olake-0.0.25"]
    assert pick_latest_release(tags) == "v0.12.1"
    assert pick_latest_release(["olake-0.0.25"]) is None


def _run(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """A local repo with two commits; the first is tagged v1.0.0."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    _run("init", "--quiet", "--initial-branch=master", cwd=repo)
    (repo / "a.md").write_text("one")
    _run("add", ".", cwd=repo)
    _run("commit", "--quiet", "-m", "one", cwd=repo)
    _run("tag", "-a", "v1.0.0", "-m", "release", cwd=repo)
    (repo / "a.md").write_text("two")
    _run("commit", "--quiet", "-am", "two", cwd=repo)
    return repo


def test_checkout_branch_then_tag_then_back(tmp_path: Path, upstream: Path):
    url = upstream.as_uri()  # file:// so --depth is honoured
    dest = tmp_path / "mirror"
    head = _run("rev-parse", "HEAD", cwd=upstream)
    tagged = _run("rev-parse", "v1.0.0^{commit}", cwd=upstream)

    assert checkout(url, "master", dest) == head
    assert (dest / "a.md").read_text() == "two"

    assert checkout(url, "v1.0.0", dest) == tagged  # annotated tag peels to its commit
    assert (dest / "a.md").read_text() == "one"

    (dest / "junk.txt").write_text("local edit")  # local changes must not survive
    assert checkout(url, "master", dest) == head
    assert not (dest / "junk.txt").exists()


def test_unknown_ref_raises_git_error(tmp_path: Path, upstream: Path):
    with pytest.raises(GitError, match="failed"):
        checkout(upstream.as_uri(), "no-such-branch", tmp_path / "mirror")
