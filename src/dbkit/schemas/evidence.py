from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any


_FORBIDDEN_EVIDENCE_REQUEST_KEYS = frozenset(
    {"root_cause", "findings", "verdict", "summary", "recommended_remediation"}
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
    evidence_request: EvidenceRequest
    collection_plan: CollectionPlan
    raw_evidence: tuple[RawEvidence, ...]
    artifacts: tuple[Any, ...]
    telemetry: tuple[Any, ...]
    blocking_issues: tuple[str, ...] = ()


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


def stable_id(prefix: str, *parts: object) -> str:
    digest = sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:12]
    return f"{prefix}_{digest}"
