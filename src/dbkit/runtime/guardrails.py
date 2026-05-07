from __future__ import annotations

import re

from dbkit.schemas.runtime import GuardrailsResult, NormalizedRequest

_ALLOWED_TARGET_AGENTS = frozenset(
    {"mysql_analyzer", "redis_rdb_analyzer", "redis_inspector"}
)
_ALLOWED_TARGET_DOMAINS = frozenset({"mysql", "redis", "unknown"})
_ALLOWED_TASK_TYPES = frozenset(
    {"alert_analysis", "incident_analysis", "general_question", "unknown"}
)
_ALLOWED_INPUT_MODES = frozenset(
    {"live_collection", "provided_evidence", "hybrid", "unknown"}
)
_ROUTING_CONFIDENCE_THRESHOLD = 0.5

_LEAKAGE_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|api_key)\b\s*[:=：]\s*(?!<SECRET_REF:)\S{3,}"
    r"|(?:密码|口令)\s*[是为：:]\s*(?!<SECRET_REF:)\S{3,}"
)


class Guardrails:
    def validate(self, request: NormalizedRequest) -> GuardrailsResult:
        issues: list[str] = []

        if not request.request_id:
            issues.append("request_id is missing")
        if not request.original_input:
            issues.append("original_input is missing")

        if request.target_agent and request.target_agent not in _ALLOWED_TARGET_AGENTS:
            issues.append(
                f"target_agent '{request.target_agent}' is not an allowed routing target"
            )

        if request.target_domain not in _ALLOWED_TARGET_DOMAINS:
            issues.append(
                f"target_domain '{request.target_domain}' is not supported"
            )

        if request.task_type and request.task_type not in _ALLOWED_TASK_TYPES:
            issues.append(f"task_type '{request.task_type}' is not recognized")

        if request.input_mode not in _ALLOWED_INPUT_MODES:
            issues.append(f"input_mode '{request.input_mode}' is not recognized")

        if (
            request.routing_confidence is not None
            and request.routing_confidence < _ROUTING_CONFIDENCE_THRESHOLD
        ):
            issues.append(
                f"routing_confidence {request.routing_confidence:.2f} is below "
                f"threshold {_ROUTING_CONFIDENCE_THRESHOLD}"
            )

        for field in request.missing_fields:
            issues.append(f"missing required field: {field}")

        if _LEAKAGE_PATTERN.search(request.redacted_input):
            issues.append("secret leakage detected in redacted_input")

        if _request_contains_secret_leakage(request.to_dict()):
            issues.append("secret leakage detected in normalized_request")

        if request.event:
            tw = request.event.get("time_window")
            if tw:
                tw_issue = _validate_time_window(tw)
                if tw_issue:
                    issues.append(tw_issue)

        return GuardrailsResult(
            passed=len(issues) == 0,
            normalized_request=request,
            blocking_issues=tuple(issues),
        )

    def validate_normalized_request(self, request: NormalizedRequest) -> NormalizedRequest:
        """Backward-compatible wrapper — raises on any blocking issue."""
        result = self.validate(request)
        if not result.passed:
            raise ValueError(f"Guardrails blocked: {list(result.blocking_issues)}")
        return request


def _validate_time_window(tw: dict) -> str | None:
    start = tw.get("start")
    end = tw.get("end")
    if not start or not end:
        return None
    try:
        from datetime import datetime

        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        if s >= e:
            return f"time_window.start ({start}) must be before time_window.end ({end})"
    except (ValueError, TypeError):
        return f"time_window contains unparseable timestamps"
    return None


def _request_contains_secret_leakage(payload: dict) -> bool:
    text = str(payload)
    return bool(_LEAKAGE_PATTERN.search(text))
