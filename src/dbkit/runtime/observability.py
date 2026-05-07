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
