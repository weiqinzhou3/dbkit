from __future__ import annotations

import re
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from dbkit.schemas.runtime import NormalizedRequest

_INPUT_MODES = frozenset({"live_collection", "provided_evidence", "hybrid", "unknown"})
_DEFAULT_COLLECTION_POLICY = {
    "allow_live_collection": False,
    "allow_mysql_login": False,
    "allow_ssh": False,
    "allow_metrics_query": False,
}
_MYSQL_URI = re.compile(
    r"(?i)\bmysql://(?P<username>[^:@/\s]+):(?P<password_ref><SECRET_REF:[^>]+>)@"
    r"(?P<host>[^:/\s]+)(?::(?P<port>\d+))?"
)


def normalize_request(
    user_input: str,
    *,
    llm_json: dict[str, Any] | None = None,
    redaction_summary: dict[str, Any] | None = None,
    llm_intake_failed: bool = False,
    fallback_reason: str | None = None,
    phase: str = "phase-01.2",
) -> NormalizedRequest:
    text = user_input.strip()
    if not text:
        raise ValueError("user_input is required")

    request_id = _request_id(text)

    if llm_json:
        target_domain = _string_or_default(llm_json.get("target_domain"), "mysql")
        target_agent = _string_or_default(llm_json.get("target_agent"), "mysql_analyzer")
        task_type = _string_or_default(llm_json.get("task_type"), "unknown")
        routing_confidence = _float_or_default(llm_json.get("routing_confidence"), 0.9)
        input_mode = _normalize_input_mode(llm_json.get("input_mode"))
        target = _optional_mapping(llm_json.get("target"))
        ssh_target = _optional_mapping(llm_json.get("ssh_target"))
        provided_evidence = _provided_evidence(llm_json.get("provided_evidence"))
        collection_policy = _collection_policy(llm_json.get("collection_policy"), input_mode)
        event = _build_event(llm_json)
        evidence_plan = _evidence_plan(llm_json.get("evidence_plan"))
        normalizer = "llm_intake_plus_normalize_request"
        llm_metadata = _optional_mapping(llm_json.get("metadata")) or {}
    else:
        target_domain = "mysql" if "mysql" in text.lower() else "mysql"
        target_agent = "mysql_analyzer"
        task_type = "unknown"
        routing_confidence = 0.9
        input_mode = "unknown"
        target = _extract_mysql_uri_target(text)
        ssh_target = None
        provided_evidence = _provided_evidence(None)
        collection_policy = _collection_policy(None, input_mode)
        event = None
        evidence_plan = _evidence_plan(None)
        normalizer = "deterministic_fallback"
        llm_metadata = {}

    missing_fields = _detect_missing_fields(
        input_mode=input_mode,
        target=target,
        ssh_target=ssh_target,
        provided_evidence=provided_evidence,
        collection_policy=collection_policy,
        event=event,
        task_type=task_type,
        llm_missing_fields=_missing_from_llm(llm_json),
    )

    metadata: dict[str, Any] = {
        "normalizer": normalizer,
        "skill": "skills/intake/SKILL.md",
        **llm_metadata,
    }
    if llm_intake_failed:
        metadata["llm_intake_failed"] = True
        metadata["fallback_reason"] = fallback_reason or "unknown"

    return NormalizedRequest(
        request_id=request_id,
        original_input=text,
        redacted_input=text,
        target_domain=target_domain,
        requested_capability="runtime_intake",
        missing_fields=missing_fields,
        phase=phase,
        target_agent=target_agent,
        task_type=task_type,
        routing_confidence=routing_confidence,
        input_mode=input_mode,
        target=target,
        ssh_target=ssh_target,
        provided_evidence=provided_evidence,
        collection_policy=collection_policy,
        event=event,
        evidence_plan=evidence_plan,
        redaction_summary=redaction_summary,
        metadata=metadata,
    )


def _request_id(text: str) -> str:
    digest = sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"req_{digest}"


def _build_event(llm_json: dict[str, Any]) -> dict[str, Any] | None:
    event = _optional_mapping(llm_json.get("event"))
    if not event:
        return None

    result = dict(event)
    event_time = result.get("event_time")
    time_window = _optional_mapping(result.get("time_window"))
    if event_time and time_window:
        result["time_window"] = _complete_time_window(event_time, time_window)
    elif time_window:
        result["time_window"] = time_window
    return result


def _complete_time_window(
    event_time_str: str,
    time_window: dict[str, Any],
) -> dict[str, Any]:
    result = dict(time_window)
    if result.get("start") and result.get("end"):
        return result

    before = _parse_duration_hours(result.get("before"))
    after = _parse_duration_hours(result.get("after"))
    if before is None and after is None:
        return result

    try:
        event_time = datetime.fromisoformat(str(event_time_str))
    except (TypeError, ValueError):
        return result

    if before is not None and not result.get("start"):
        result["start"] = (event_time - timedelta(hours=before)).isoformat()
    if after is not None and not result.get("end"):
        result["end"] = (event_time + timedelta(hours=after)).isoformat()
    return result


def _parse_duration_hours(value: object) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*h\s*", str(value), flags=re.I)
    if not match:
        return None
    return float(match.group(1))


