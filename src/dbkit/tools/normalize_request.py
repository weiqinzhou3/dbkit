from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from dbkit.schemas.runtime import NormalizedRequest


def normalize_request(
    user_input: str,
    *,
    llm_json: dict[str, Any] | None = None,
    redaction_summary: dict[str, Any] | None = None,
) -> NormalizedRequest:
    text = user_input.strip()
    if not text:
        raise ValueError("user_input is required")

    request_id = _request_id(text)

    if llm_json:
        target_domain = llm_json.get("target_domain") or "mysql"
        target_agent = llm_json.get("target_agent") or "mysql_analyzer"
        task_type = llm_json.get("task_type") or "unknown"
        routing_confidence = llm_json.get("routing_confidence") or 0.9
        target = llm_json.get("target") or None
        ssh_target = llm_json.get("ssh_target") or None
        event = _build_event(llm_json)
        normalizer = "llm_intake_plus_normalize_request"
    else:
        target_domain = "mysql" if "mysql" in text.lower() else "mysql"
        target_agent = "mysql_analyzer"
        task_type = "unknown"
        routing_confidence = 0.9
        target = None
        ssh_target = None
        event = None
        normalizer = "deterministic_fallback"

    missing_fields = _detect_missing_fields(target, event, task_type)
    evidence_plan = _generate_evidence_plan(event)

    return NormalizedRequest(
        request_id=request_id,
        original_input=text,
        redacted_input=text,
        target_domain=target_domain,
        requested_capability="runtime_intake",
        missing_fields=missing_fields,
        phase="phase-01.1",
        target_agent=target_agent,
        task_type=task_type,
        routing_confidence=routing_confidence,
        target=target,
        ssh_target=ssh_target,
        event=event,
        evidence_plan=evidence_plan,
        redaction_summary=redaction_summary,
        metadata={
            "normalizer": normalizer,
            "skill": "skills/intake/SKILL.md",
        },
    )


def _request_id(text: str) -> str:
    digest = sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"req_{digest}"


def _build_event(llm_json: dict[str, Any]) -> dict[str, Any] | None:
    event = llm_json.get("event")
    if not event:
        return None

    event_time = event.get("event_time")
    time_window = event.get("time_window")
    task_type = llm_json.get("task_type", "unknown")

    if event_time and not time_window:
        time_window = _infer_time_window(event_time, task_type)

    result = dict(event)
    if time_window:
        result["time_window"] = time_window

    return result


def _infer_time_window(event_time_str: str, task_type: str) -> dict[str, Any] | None:
    try:
        dt = datetime.fromisoformat(event_time_str)
    except (ValueError, TypeError):
        return None

    if task_type in ("alert_analysis", "incident_analysis"):
        before_h, after_h = 6, 1
    else:
        return None

    start = dt - timedelta(hours=before_h)
    end = dt + timedelta(hours=after_h)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source": "skill_default_from_event_time",
        "before": f"{before_h}h",
        "after": f"{after_h}h",
    }


def _detect_missing_fields(
    target: dict[str, Any] | None,
    event: dict[str, Any] | None,
    task_type: str | None,
) -> tuple[str, ...]:
    missing: list[str] = []

    if not (target and target.get("host")):
        missing.append("target.host")
    if not (target and target.get("username")):
        missing.append("target.username")

    if task_type in ("alert_analysis", "incident_analysis"):
        event_time = event.get("event_time") if event else None
        if not event_time:
            missing.append("event.event_time")

    return tuple(missing)


def _generate_evidence_plan(event: dict[str, Any] | None) -> dict[str, Any]:
    symptoms = event.get("symptoms", []) if event else []

    required: list[str] = ["mysql.runtime_status", "mysql.processlist"]

    if any(s in ("high_cpu", "cpu") for s in symptoms):
        required.append("metrics.cpu")
    if any(s in ("slow_query", "high_latency", "slow") for s in symptoms):
        required.append("mysql.slow_log")
    if any(s in ("error", "crash", "connection_refused", "connection_spike") for s in symptoms):
        required.append("mysql.error_log")
    if any(s in ("replication", "lag", "replica") for s in symptoms):
        required.append("mysql.replication_status")

    if len(required) == 2:
        required.extend(["mysql.slow_log", "mysql.error_log"])

    return {
        "required_evidence": required,
        "provided_evidence": [],
        "missing_evidence": [],
    }


def normalize_request_tool(user_input: str) -> dict[str, object]:
    """Normalize a DBKit Phase 01.1 intake request into structured fields."""
    return normalize_request(user_input).to_dict()
