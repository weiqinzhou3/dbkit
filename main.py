from __future__ import annotations

import sys
from pathlib import Path
from collections.abc import Sequence

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbkit.cli import main as dbkit_main


def main(argv: Sequence[str] | None = None) -> int:
    return dbkit_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
