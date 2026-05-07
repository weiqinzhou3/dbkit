from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NormalizedRequest:
    request_id: str
    original_input: str
    redacted_input: str
    target_domain: str
    requested_capability: str
    missing_fields: tuple[str, ...]
    phase: str = "phase-01.1"
    target_agent: str | None = None
    task_type: str | None = None
    routing_confidence: float | None = None
    target: dict[str, Any] | None = None
    ssh_target: dict[str, Any] | None = None
    event: dict[str, Any] | None = None
    evidence_plan: dict[str, Any] | None = None
    redaction_summary: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["missing_fields"] = list(self.missing_fields)
        return payload


@dataclass(frozen=True)
class GuardrailsResult:
    passed: bool
    normalized_request: NormalizedRequest
    blocking_issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "normalized_request": self.normalized_request.to_dict(),
            "blocking_issues": list(self.blocking_issues),
        }


@dataclass(frozen=True)
class RouteDecision:
    target_agent_name: str
    target_domain: str
    phase: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryEvent:
    event_type: str
    stage: str
    message: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactRecord:
    kind: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "path": str(self.path)}


@dataclass(frozen=True)
class RuntimeResult:
    normalized_request: NormalizedRequest
    route_decision: RouteDecision | None
    artifacts: tuple[ArtifactRecord, ...]
    telemetry: tuple[TelemetryEvent, ...]
    deepagents_runtime_ready: bool
    blocked: bool = False
    blocking_issues: tuple[str, ...] = field(default_factory=tuple)
