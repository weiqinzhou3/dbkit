from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from time import perf_counter_ns

from langchain_core.tools import StructuredTool

from dbkit.agents.evidence_structuring import EvidenceStructuringSubagentRegistration
from dbkit.runtime.artifact_paths import to_host_path
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.evidence_structuring import EvidenceStructuringPipeline
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.schemas.evidence import EvidenceStructuringResult


def create_evidence_structuring_tools(
    *,
    artifact_store: ArtifactStore,
    telemetry: TelemetryRecorder,
    subagent_registration: EvidenceStructuringSubagentRegistration,
    result_sink: Callable[[EvidenceStructuringResult], None],
    repo_dir: Path,
    max_workers: int = 4,
    per_item_timeout_seconds: int = 30,
    total_timeout_seconds: int = 120,
) -> tuple[Any, ...]:
    def build_evidence_bundle(
        request_id: str = "",
        raw_evidence_index_virtual_path: str = "",
        raw_evidence_index_repo_path: str = "",
        artifact_root: str = "",
        max_workers: int = max_workers,
        per_item_timeout_seconds: int = per_item_timeout_seconds,
        total_timeout_seconds: int = total_timeout_seconds,
        raw_evidence_index: str = "",
    ) -> str:
        """Build an EvidenceBundle from a DBKit RawEvidence index artifact."""
        started_ns = perf_counter_ns()
        index_ref = (
            raw_evidence_index_virtual_path
            or raw_evidence_index
            or raw_evidence_index_repo_path
        )
        host_index_path = to_host_path(index_ref, repo_dir=repo_dir)
        result = EvidenceStructuringPipeline(
            artifact_store=artifact_store,
            telemetry=telemetry,
            subagent_registration=subagent_registration,
            max_workers=max_workers,
            per_item_timeout_seconds=per_item_timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
        ).run(host_index_path)
        result_sink(result)
        artifact_ref = (
            _repo_relative(result.bundle_artifact.path, repo_dir=repo_dir)
            if result.bundle_artifact is not None
            else None
        )
        raw_bytes_processed = (
            int(result.bundle.processing_summary.get("raw_bytes") or 0)
            if result.bundle is not None
            else 0
        )
        payload = {
            "status": result.status,
            "request_id": result.request_id,
            "parent_agent": subagent_registration.parent_agent,
            "subagent": subagent_registration.name,
            "artifact": artifact_ref,
            "evidence_bundle_artifact": artifact_ref,
            "evidence_items": (
                len(result.bundle.evidence_items)
                if result.bundle is not None
                else 0
            ),
            "quality": (
                result.bundle.quality.get("overall_status")
                if result.bundle is not None
                else None
            ),
            "warnings": (
                list(result.bundle.quality.get("warnings") or [])
                if result.bundle is not None
                else []
            ),
            "duration_ms": max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            "raw_bytes_processed_inside_tool": raw_bytes_processed,
            "parallel_workers": max_workers,
            "blocking_issues": list(result.blocking_issues),
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return serialized

    return (
        StructuredTool.from_function(
            func=build_evidence_bundle,
            name="build_evidence_bundle",
            description=(
                "Evidence processing tool for the evidence_structuring subagent. "
                "Given a raw_evidence_index path, it loads raw artifacts through "
                "content_ref, classifies, parses, filters by time window, "
                "deduplicates, aggregates, validates raw_refs, persists an "
                "EvidenceBundle, and returns bounded JSON metadata. It must not "
                "perform live collection, findings generation, verdict generation, "
                "or remediation."
            ),
        ),
    )


def _repo_relative(path: Path, *, repo_dir: Path) -> str:
    try:
        return path.relative_to(repo_dir).as_posix()
    except ValueError:
        return path.as_posix()
