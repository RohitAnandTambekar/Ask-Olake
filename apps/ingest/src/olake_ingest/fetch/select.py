"""Pick the files a source wants from a checkout, using its include/exclude globs."""

from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 1_000_000  # bigger files are generated or vendored; not worth indexing


@dataclass
class Selection:
    files: list[str] = field(default_factory=list)  # repo-relative POSIX paths, sorted
    excluded: int = 0  # matched include but also matched exclude
    skipped_large: list[str] = field(default_factory=list)
    skipped_symlinks: list[str] = field(default_factory=list)
    empty_patterns: list[str] = field(default_factory=list)  # include patterns matching nothing


def _glob(root: Path, pattern: str) -> dict[str, Path]:
    """Matches for one pattern, keyed by repo-relative path, never inside .git/."""
    found: dict[str, Path] = {}
    for path in root.glob(pattern):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue
        # Check symlinks before is_file(): is_file() follows the link.
        if path.is_symlink() or path.is_file():
            found[rel.as_posix()] = path
    return found


def select_files(root: Path, include: list[str], exclude: list[str]) -> Selection:
    sel = Selection()

    included: dict[str, Path] = {}
    for pattern in include:
        matches = _glob(root, pattern)
        if not matches:
            sel.empty_patterns.append(pattern)
        included.update(matches)

    excluded: set[str] = set()
    for pattern in exclude:
        excluded.update(_glob(root, pattern))

    for rel, path in sorted(included.items()):
        if rel in excluded:
            sel.excluded += 1
        elif path.is_symlink():
            sel.skipped_symlinks.append(rel)  # could point outside the repo
        elif path.stat().st_size > MAX_FILE_BYTES:
            sel.skipped_large.append(rel)
        else:
            sel.files.append(rel)
    return sel
