from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any


_FORBIDDEN_EVIDENCE_REQUEST_KEYS = frozenset(
    {"root_cause", "findings", "verdict", "summary", "recommended_remediation"}
)
_CANONICAL_EVIDENCE_TYPES = frozenset(
    {
        "mysql.runtime_status",
        "mysql.processlist",
        "mysql.innodb_status",
        "mysql.variables",
        "mysql.error_log",
        "mysql.slow_log",
        "mysql.service_metadata",
        "mysql.log_paths",
        "metrics.cpu",
        "metrics.memory",
        "metrics.disk",
        "metrics.mysql",
        "metrics.mysql_status",
        "metrics.mysql_variables",
        "metrics.os_cpu",
        "metrics.os_memory",
        "metrics.os_disk",
        "os.mysql_service_status",
        "os.system_log",
        "provided.file",
    }
)
_EVIDENCE_TYPE_ALIASES = {
    "mysql_processlist": "mysql.processlist",
    "mysql_runtime_status": "mysql.runtime_status",
    "mysql_status": "mysql.runtime_status",
    "mysql.status": "mysql.runtime_status",
    "mysql_innodb_status": "mysql.innodb_status",
    "mysql_variables": "mysql.variables",
    "mysql_error_log": "mysql.error_log",
    "mysql_slow_log": "mysql.slow_log",
    "mysql_service_metadata": "mysql.service_metadata",
    "mysql_log_paths": "mysql.log_paths",
    "os.cpu_metrics": "metrics.cpu",
    "provided_evidence.file": "provided.file",
    "provided_evidence.directory": "provided.file",
}
_ALLOWED_SOURCES = frozenset(
    {"mysql", "ssh", "metrics", "file", "provided_evidence"}
)
_TOOL_SOURCE_DEFAULTS = {
    "collect_mysql_runtime_status": "mysql",
    "collect_processlist": "mysql",
    "collect_innodb_status": "mysql",
    "collect_mysql_variables": "mysql",
    "collect_mysql_error_log": "ssh",
    "collect_mysql_slow_log": "ssh",
    "collect_mysql_processlist": "mysql",
    "collect_mysql_service_metadata": "mysql",
    "discover_mysql_log_paths": "mysql",
    "collect_mysql_metrics_snapshot": "mysql",
    "collect_mysql_status_metrics": "mysql",
    "collect_mysql_variable_metrics": "mysql",
    "collect_metrics_snapshot": "metrics",
    "collect_os_service_status": "ssh",
    "collect_os_cpu_snapshot": "ssh",
    "collect_os_memory_snapshot": "ssh",
    "collect_os_disk_snapshot": "ssh",
    "read_remote_file": "ssh",
    "read_provided_evidence_file": "provided_evidence",
    "read_provided_evidence_directory": "provided_evidence",
}
_SUMMARY_STATUSES = (
    "collected",
    "partial",
    "failed",
    "blocked",
    "not_available",
    "not_configured",
    "not_implemented",
)


@dataclass(frozen=True)
class EvidenceRequest:
    request_id: str
    phase: str
    target_agent: str
    target_domain: str
    task_type: str
    input_mode: str
    reasoning_mode: str
    evidence_request: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CollectionStep:
    step_id: str
    evidence_type: str
    tool_name: str
    target_ref: str
    requires_secret_refs: tuple[str, ...]
    requires_approval: bool
    timeout_seconds: int
    purpose: str
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["requires_secret_refs"] = list(self.requires_secret_refs)
        return payload


@dataclass(frozen=True)
class CollectionPlan:
    request_id: str
    collection_plan_id: str
    phase: str
    input_mode: str
    steps: tuple[CollectionStep, ...]
    guardrails_status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "collection_plan_id": self.collection_plan_id,
            "phase": self.phase,
            "input_mode": self.input_mode,
            "steps": [step.to_dict() for step in self.steps],
            "guardrails_status": self.guardrails_status,
        }


@dataclass(frozen=True)
class RawEvidence:
    raw_evidence_id: str
    request_id: str
    evidence_type: str
    source: dict[str, Any]
    collection: dict[str, Any]
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CollectionGuardrailsResult:
    passed: bool
    blocking_issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidencePipelineResult:
    request_id: str
    phase: str
    status: str
    evidence_request: EvidenceRequest | None
    collection_plan: CollectionPlan | None
    raw_evidence: tuple[RawEvidence, ...]
    artifacts: tuple[Any, ...]
    telemetry: tuple[Any, ...]
    blocking_issues: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    raw_evidence_id: str
    evidence_type: str
    source: dict[str, Any]
    time_range: dict[str, Any]
    summary: str
    structured_payload: dict[str, Any]
    raw_refs: tuple[dict[str, Any], ...]
    quality_flags: tuple[str, ...] = ()
    llm_safe: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["raw_refs"] = list(self.raw_refs)
        payload["quality_flags"] = list(self.quality_flags)
        return payload


