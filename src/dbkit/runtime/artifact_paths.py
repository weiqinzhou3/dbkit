from __future__ import annotations

from pathlib import Path

_REPO_VIRTUAL_PREFIX = "/repo/"


def to_deepagents_repo_virtual_path(path: str | Path, *, repo_dir: Path) -> str:
    raw = str(path)
    if raw == "/repo":
        return "/repo/"
    if raw.startswith(_REPO_VIRTUAL_PREFIX):
        return raw
    relative = to_repo_relative_path(path, repo_dir=repo_dir)
    return _REPO_VIRTUAL_PREFIX + relative.lstrip("/")


def to_repo_relative_path(path: str | Path, *, repo_dir: Path) -> str:
    raw = str(path)
    if raw.startswith(_REPO_VIRTUAL_PREFIX):
        return raw.removeprefix(_REPO_VIRTUAL_PREFIX).lstrip("/")
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.relative_to(repo_dir.absolute()).as_posix()
        except ValueError:
            return candidate.as_posix().lstrip("/")
    return candidate.as_posix().lstrip("/")


def to_host_path(path: str | Path, *, repo_dir: Path) -> Path:
    raw = str(path)
    if raw.startswith(_REPO_VIRTUAL_PREFIX):
        return repo_dir / raw.removeprefix(_REPO_VIRTUAL_PREFIX)
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return repo_dir / candidate
