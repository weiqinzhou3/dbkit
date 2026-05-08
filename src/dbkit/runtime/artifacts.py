from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dbkit.schemas.evidence import CollectionPlan, EvidenceRequest, RawEvidence
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

    def persist_evidence_request(
        self,
        evidence_request: EvidenceRequest,
    ) -> ArtifactRecord:
        path = self.root / f"{evidence_request.request_id}.evidence-request.json"
        path.write_text(
            json.dumps(
                evidence_request.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="EvidenceRequest", path=path)

    def persist_collection_plan(self, plan: CollectionPlan) -> ArtifactRecord:
        path = self.root / f"{plan.request_id}.collection-plan.json"
        path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="CollectionPlan", path=path)

    def persist_raw_evidence_index(
        self,
        request_id: str,
        raw_evidence: tuple[RawEvidence, ...],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.raw-evidence-index.json"
        payload: dict[str, Any] = {
            "request_id": request_id,
            "phase": "phase-02",
            "raw_evidence_count": len(raw_evidence),
            "raw_evidence": [item.to_dict() for item in raw_evidence],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="RawEvidenceIndex", path=path)

    def persist_collection_telemetry(
        self,
        request_id: str,
        events: list[TelemetryEvent],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.collection-telemetry.jsonl"
        lines = [
            json.dumps(e.to_dict(), ensure_ascii=False, sort_keys=True)
            for e in events
        ]
        path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        return ArtifactRecord(kind="CollectionTelemetry", path=path)
