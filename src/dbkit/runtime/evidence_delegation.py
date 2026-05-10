from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dbkit.runtime.artifact_paths import (
    to_deepagents_repo_virtual_path,
    to_repo_relative_path,
)
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.schemas.evidence import EvidenceStructuringResult


class EvidenceStructuringDelegator:
    def __init__(
        self,
        *,
        mysql_analyzer_runtime: Any,
        telemetry: TelemetryRecorder,
        repo_dir: Path,
        artifact_root: Path,
        max_agent_iterations: int = 4,
    ) -> None:
        self.mysql_analyzer_runtime = mysql_analyzer_runtime
        self.telemetry = telemetry
        self.repo_dir = repo_dir
        self.artifact_root = artifact_root
        self.max_agent_iterations = max_agent_iterations

    def run(
        self,
        *,
        request_id: str,
        raw_evidence_index: str | Path,
        result_sink: list[EvidenceStructuringResult],
    ) -> EvidenceStructuringResult | None:
        index_path = Path(raw_evidence_index)
        repo_relative_path = to_repo_relative_path(index_path, repo_dir=self.repo_dir)
        virtual_path = to_deepagents_repo_virtual_path(index_path, repo_dir=self.repo_dir)
        base_attrs = {
            "request_id": request_id,
            "parent_agent": "mysql_analyzer",
            "subagent": "evidence_structuring",
            "raw_evidence_index": virtual_path,
            "raw_evidence_index_repo_relative": repo_relative_path,
            "raw_evidence_index_virtual_path": virtual_path,
            "artifact_root": str(self.artifact_root),
            "filesystem_root": "/repo",
        }
        prompt = _delegation_prompt(
            request_id=request_id,
            raw_evidence_index_repo_relative=repo_relative_path,
            raw_evidence_index_virtual_path=virtual_path,
            artifact_root=".dbkit/artifacts",
        )
        self.telemetry.emit(
            event_type="mysql_analyzer_delegates_evidence_structuring_started",
            stage="evidence_structuring",
            message="MySQL analyzer started evidence_structuring subagent delegation",
            attributes={
                **base_attrs,
                "subagent_input_chars": len(prompt),
                "status": "started",
            },
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

        payload = {
            "mode": "evidence_structuring_delegation",
            "raw_evidence_index": virtual_path,
            "build_evidence_bundle_input": {
                "request_id": request_id,
                "raw_evidence_index_virtual_path": virtual_path,
                "raw_evidence_index_repo_path": repo_relative_path,
                "artifact_root": ".dbkit/artifacts",
                "max_workers": 4,
                "per_item_timeout_seconds": 30,
                "total_timeout_seconds": 120,
            },
            "max_agent_iterations": self.max_agent_iterations,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }
        config = {"recursion_limit": self.max_agent_iterations}
        try:
            invoke(payload, config)
        except TypeError:
            invoke(payload)
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


def _delegation_prompt(
    *,
    request_id: str,
    raw_evidence_index_repo_relative: str,
    raw_evidence_index_virtual_path: str,
    artifact_root: str,
) -> str:
    context = {
        "mode": "evidence_structuring_delegation",
        "parent_agent": "mysql_analyzer",
        "subagent": "evidence_structuring",
        "raw_evidence_index_repo_relative": raw_evidence_index_repo_relative,
        "raw_evidence_index_virtual_path": raw_evidence_index_virtual_path,
        "build_evidence_bundle_input": {
            "request_id": request_id,
            "raw_evidence_index_virtual_path": raw_evidence_index_virtual_path,
            "raw_evidence_index_repo_path": raw_evidence_index_repo_relative,
            "artifact_root": artifact_root,
            "max_workers": 4,
            "per_item_timeout_seconds": 30,
            "total_timeout_seconds": 120,
        },
        "filesystem_root": "/repo",
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
        "- Use the raw_evidence_index_virtual_path exactly as provided.\n"
        "- Do not call read_file for raw evidence artifacts.\n"
        "- Do not call ls or glob for raw evidence artifacts.\n"
        "- Call build_evidence_bundle exactly once with structured input.\n"
        "- build_evidence_bundle owns raw_evidence_index loading, content_ref loading, parsing, filtering, deduplication, aggregation, compression, and EvidenceBundle writing.\n"
        "- Do not inspect every raw artifact manually.\n"
        "- Do not call live collectors.\n"
        "- Do not request additional collection.\n"
        "- Do not generate findings, root cause, verdict, final summary, or recommendations.\n\n"
        "After build_evidence_bundle returns, output only the small JSON result. "
        "Do not summarize raw files or EvidenceBundle in prose.\n\n"
        "Delegation context JSON:\n"
        + json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
    )
