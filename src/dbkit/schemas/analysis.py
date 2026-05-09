from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALLOWED_FINDING_CATEGORIES = frozenset(
    {
        "connection",
        "availability",
        "performance",
        "high_cpu",
        "lock_contention",
        "slow_query",
        "configuration",
        "resource_pressure",
        "log_signal",
        "service_state",
        "unknown",
    }
)
ALLOWED_SEVERITIES = ("critical", "high", "medium", "low", "info")
ALLOWED_VALIDATION_STATUSES = frozenset(
    {"passed", "downgraded", "blocked", "requires_human_review"}
)
FORBIDDEN_ANALYSIS_KEYS = frozenset(
    {"raw_evidence", "raw_logs", "root_cause_execution", "remediation_executed"}
)


@dataclass(frozen=True)
class Phase04AnalysisResult:
    request_id: str
    phase: str
    status: str
    findings_draft: dict[str, Any] | None
    validation_result: dict[str, Any] | None
    verdict: dict[str, Any] | None
    summary: str | None
    artifacts: tuple[Any, ...]
    telemetry: tuple[Any, ...]
    blocking_issues: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def validate_evidence_bundle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("EvidenceBundle must be an object")
    if not payload.get("request_id"):
        raise ValueError("EvidenceBundle.request_id is required")
    if payload.get("phase") != "phase-03":
        raise ValueError("EvidenceBundle.phase must be phase-03")
    evidence_items = payload.get("evidence_items")
    if not isinstance(evidence_items, list):
        raise ValueError("EvidenceBundle.evidence_items must be a list")
    for item in evidence_items:
        if not isinstance(item, dict) or not item.get("evidence_id"):
            raise ValueError("EvidenceBundle evidence item evidence_id is required")
        if not item.get("evidence_type"):
            raise ValueError("EvidenceBundle evidence item evidence_type is required")
    return payload


def validate_findings_draft(payload: dict[str, Any], evidence_bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("FindingsDraft must be an object")
    _reject_forbidden_keys(payload, "FindingsDraft")
    if payload.get("phase") != "phase-04":
        raise ValueError("FindingsDraft.phase must be phase-04")
    if payload.get("mode") != "findings_generation":
        raise ValueError("FindingsDraft.mode must be findings_generation")
    if payload.get("target_agent") != "mysql_analyzer":
        raise ValueError("FindingsDraft.target_agent must be mysql_analyzer")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("FindingsDraft.findings must be a list")
    for finding in findings:
        _validate_finding_shape(finding)
    payload.setdefault("request_id", evidence_bundle["request_id"])
    payload.setdefault("insufficient_evidence", [])
    payload.setdefault("metadata", {})
    return payload


def validate_validation_result(payload: dict[str, Any], findings_draft: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("ValidationResult must be an object")
    _reject_forbidden_keys(payload, "ValidationResult")
    if payload.get("phase") != "phase-04":
        raise ValueError("ValidationResult.phase must be phase-04")
    for field_name in ("validated_findings", "blocked_findings", "downgraded_findings"):
        if not isinstance(payload.get(field_name), list):
            raise ValueError(f"ValidationResult.{field_name} must be a list")
    for item in payload["validated_findings"]:
        if item.get("validation_status") not in ALLOWED_VALIDATION_STATUSES:
            raise ValueError("ValidationResult validation_status is invalid")
        confidence = item.get("confidence_after_validation")
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise ValueError("confidence_after_validation must be between 0 and 1")
    payload.setdefault("request_id", findings_draft["request_id"])
    payload.setdefault("requires_human_review", False)
    payload.setdefault(
        "validation_summary",
        {
            "passed": len(payload["validated_findings"]),
            "blocked": len(payload["blocked_findings"]),
            "downgraded": len(payload["downgraded_findings"]),
        },
    )
    return payload


def evidence_id_set(evidence_bundle: dict[str, Any]) -> set[str]:
    return {
        str(item["evidence_id"])
        for item in evidence_bundle.get("evidence_items", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }


def finding_by_id(findings_draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(finding["finding_id"]): finding
        for finding in findings_draft.get("findings", [])
        if isinstance(finding, dict) and finding.get("finding_id")
    }


def _validate_finding_shape(finding: Any) -> None:
    if not isinstance(finding, dict):
        raise ValueError("Finding must be an object")
    for field_name in ("finding_id", "title", "category", "severity", "confidence", "status", "statement"):
        if field_name not in finding:
            raise ValueError(f"Finding.{field_name} is required")
    if finding["category"] not in ALLOWED_FINDING_CATEGORIES:
        raise ValueError("Finding.category is invalid")
    if finding["severity"] not in ALLOWED_SEVERITIES:
        raise ValueError("Finding.severity is invalid")
    if not 0 <= float(finding["confidence"]) <= 1:
        raise ValueError("Finding.confidence must be between 0 and 1")
    evidence_refs = finding.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise ValueError("Finding.evidence_refs is required")
    for ref in evidence_refs:
        if not isinstance(ref, dict) or not ref.get("evidence_id"):
            raise ValueError("Finding.evidence_refs[].evidence_id is required")
        if not ref.get("evidence_type"):
            raise ValueError("Finding.evidence_refs[].evidence_type is required")


def _reject_forbidden_keys(payload: dict[str, Any], name: str) -> None:
    forbidden = sorted(key for key in FORBIDDEN_ANALYSIS_KEYS if key in payload)
    if forbidden:
        raise ValueError(f"{name} contains forbidden keys: {','.join(forbidden)}")
