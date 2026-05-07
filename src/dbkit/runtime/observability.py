from __future__ import annotations

from dbkit.schemas.runtime import TelemetryEvent


class TelemetryRecorder:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(
        self,
        *,
        event_type: str,
        stage: str,
        message: str,
        attributes: dict[str, object] | None = None,
    ) -> TelemetryEvent:
        event = TelemetryEvent(
            event_type=event_type,
            stage=stage,
            message=message,
            attributes=dict(attributes or {}),
        )
        self.events.append(event)
        return event

    def emit_runtime_cost(
        self,
        *,
        stage: str,
        raw_bytes: int,
        filtered_bytes: int,
        compression_ratio: float,
        estimated_tokens: int,
        tool_latency_ms: float,
    ) -> TelemetryEvent:
        return self.emit(
            event_type="runtime_cost",
            stage=stage,
            message=f"{stage} runtime cost telemetry",
            attributes={
                "stage": stage,
                "raw_bytes": raw_bytes,
                "filtered_bytes": filtered_bytes,
                "compression_ratio": compression_ratio,
                "estimated_tokens": estimated_tokens,
                "tool_latency_ms": round(tool_latency_ms, 3),
            },
        )

    def emit_redaction_completed(
        self,
        *,
        request_id: str,
        secret_count: int,
        patterns: list[str],
        raw_bytes: int,
        filtered_bytes: int,
    ) -> TelemetryEvent:
        return self.emit(
            event_type="redaction_completed",
            stage="redactor",
            message="Input redaction completed",
            attributes={
                "request_id": request_id,
                "secret_count": secret_count,
                "redacted_patterns": patterns,
                "raw_bytes": raw_bytes,
                "filtered_bytes": filtered_bytes,
            },
        )

    def emit_intake_agent_started(self, *, request_id: str) -> TelemetryEvent:
        return self.emit(
            event_type="intake_agent_started",
            stage="intake",
            message="Intake agent invocation started",
            attributes={"request_id": request_id, "phase": "phase-01.1"},
        )

    def emit_intake_agent_completed(self, *, request_id: str) -> TelemetryEvent:
        return self.emit(
            event_type="intake_agent_completed",
            stage="intake",
            message="Intake agent invocation completed",
            attributes={"request_id": request_id},
        )

    def emit_intake_json_parse_failed(
        self, *, request_id: str, reason: str
    ) -> TelemetryEvent:
        return self.emit(
            event_type="intake_json_parse_failed",
            stage="intake",
            message="Failed to parse LLM JSON output; using deterministic fallback",
            attributes={
                "request_id": request_id,
                "reason": reason,
                "llm_intake_failed": True,
            },
        )

    def emit_normalize_request_started(self, *, request_id: str) -> TelemetryEvent:
        return self.emit(
            event_type="normalize_request_started",
            stage="normalize",
            message="normalize_request started",
            attributes={"request_id": request_id},
        )

    def emit_normalize_request_completed(
        self, *, request_id: str, missing_fields: list[str]
    ) -> TelemetryEvent:
        return self.emit(
            event_type="normalize_request_completed",
            stage="normalize",
            message="normalize_request completed",
            attributes={"request_id": request_id, "missing_fields": missing_fields},
        )

    def emit_guardrails_started(self, *, request_id: str) -> TelemetryEvent:
        return self.emit(
            event_type="request_guardrails_started",
            stage="guardrails",
            message="Request guardrails validation started",
            attributes={"request_id": request_id},
        )

    def emit_guardrails_passed(self, *, request_id: str) -> TelemetryEvent:
        return self.emit(
            event_type="request_guardrails_passed",
            stage="guardrails",
            message="Request guardrails validation passed",
            attributes={"request_id": request_id},
        )

    def emit_guardrails_blocked(
        self, *, request_id: str, blocking_issues: list[str]
    ) -> TelemetryEvent:
        return self.emit(
            event_type="request_guardrails_blocked",
            stage="guardrails",
            message="Request blocked by guardrails",
            attributes={
                "request_id": request_id,
                "blocking_issues": blocking_issues,
            },
        )

    def emit_route_selected(
        self, *, request_id: str, target_agent: str, target_domain: str
    ) -> TelemetryEvent:
        return self.emit(
            event_type="route_selected",
            stage="router",
            message=f"Route selected: {target_agent}",
            attributes={
                "request_id": request_id,
                "target_agent": target_agent,
                "target_domain": target_domain,
            },
        )

    def emit_artifact_written(
        self, *, request_id: str, kind: str, path: str
    ) -> TelemetryEvent:
        return self.emit(
            event_type="artifact_written",
            stage="artifacts",
            message=f"Artifact written: {kind}",
            attributes={"request_id": request_id, "kind": kind, "path": path},
        )
