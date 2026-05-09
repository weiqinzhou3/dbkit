from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

from dbkit.agents.evidence_structuring import EvidenceStructuringSubagentRegistration
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
) -> tuple[Any, ...]:
    def build_evidence_bundle(raw_evidence_index: str) -> str:
        """Build an EvidenceBundle from a DBKit RawEvidence index artifact."""
        result = EvidenceStructuringPipeline(
            artifact_store=artifact_store,
            telemetry=telemetry,
            subagent_registration=subagent_registration,
        ).run(Path(raw_evidence_index))
        result_sink(result)
        payload = {
            "status": result.status,
            "request_id": result.request_id,
            "parent_agent": subagent_registration.parent_agent,
            "subagent": subagent_registration.name,
            "evidence_bundle_artifact": (
                str(result.bundle_artifact.path)
                if result.bundle_artifact is not None
                else None
            ),
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
            "blocking_issues": list(result.blocking_issues),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

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
