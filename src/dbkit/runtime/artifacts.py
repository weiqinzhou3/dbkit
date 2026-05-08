from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dbkit.schemas.runtime import ArtifactRecord, NormalizedRequest, TelemetryEvent


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def persist_request(self, request: NormalizedRequest) -> ArtifactRecord:
        path = self.root / f"{request.request_id}.normalized-request.json"
        path.write_text(
            json.dumps(request.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="NormalizedRequest", path=path)

    def persist_blocked_request(
        self,
        request: NormalizedRequest,
        blocking_issues: tuple[str, ...],
        *,
        user_message: dict[str, Any] | None = None,
        supplement_required: bool = True,
        supplement_fields: list[str] | None = None,
    ) -> ArtifactRecord:
        payload: dict[str, Any] = {
            "status": "blocked",
            "reason": "missing_required_fields"
            if any(issue.startswith("missing required field: ") for issue in blocking_issues)
            else "guardrails_failed",
            "blocking_issues": list(blocking_issues),
            "normalized_request": request.to_dict(),
            "user_message": user_message or {},
            "supplement_required": supplement_required,
            "supplement_fields": supplement_fields or [],
        }
        path = self.root / f"{request.request_id}.blocked-request.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="BlockedRequest", path=path)

    def persist_telemetry(
        self,
        request_id: str,
        events: list[TelemetryEvent],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.telemetry.jsonl"
        lines = [
            json.dumps(e.to_dict(), ensure_ascii=False, sort_keys=True)
            for e in events
        ]
        path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        return ArtifactRecord(kind="Telemetry", path=path)
