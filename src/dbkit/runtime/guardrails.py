from __future__ import annotations

import fnmatch
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
    def __init__(
        self,
        *,
        allowed_workspace_root: str = "/workspace/",
        max_discovered_files: int = 100,
        max_evidence_file_size_bytes: int = 50_000_000,
        blocked_paths: tuple[str, ...] = (),
    ) -> None:
        self.allowed_workspace_root = _normalize_root(allowed_workspace_root)
        self.max_discovered_files = max_discovered_files
        self.max_evidence_file_size_bytes = max_evidence_file_size_bytes
        self.blocked_paths = blocked_paths

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

        issues.extend(self._validate_provided_evidence_paths(request))

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

    def validate_supplement_patch(
        self,
        patch: dict[str, object],
        *,
        base_request: NormalizedRequest,
    ) -> GuardrailsResult:
        issues: list[str] = []
        allowed_top_level = {
            "target",
            "ssh_target",
            "provided_evidence",
            "event",
            "evidence_plan",
            "missing_fields",
            "metadata",
        }

        for key in patch:
            if key == "target_agent":
                issues.append("supplement_patch cannot modify target_agent")
            elif key == "target_domain":
                issues.append("supplement_patch cannot modify target_domain")
            elif key == "task_type":
                issues.append("supplement_patch cannot modify task_type")
            elif key == "input_mode":
                issues.append("supplement_patch cannot modify input_mode")
            elif key == "collection_policy":
                issues.extend(
                    _collection_policy_patch_issues(
                        patch.get("collection_policy"),
                        base_request.collection_policy or {},
                    )
                )
            elif key not in allowed_top_level:
                issues.append(f"supplement_patch cannot modify field: {key}")

        if _request_contains_secret_leakage(patch):
            issues.append("secret leakage detected in supplement_patch")

        return GuardrailsResult(
            passed=len(issues) == 0,
            normalized_request=base_request,
            blocking_issues=tuple(issues),
        )

    def _validate_provided_evidence_paths(self, request: NormalizedRequest) -> list[str]:
        provided = request.provided_evidence or {}
        discovery = provided.get("discovery") or {}
        files = _string_list(provided.get("files"))
        discovered_files = _string_list(discovery.get("discovered_files"))
        attempted_paths = _string_list(discovery.get("attempted_paths"))
        all_paths = list(dict.fromkeys(files + discovered_files + attempted_paths))
        issues: list[str] = []

        evidence_files = list(dict.fromkeys(files + discovered_files))
        if len(evidence_files) > self.max_discovered_files:
            issues.append(
                "too many discovered files: "
                f"{len(evidence_files)} > {self.max_discovered_files}"
            )

        for path in all_paths:
            if not _is_under_root(path, self.allowed_workspace_root):
                issues.append(
                    f"provided evidence path outside allowed workspace root: {path}"
                )
            if any(fnmatch.fnmatch(path, pattern) for pattern in self.blocked_paths):
                issues.append(f"provided evidence path matches blocked path: {path}")

        sizes = discovery.get("file_sizes_bytes") or {}
        if isinstance(sizes, dict):
            for path, raw_size in sizes.items():
                try:
                    size = int(raw_size)
                except (TypeError, ValueError):
                    continue
                if size > self.max_evidence_file_size_bytes:
                    issues.append(
                        "provided evidence file exceeds max evidence file size: "
                        f"{path}={size} > {self.max_evidence_file_size_bytes}"
                    )

        return issues


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


def _collection_policy_patch_issues(
    patch_policy: object,
    base_policy: dict[str, bool],
) -> list[str]:
    if not isinstance(patch_policy, dict):
        return ["supplement_patch.collection_policy must be an object"]
    issues: list[str] = []
    for key, value in patch_policy.items():
        if bool(value) and not bool(base_policy.get(str(key), False)):
            issues.append(
                "supplement_patch cannot enable collection policy denied by user: "
                f"{key}"
            )
    return issues


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _normalize_root(path: str) -> str:
    root = path if path.startswith("/") else f"/{path}"
    return root if root.endswith("/") else f"{root}/"


def _is_under_root(path: str, root: str) -> bool:
    if path == root.rstrip("/"):
        return True
    return path.startswith(root)
