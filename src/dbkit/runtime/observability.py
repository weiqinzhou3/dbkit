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
