from __future__ import annotations

import json
from pathlib import Path

from dbkit.schemas.runtime import ArtifactRecord, NormalizedRequest


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def persist_request(self, request: NormalizedRequest) -> ArtifactRecord:
        path = self.root / f"{request.request_id}.normalized-request.json"
        path.write_text(
            json.dumps(request.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="NormalizedRequest", path=path)
