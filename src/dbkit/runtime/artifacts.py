from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dbkit.schemas.evidence import (
    CollectionPlan,
    EvidenceBundle,
    EvidenceItem,
    EvidenceRequest,
    RawEvidence,
    collection_summary,
)
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

    def persist_evidence_request_failed(
        self,
        request: NormalizedRequest,
        *,
        reason: str,
        details: list[str] | None = None,
    ) -> ArtifactRecord:
        path = self.root / f"{request.request_id}.evidence-request-failed.json"
        payload: dict[str, Any] = {
            "request_id": request.request_id,
            "phase": "phase-02",
            "status": "blocked",
            "reason": reason,
            "details": details or [],
            "normalized_request": request.to_dict(),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="EvidenceRequestFailed", path=path)

    def persist_collection_plan(self, plan: CollectionPlan) -> ArtifactRecord:
        path = self.root / f"{plan.request_id}.collection-plan.json"
        path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="CollectionPlan", path=path)

    def persist_collection_blocked(
        self,
        request: NormalizedRequest,
        plan: CollectionPlan,
        *,
        reason: str,
        missing_dependencies: list[str] | None = None,
        install_hint: str | None = None,
    ) -> ArtifactRecord:
        path = self.root / f"{request.request_id}.collection-blocked.json"
        payload: dict[str, Any] = {
            "request_id": request.request_id,
            "phase": request.phase,
            "status": "blocked",
            "reason": reason,
            "missing_dependencies": missing_dependencies or [],
            "install_hint": install_hint,
            "collection_plan": plan.to_dict(),
            "normalized_request": request.to_dict(),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="CollectionBlocked", path=path)

    def persist_raw_evidence_index(
        self,
        request_id: str,
        raw_evidence: tuple[RawEvidence, ...],
        *,
        phase: str = "phase-02",
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.raw-evidence-index.json"
        payload: dict[str, Any] = {
            "request_id": request_id,
            "phase": phase,
            "metadata": _phase_metadata(phase),
            "raw_evidence": [_raw_evidence_index_entry(item) for item in raw_evidence],
            **collection_summary(raw_evidence),
        }
        raw_dir = self.root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for item in raw_evidence:
            content_ref = item.payload.get("content_ref")
            if content_ref and Path(str(content_ref)).exists():
                continue
            raw_path = raw_dir / f"{item.raw_evidence_id}.json"
            raw_path.write_text(
                json.dumps(
                    item.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
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

    def persist_evidence_bundle(self, bundle: EvidenceBundle) -> ArtifactRecord:
        path = self.root / f"{bundle.request_id}.evidence-bundle.json"
        path.write_text(
            json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="EvidenceBundle", path=path)

    def persist_evidence_item(self, item: EvidenceItem) -> ArtifactRecord:
        evidence_dir = self.root / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        path = evidence_dir / f"{item.evidence_id}.json"
        path.write_text(
            json.dumps(item.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="EvidenceItem", path=path)

    def persist_evidence_processing_telemetry(
        self,
        request_id: str,
        events: list[TelemetryEvent],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.evidence-processing-telemetry.jsonl"
        lines = [
            json.dumps(e.to_dict(), ensure_ascii=False, sort_keys=True)
            for e in events
        ]
        path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        return ArtifactRecord(kind="EvidenceProcessingTelemetry", path=path)

    def persist_findings_draft(
        self,
        request_id: str,
        findings_draft: dict[str, Any],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.findings-draft.json"
        path.write_text(
            json.dumps(findings_draft, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="FindingsDraft", path=path)

    def persist_invalid_findings_draft(
        self,
        request_id: str,
        payload: dict[str, Any],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.findings-draft.invalid.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="InvalidFindingsDraft", path=path)

    def persist_validation_result(
        self,
        request_id: str,
        validation_result: dict[str, Any],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.validation-result.json"
        path.write_text(
            json.dumps(validation_result, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="ValidationResult", path=path)

    def persist_invalid_validation_result(
        self,
        request_id: str,
        payload: dict[str, Any],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.validation-result.invalid.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="InvalidValidationResult", path=path)

    def persist_analysis_timeout(
        self,
        request_id: str,
        payload: dict[str, Any],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.analysis-timeout.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="AnalysisTimeout", path=path)

    def persist_compact_analysis_context(
        self,
        request_id: str,
        payload: dict[str, Any],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.compact-analysis-context.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="CompactAnalysisContext", path=path)

    def persist_verdict(
        self,
        request_id: str,
        verdict: dict[str, Any],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.verdict.json"
        path.write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRecord(kind="Verdict", path=path)

    def persist_summary(
        self,
        request_id: str,
        summary: str,
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.summary.md"
        path.write_text(summary, encoding="utf-8")
        return ArtifactRecord(kind="Summary", path=path)

    def persist_analysis_telemetry(
        self,
        request_id: str,
        events: list[TelemetryEvent],
    ) -> ArtifactRecord:
        path = self.root / f"{request_id}.analysis-telemetry.jsonl"
        lines = [
            json.dumps(e.to_dict(), ensure_ascii=False, sort_keys=True)
            for e in events
        ]
        path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        return ArtifactRecord(kind="AnalysisTelemetry", path=path)


def _raw_evidence_index_entry(item: RawEvidence) -> dict[str, Any]:
    payload = {
        key: item.payload.get(key)
        for key in ("content_ref", "bytes", "line_count")
        if key in item.payload
    }
    collection = {
        key: item.collection.get(key)
        for key in ("status", "errors", "reason", "duration_ms")
        if key in item.collection
    }
    entry: dict[str, Any] = {
        "raw_evidence_id": item.raw_evidence_id,
        "request_id": item.request_id,
        "evidence_type": item.evidence_type,
        "source": item.source,
        "collection": collection,
        "payload": payload,
        "metadata": item.metadata,
    }
    preview = _payload_preview(item.payload)
    if preview:
        entry["preview"] = preview
    return entry


def _phase_metadata(phase: str) -> dict[str, Any]:
    if phase.startswith("phase-02.1"):
        return {"phase_detail": "phase-02.1-real-mysql-evidence-collection"}
    return {}


def _payload_preview(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}

    if isinstance(data.get("rows"), list):
        rows = data["rows"]
        return {
            "rows_count": len(rows),
            "first_n_keys": _first_keys(rows),
        }

    if isinstance(data.get("queries"), list):
        queries = data["queries"]
        rows_count = 0
        query_count = 0
        for query in queries:
            if not isinstance(query, dict):
                continue
            query_count += 1
            rows = query.get("rows")
            if isinstance(rows, list):
                rows_count += len(rows)
        return {
            "queries_count": query_count,
            "rows_count": rows_count,
            "first_n_keys": _first_keys(
                [
                    row
                    for query in queries
                    if isinstance(query, dict)
                    for row in (query.get("rows") or [])
                    if isinstance(row, dict)
                ]
            ),
        }

    return {
        key: data[key]
        for key in (
            "error_log_path",
            "slow_log_path",
            "slow_query_log_enabled",
            "log_output",
            "datadir",
            "reason",
        )
        if key in data
    }


def _first_keys(rows: list[Any], limit: int = 5) -> list[str]:
    for row in rows:
        if isinstance(row, dict):
            return list(row.keys())[:limit]
    return []
