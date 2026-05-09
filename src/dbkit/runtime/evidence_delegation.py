from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dbkit.runtime.observability import TelemetryRecorder
from dbkit.schemas.evidence import EvidenceStructuringResult


class EvidenceStructuringDelegator:
    def __init__(
        self,
        *,
        mysql_analyzer_runtime: Any,
        telemetry: TelemetryRecorder,
    ) -> None:
        self.mysql_analyzer_runtime = mysql_analyzer_runtime
        self.telemetry = telemetry

    def run(
        self,
        *,
        request_id: str,
        raw_evidence_index: str | Path,
        result_sink: list[EvidenceStructuringResult],
    ) -> EvidenceStructuringResult | None:
        index_path = Path(raw_evidence_index)
        base_attrs = {
            "request_id": request_id,
            "parent_agent": "mysql_analyzer",
            "subagent": "evidence_structuring",
            "raw_evidence_index": str(index_path),
        }
        self.telemetry.emit(
            event_type="mysql_analyzer_delegates_evidence_structuring_started",
            stage="evidence_structuring",
            message="MySQL analyzer started evidence_structuring subagent delegation",
            attributes={**base_attrs, "status": "started"},
        )
        self.telemetry.emit(
            event_type="evidence_structuring_model_call_started",
            stage="evidence_structuring",
            message="Evidence structuring subagent model call requested through DeepAgents task delegation",
            attributes={**base_attrs, "status": "started"},
        )

        invoke = getattr(self.mysql_analyzer_runtime, "invoke", None)
        if not callable(invoke):
            self.telemetry.emit(
                event_type="evidence_subagent_invocation_failed",
                stage="evidence_structuring",
                message="MySQL analyzer runtime cannot invoke evidence structuring subagent",
                attributes={**base_attrs, "status": "failed", "reason": "invoke_missing"},
            )
            return None

        invoke(
            {
                "mode": "evidence_structuring_delegation",
                "raw_evidence_index": str(index_path),
                "messages": [
                    {
                        "role": "user",
                        "content": _delegation_prompt(index_path),
                    }
                ],
            }
        )
        if result_sink:
            result = result_sink[-1]
            bundle_artifact = (
                str(result.bundle_artifact.path)
                if result.bundle_artifact is not None
                else None
            )
            self.telemetry.emit(
                event_type="evidence_subagent_invocation_completed",
                stage="evidence_structuring",
                message="Evidence structuring subagent invocation completed",
                attributes={
                    **base_attrs,
                    "status": result.status,
                    "evidence_bundle_artifact": bundle_artifact,
                },
            )
            return result

        self.telemetry.emit(
            event_type="evidence_subagent_invocation_failed",
            stage="evidence_structuring",
            message="Evidence structuring subagent did not call build_evidence_bundle",
            attributes={
                **base_attrs,
                "status": "failed",
                "reason": "build_evidence_bundle_tool_not_called",
            },
        )
        return None


def _delegation_prompt(raw_evidence_index: Path) -> str:
    context = {
        "mode": "evidence_structuring_delegation",
        "parent_agent": "mysql_analyzer",
        "subagent": "evidence_structuring",
        "raw_evidence_index": str(raw_evidence_index),
        "expected_output": {
            "status": "evidence_bundle_created",
            "subagent": "evidence_structuring",
            "artifact": ".dbkit/artifacts/<request_id>.evidence-bundle.json",
        },
    }
    return (
        "RawEvidence collection has completed.\n\n"
        "You are mysql_analyzer in a delegation step. Do not analyze RawEvidence "
        "yourself. You must use the DeepAgents task tool exactly once with "
        "subagent_type=evidence_structuring.\n\n"
        "The task description for evidence_structuring must include:\n"
        "- You are the Evidence Structuring Subagent for mysql_analyzer.\n"
        "- Transform RawEvidence into EvidenceBundle.\n"
        "- Use content_ref to read full raw artifacts.\n"
        "- Call the build_evidence_bundle tool with the raw_evidence_index path.\n"
        "- Do not call live collectors.\n"
        "- Do not request additional collection.\n"
        "- Do not generate findings, root cause, verdict, final summary, or recommendations.\n\n"
        "After the task tool returns, output only the JSON returned by the "
        "subagent/tool. Do not add prose.\n\n"
        "Delegation context JSON:\n"
        + json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
    )