@dataclass(frozen=True)
class EvidenceBundle:
    request_id: str
    phase: str
    bundle_id: str
    input_raw_evidence_index: str
    source_raw_evidence_count: int
    processed_raw_evidence_count: int
    time_window: dict[str, Any]
    evidence_items: tuple[EvidenceItem, ...]
    coverage: dict[str, Any]
    quality: dict[str, Any]
    processing_summary: dict[str, Any]
    skipped_raw_evidence: tuple[dict[str, Any], ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "phase": self.phase,
            "bundle_id": self.bundle_id,
            "input_raw_evidence_index": self.input_raw_evidence_index,
            "source_raw_evidence_count": self.source_raw_evidence_count,
            "processed_raw_evidence_count": self.processed_raw_evidence_count,
            "time_window": self.time_window,
            "evidence_items": [item.to_dict() for item in self.evidence_items],
            "coverage": self.coverage,
            "quality": self.quality,
            "processing_summary": self.processing_summary,
            "skipped_raw_evidence": list(self.skipped_raw_evidence),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class EvidenceStructuringResult:
    request_id: str
    phase: str
    status: str
    bundle: EvidenceBundle | None
    bundle_artifact: Any | None
    artifacts: tuple[Any, ...]
    telemetry: tuple[Any, ...]
    blocking_issues: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def collection_summary(raw_evidence: tuple[RawEvidence, ...]) -> dict[str, int]:
    summary = {f"{status}_count": 0 for status in _SUMMARY_STATUSES}
    summary["raw_evidence_count"] = len(raw_evidence)
    for item in raw_evidence:
        status = str(item.collection.get("status") or "failed")
        key = f"{status}_count"
        if key in summary:
            summary[key] += 1
    return summary


def collection_status(raw_evidence: tuple[RawEvidence, ...]) -> str:
    summary = collection_summary(raw_evidence)
    collected = summary["collected_count"]
    warnings = (
        summary["failed_count"]
        + summary["not_available_count"]
        + summary["partial_count"]
        + summary["blocked_count"]
        + summary["not_configured_count"]
    )
    if collected > 0 and warnings == 0:
        return "raw_evidence_collected"
    if collected > 0:
        return "collection_completed_with_warnings"
    if summary["blocked_count"] > 0:
        return "collection_blocked"
    if summary["failed_count"] > 0:
        return "collection_failed"
    if summary["not_implemented_count"] > 0:
        return "collection_not_implemented"
    if summary["not_available_count"] > 0 or summary["not_configured_count"] > 0:
        return "collection_completed_with_warnings"
    return "collection_failed"


def validate_evidence_request(payload: dict[str, Any]) -> EvidenceRequest:
    forbidden = sorted(key for key in _FORBIDDEN_EVIDENCE_REQUEST_KEYS if key in payload)
    if forbidden:
        raise ValueError(
            "EvidenceRequest must not contain analysis output keys: "
            + ", ".join(forbidden)
        )

    if payload.get("phase") != "phase-02":
        raise ValueError("EvidenceRequest.phase must be phase-02")
    if payload.get("reasoning_mode") != "evidence_planning":
        raise ValueError("EvidenceRequest.reasoning_mode must be evidence_planning")
    if payload.get("target_agent") != "mysql_analyzer":
        raise ValueError("EvidenceRequest.target_agent must be mysql_analyzer")

    evidence_request = payload.get("evidence_request")
    if not isinstance(evidence_request, dict):
        raise ValueError("EvidenceRequest.evidence_request must be an object")
    for field_name in (
        "goal",
        "required_evidence",
        "optional_evidence",
        "not_required_evidence",
        "missing_inputs",
        "approval_requirements",
    ):
        if field_name not in evidence_request:
            raise ValueError(f"EvidenceRequest.evidence_request.{field_name} is required")
    if not isinstance(evidence_request["required_evidence"], list):
        raise ValueError("EvidenceRequest.evidence_request.required_evidence must be a list")
    evidence_request = _normalize_evidence_request_items(evidence_request)

    return EvidenceRequest(
        request_id=str(payload["request_id"]),
        phase="phase-02",
        target_agent=str(payload["target_agent"]),
        target_domain=str(payload.get("target_domain") or "mysql"),
        task_type=str(payload.get("task_type") or "unknown"),
        input_mode=str(payload.get("input_mode") or "unknown"),
        reasoning_mode="evidence_planning",
        evidence_request=dict(evidence_request),
        metadata=dict(payload.get("metadata") or {}),
    )


def _normalize_evidence_request_items(evidence_request: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(evidence_request)
    for field_name in (
        "required_evidence",
        "optional_evidence",
        "not_required_evidence",
    ):
        items = normalized.get(field_name)
        if isinstance(items, list):
            normalized[field_name] = [
                _normalize_evidence_request_item(item) if isinstance(item, dict) else item
                for item in items
            ]
    return normalized


def _normalize_evidence_request_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    evidence_type = str(normalized.get("evidence_type") or "")
    canonical_type = _EVIDENCE_TYPE_ALIASES.get(evidence_type, evidence_type)
    if canonical_type and canonical_type not in _CANONICAL_EVIDENCE_TYPES:
        raise ValueError(f"unknown EvidenceRequest evidence_type: {evidence_type}")
    normalized["evidence_type"] = canonical_type

    tool_hint = str(normalized.get("tool_hint") or "")
    source = str(normalized.get("source") or "")
    if source not in _ALLOWED_SOURCES:
        source = _TOOL_SOURCE_DEFAULTS.get(tool_hint, source)
    if not source:
        normalized["source"] = ""
        return normalized
    if source not in _ALLOWED_SOURCES:
        raise ValueError(f"unknown EvidenceRequest source: {normalized.get('source')}")
    normalized["source"] = source
    return normalized


def stable_id(prefix: str, *parts: object) -> str:
    digest = sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:12]
    return f"{prefix}_{digest}"
