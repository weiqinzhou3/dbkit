from __future__ import annotations

from dbkit.schemas.evidence import CollectionGuardrailsResult, CollectionPlan
from dbkit.schemas.runtime import NormalizedRequest


_PROVIDED_EVIDENCE_TOOLS = frozenset(
    {"read_provided_evidence_file", "read_provided_evidence_directory"}
)
_LIVE_COLLECTION_TOOLS = frozenset(
    {
        "collect_mysql_runtime_status",
        "collect_processlist",
        "collect_innodb_status",
        "collect_mysql_variables",
        "collect_mysql_error_log",
        "collect_mysql_slow_log",
        "collect_metrics_snapshot",
    }
)
_ALL_TOOLS = _PROVIDED_EVIDENCE_TOOLS | _LIVE_COLLECTION_TOOLS


class CollectionGuardrails:
    def validate(
        self,
        plan: CollectionPlan,
        request: NormalizedRequest,
    ) -> CollectionGuardrailsResult:
        issues: list[str] = []
        policy = request.collection_policy or {}

        for step in plan.steps:
            if step.tool_name not in _ALL_TOOLS:
                issues.append(f"collector tool does not exist: {step.tool_name}")
                continue

            if request.input_mode == "provided_evidence" and step.tool_name not in _PROVIDED_EVIDENCE_TOOLS:
                issues.append(
                    f"tool not allowed for provided_evidence input_mode: {step.tool_name}"
                )

            if step.tool_name in _PROVIDED_EVIDENCE_TOOLS:
                if not step.source_path:
                    issues.append(f"source_path is required for {step.tool_name}")
                elif not step.source_path.startswith("/workspace/"):
                    issues.append(
                        f"provided evidence path outside workspace: {step.source_path}"
                    )

            if step.tool_name in _LIVE_COLLECTION_TOOLS:
                if not policy.get("allow_live_collection"):
                    issues.append(
                        f"collection_policy does not permit live collection: {step.tool_name}"
                    )
                if step.tool_name != "collect_metrics_snapshot" and not policy.get("allow_mysql_login"):
                    issues.append(
                        f"collection_policy does not permit MySQL login: {step.tool_name}"
                    )
                target = request.target or {}
                if not target.get("host"):
                    issues.append("target.host is required for live collection")
                if not target.get("username"):
                    issues.append("target.username is required for live collection")
                for secret_ref in step.requires_secret_refs:
                    if not str(secret_ref).startswith("<SECRET_REF:"):
                        issues.append(
                            f"invalid secret ref for collection step {step.step_id}"
                        )

            if step.timeout_seconds <= 0:
                issues.append(f"timeout_seconds must be positive for {step.step_id}")

        return CollectionGuardrailsResult(
            passed=not issues,
            blocking_issues=tuple(dict.fromkeys(issues)),
        )