def _detect_missing_fields(
    *,
    input_mode: str,
    target: dict[str, Any] | None,
    ssh_target: dict[str, Any] | None,
    provided_evidence: dict[str, Any] | None,
    collection_policy: dict[str, bool],
    event: dict[str, Any] | None,
    task_type: str | None,
    llm_missing_fields: tuple[str, ...],
) -> tuple[str, ...]:
    missing: list[str] = []

    for field in llm_missing_fields:
        if field.startswith("runtime_context."):
            continue
        if field == "event.event_time" and event and event.get("event_time"):
            continue
        if _missing_field_allowed_for_mode(field, input_mode, collection_policy):
            missing.append(field)

    if input_mode == "provided_evidence":
        if not _has_provided_evidence(provided_evidence):
            missing.append(_provided_evidence_missing_field(provided_evidence))
    elif input_mode in {"live_collection", "hybrid", "unknown"}:
        if collection_policy.get("allow_mysql_login") or input_mode in {"live_collection", "unknown"}:
            if not (target and target.get("host")):
                missing.append("target.host")
            if not (target and target.get("username")):
                missing.append("target.username")
            if input_mode == "live_collection" and target and not target.get("password_ref"):
                missing.append("target.password_ref")

        if collection_policy.get("allow_ssh"):
            if not (ssh_target and ssh_target.get("host")):
                missing.append("ssh_target.host")
            if not (ssh_target and ssh_target.get("username")):
                missing.append("ssh_target.username")

    if task_type in ("alert_analysis", "incident_analysis"):
        event_time = event.get("event_time") if event else None
        if not event_time:
            missing.append("event.event_time")

    return tuple(dict.fromkeys(missing))


def _missing_field_allowed_for_mode(
    field: str,
    input_mode: str,
    collection_policy: dict[str, bool],
) -> bool:
    if field.startswith("runtime_context."):
        return False
    if input_mode == "provided_evidence" and (
        field.startswith("target.") or field.startswith("ssh_target.")
    ):
        return False
    if field.startswith("ssh_target.") and not collection_policy.get("allow_ssh"):
        return False
    if field.startswith("target.") and input_mode == "hybrid" and not collection_policy.get("allow_mysql_login"):
        return False
    return True


def _has_provided_evidence(provided_evidence: dict[str, Any] | None) -> bool:
    if not provided_evidence:
        return False
    discovery = provided_evidence.get("discovery") or {}
    return bool(
        provided_evidence.get("files")
        or provided_evidence.get("pasted_text")
        or provided_evidence.get("input_files")
        or discovery.get("discovered_files")
    )


def _provided_evidence_missing_field(
    provided_evidence: dict[str, Any] | None,
) -> str:
    mode = (provided_evidence or {}).get("mode")
    if mode == "pasted_text":
        return "provided_evidence.pasted_text"
    return "provided_evidence.files"


def _extract_mysql_uri_target(text: str) -> dict[str, Any] | None:
    match = _MYSQL_URI.search(text)
    if not match:
        return None
    port = int(match.group("port") or "3306")
    return {
        "type": "mysql",
        "host": match.group("host"),
        "port": port,
        "username": match.group("username"),
        "password_ref": match.group("password_ref"),
    }


def _normalize_input_mode(value: object) -> str:
    mode = str(value or "unknown").strip()
    return mode if mode in _INPUT_MODES else "unknown"


def _collection_policy(value: object, input_mode: str) -> dict[str, bool]:
    if isinstance(value, dict):
        result = dict(_DEFAULT_COLLECTION_POLICY)
        for key in result:
            result[key] = bool(value.get(key, result[key]))
        return result
    if input_mode == "live_collection":
        return {
            "allow_live_collection": True,
            "allow_mysql_login": True,
            "allow_ssh": False,
            "allow_metrics_query": False,
        }
    return dict(_DEFAULT_COLLECTION_POLICY)


def _provided_evidence(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        discovery = value.get("discovery") if isinstance(value.get("discovery"), dict) else {}
        return {
            "mode": value.get("mode") or "unknown",
            "files": list(value.get("files") or []),
            "pasted_text": bool(value.get("pasted_text", False)),
            "description": value.get("description") or "",
            "discovery": _provided_evidence_discovery(discovery),
        }
    return {
        "mode": "unknown",
        "files": [],
        "pasted_text": False,
        "description": "",
        "discovery": _provided_evidence_discovery({}),
    }


def _provided_evidence_discovery(value: dict[str, Any]) -> dict[str, Any]:
    file_sizes = value.get("file_sizes_bytes") or {}
    if not isinstance(file_sizes, dict):
        file_sizes = {}
    return {
        "attempted_paths": list(value.get("attempted_paths") or []),
        "discovered_files": list(value.get("discovered_files") or []),
        "discovery_status": value.get("discovery_status") or "not_attempted",
        "errors": list(value.get("errors") or []),
        "file_sizes_bytes": dict(file_sizes),
    }


def _evidence_plan(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "required_evidence": list(value.get("required_evidence") or []),
            "provided_evidence": list(value.get("provided_evidence") or []),
            "missing_evidence": list(value.get("missing_evidence") or []),
        }
    return {"required_evidence": [], "provided_evidence": [], "missing_evidence": []}


def _missing_from_llm(llm_json: dict[str, Any] | None) -> tuple[str, ...]:
    if not llm_json:
        return ()
    fields = llm_json.get("missing_fields") or []
    if not isinstance(fields, list):
        return ()
    return tuple(str(field) for field in fields if str(field).strip())


def _optional_mapping(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _string_or_default(value: object, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _float_or_default(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_request_tool(user_input: str) -> dict[str, object]:
    """Normalize a DBKit Phase 01.1 intake request into structured fields."""
    return normalize_request(user_input).to_dict()
