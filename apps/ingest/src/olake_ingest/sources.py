"""Load and validate config/sources.yaml.

Two kinds of source:
- git:  a GitHub repo checked out at a ref; include/exclude are file globs.
- site: a website read through its sitemap; include/exclude are URL-path globs.
"""

import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from olake_ingest.config import SOURCES_FILE
from olake_shared import Product, SourceType

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class _SourceBase(BaseModel):
    name: str
    product: Product | Literal["auto"]
    source_type: SourceType | Literal["auto"]
    trust_tier: Literal[1, 2, 3, 4]
    include: list[str] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)


class GitSource(_SourceBase):
    kind: Literal["git"]
    repo: str  # "owner/name" on GitHub
    ref: str = "master"

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, value: str) -> str:
        if not REPO_PATTERN.match(value):
            raise ValueError(f"repo must look like 'owner/name', got {value!r}")
        return value

    @field_validator("include", "exclude")
    @classmethod
    def _relative_patterns(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            if p.startswith("/") or ".." in Path(p).parts:
                raise ValueError(f"pattern must stay inside the repo: {p!r}")
        return patterns

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.repo}.git"


class SiteSource(_SourceBase):
    kind: Literal["site"]
    site: str  # e.g. "https://olake.io": scheme and host only
    sitemap: str = "/sitemap.xml"

    @field_validator("site")
    @classmethod
    def _site_shape(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/"):
            raise ValueError(f"site must look like 'https://host', got {value!r}")
        return f"https://{parts.netloc}"

    @field_validator("include", "exclude")
    @classmethod
    def _path_patterns(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            if not p.startswith("/") or ".." in p.split("/"):
                raise ValueError(f"site pattern must be an absolute URL path: {p!r}")
        return patterns

    @property
    def host(self) -> str:
        return urlsplit(self.site).netloc


SourceConfig = Annotated[GitSource | SiteSource, Field(discriminator="kind")]


class SourcesFile(BaseModel):
    sources: list[SourceConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "SourcesFile":
        names = [s.name for s in self.sources]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate source names: {sorted(duplicates)}")

        # One checkout per repo, so every git source on a repo must ask for the same ref.
        refs: dict[str, str] = {}
        for s in self.sources:
            if isinstance(s, GitSource) and refs.setdefault(s.repo, s.ref) != s.ref:
                raise ValueError(
                    f"{s.repo} is requested at two refs ({refs[s.repo]!r} and {s.ref!r})"
                )
        return self


def load_sources(
    path: Path = SOURCES_FILE, only: list[str] | None = None
) -> list[GitSource | SiteSource]:
    """Parse the file, validate it, and optionally keep only the named sources."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = SourcesFile.model_validate(data).sources
    if only:
        unknown = set(only) - {s.name for s in sources}
        if unknown:
            raise ValueError(f"unknown source names: {sorted(unknown)}")
        sources = [s for s in sources if s.name in only]
    return sources
