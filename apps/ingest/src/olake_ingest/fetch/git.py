"""Shallow, repeatable checkouts of GitHub repos with the system `git` binary."""

import re
import subprocess
from pathlib import Path

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")  # v1.2.3 only; v1.2.3-rc1 is skipped
LATEST_RELEASE = "latest-release"


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: Path | None = None, timeout: int = 600) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def pick_latest_release(tags: list[str]) -> str | None:
    """Highest vX.Y.Z tag by numeric comparison, so v0.10.0 beats v0.9.9."""
    releases = [(tuple(int(n) for n in m.groups()), t) for t in tags if (m := RELEASE_TAG.match(t))]
    return max(releases)[1] if releases else None


def resolve_ref(url: str, ref: str) -> str:
    """Turn `latest-release` into a concrete tag. Other refs pass through unchanged."""
    if ref != LATEST_RELEASE:
        return ref
    output = _git("ls-remote", "--tags", "--refs", url, timeout=60)
    tags = [line.split("refs/tags/", 1)[1] for line in output.splitlines() if "refs/tags/" in line]
    tag = pick_latest_release(tags)
    if tag is None:
        raise GitError(f"{url} has no vX.Y.Z release tags")
    return tag


def checkout(url: str, ref: str, dest: Path) -> str:
    """Make `dest` a depth-1 checkout of `ref` and return the commit SHA.

    Works the same for a first clone and for later updates: init once, then
    fetch exactly one commit and force-check it out. Local edits are discarded,
    which is what we want for a read-only mirror.
    """
    if not (dest / ".git").is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        _git("init", "--quiet", cwd=dest)
        _git("remote", "add", "origin", url, cwd=dest)
    else:
        _git("remote", "set-url", "origin", url, cwd=dest)

    _git("fetch", "--quiet", "--depth", "1", "--force", "origin", ref, cwd=dest)
    _git("checkout", "--quiet", "--force", "--detach", "FETCH_HEAD^{commit}", cwd=dest)
    _git("clean", "-ffdxq", cwd=dest)
    return _git("rev-parse", "HEAD", cwd=dest)
