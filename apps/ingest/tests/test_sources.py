from pathlib import Path

import pytest
from pydantic import ValidationError

from olake_ingest.config import SOURCES_FILE
from olake_ingest.sources import GitSource, SiteSource, SourcesFile, load_sources


def _git(**overrides) -> dict:
    base = {
        "name": "code",
        "kind": "git",
        "repo": "datazip-inc/olake",
        "ref": "master",
        "product": "olake-go",
        "source_type": "code",
        "trust_tier": 1,
        "include": ["drivers/**/*.go"],
    }
    base.update(overrides)
    return base


def _site(**overrides) -> dict:
    base = {
        "name": "docs",
        "kind": "site",
        "site": "https://olake.io",
        "product": "auto",
        "source_type": "auto",
        "trust_tier": 1,
        "include": ["/docs/**"],
    }
    base.update(overrides)
    return base


def test_real_sources_file_is_valid():
    sources = load_sources(SOURCES_FILE)
    assert [s.name for s in sources] == ["docs", "guides", "olake-code", "ui", "helm", "fusion"]
    assert [s.kind for s in sources] == ["site", "site", "git", "git", "git", "git"]


def test_kind_selects_the_right_model():
    parsed = SourcesFile.model_validate({"sources": [_git(), _site()]}).sources
    assert isinstance(parsed[0], GitSource)
    assert isinstance(parsed[1], SiteSource)


def test_only_filters_and_rejects_unknown_names():
    assert [s.name for s in load_sources(SOURCES_FILE, only=["helm"])] == ["helm"]
    with pytest.raises(ValueError, match="unknown source names"):
        load_sources(SOURCES_FILE, only=["nope"])


def test_same_repo_at_two_refs_is_rejected():
    with pytest.raises(ValidationError, match="two refs"):
        SourcesFile.model_validate({"sources": [_git(), _git(name="more", ref="dev")]})


def test_two_sites_on_one_host_are_fine():
    SourcesFile.model_validate({"sources": [_site(), _site(name="guides", include=["/blog/*/"])]})


def test_duplicate_names_are_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        SourcesFile.model_validate({"sources": [_git(), _git()]})


@pytest.mark.parametrize(
    "bad",
    [
        _git(repo="olake"),  # missing owner
        _git(product="spark"),  # not in the taxonomy
        _git(trust_tier=5),
        _git(include=[]),
        _git(include=["../../etc/*"]),  # escapes the repo
        _git(include=["/drivers/**"]),  # git globs are relative
        _git(kind="svn"),  # unknown kind
        _site(site="http://olake.io"),  # https only
        _site(site="https://olake.io/docs"),  # host only, no path
        _site(include=["docs/**"]),  # site globs are absolute paths
        _site(include=["/docs/../admin/"]),
    ],
)
def test_invalid_entries_are_rejected(bad: dict):
    with pytest.raises(ValidationError):
        SourcesFile.model_validate({"sources": [bad]})


def test_site_is_normalised():
    [source] = SourcesFile.model_validate({"sources": [_site(site="https://olake.io/")]}).sources
    assert source.site == "https://olake.io"
    assert source.host == "olake.io"


def test_load_from_custom_path(tmp_path: Path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n"
        "  - name: x\n"
        "    kind: git\n"
        "    repo: a/b\n"
        "    product: helm\n"
        "    source_type: config\n"
        "    trust_tier: 2\n"
        "    include: ['*.yaml']\n"
    )
    [source] = load_sources(path)
    assert source.clone_url == "https://github.com/a/b.git"
