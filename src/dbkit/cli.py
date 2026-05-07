from __future__ import annotations

from collections.abc import Sequence

from dbkit import __version__


def main(argv: Sequence[str] | None = None) -> int:
    _ = list(argv or [])
    print(f"DBKit {__version__}")
    return 0

