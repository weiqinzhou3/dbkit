from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.collection_guardrails import CollectionGuardrails
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.time_context import TimeProvider
from dbkit.schemas.evidence import (
    CollectionPlan,
    CollectionStep,
    EvidencePipelineResult,
    EvidenceRequest,
    RawEvidence,
    stable_id,
    validate_evidence_request,
)
from dbkit.schemas.runtime import NormalizedRequest
from dbkit.tools.collectors import CollectorRegistry


@dataclass
class EvidencePipeline:
    artifact_store: ArtifactStore
    telemetry: TelemetryRecorder
    collectors: CollectorRegistry
    time_provider: Any
    guardrails: CollectionGuardrails
    mysql_analyzer_runtime: Any | None

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        telemetry: TelemetryRecorder,
        collectors: CollectorRegistry,
        time_provider: Any | None = None,
        guardrails: CollectionGuardrails | None = None,
        mysql_analyzer_runtime: Any | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.telemetry = telemetry
        self.collectors = collectors
        self.time_provider = time_provider or TimeProvider()
        self.guardrails = guardrails or CollectionGuardrails()
        self.mysql_analyzer_runtime = mysql_analyzer_runtime

    def run(
        self,
        request: NormalizedRequest,
        *,
        evidence_request_json: dict[str, Any] | None = None,
    ) -> EvidencePipelineResult:
        self.telemetry.emit(
            event_type="evidence_planning_started",
            stage="evidence_planning",
            message="Evidence planning started",
            attributes={"request_id": request.request_id},
        )
        if evidence_request_json is None:
            evidence_request_json = self._invoke_mysql_analyzer(request)
        evidence_request = validate_evidence_request(evidence_request_json)
        self.telemetry.emit(
            event_type="evidence_planning_completed",
            stage="evidence_planning",
            message="Evidence planning completed",
            attributes={"request_id": request.request_id},
        )
        self.telemetry.emit(
            event_type="evidence_request_validated",
            stage="evidence_planning",
            message="Evidence request validated",
            attributes={"request_id": request.request_id},
        )
        evidence_artifact = self.artifact_store.persist_evidence_request(
            evidence_request
        )

        plan = self.create_collection_plan(evidence_request, request)
        self.telemetry.emit(
            event_type="collection_plan_created",
            stage="collection_planning",
            message="Collection plan created",
            attributes={
                "request_id": request.request_id,
                "collection_plan_id": plan.collection_plan_id,
                "step_count": len(plan.steps),
            },
        )
        self.telemetry.emit(
            event_type="collection_guardrails_started",
            stage="collection_guardrails",
            message="Collection guardrails started",
            attributes={"request_id": request.request_id},
        )
        guardrails_result = self.guardrails.validate(plan, request)
        if not guardrails_result.passed:
            self.telemetry.emit(
                event_type="collection_guardrails_blocked",
                stage="collection_guardrails",
                message="Collection guardrails blocked",
                attributes={
                    "request_id": request.request_id,
                    "blocking_issues": list(guardrails_result.blocking_issues),
                },
            )
            blocked_plan = CollectionPlan(
                request_id=plan.request_id,
                collection_plan_id=plan.collection_plan_id,
                phase=plan.phase,
                input_mode=plan.input_mode,
                steps=plan.steps,
                guardrails_status="blocked",
            )
            plan_artifact = self.artifact_store.persist_collection_plan(blocked_plan)
            telemetry_artifact = self.artifact_store.persist_collection_telemetry(
                request.request_id, self.telemetry.events
            )
            return EvidencePipelineResult(
                request_id=request.request_id,
                phase="phase-02",
                status="collection_blocked",
                evidence_request=evidence_request,
                collection_plan=blocked_plan,
                raw_evidence=(),
                artifacts=(evidence_artifact, plan_artifact, telemetry_artifact),
                telemetry=tuple(self.telemetry.events),
                blocking_issues=guardrails_result.blocking_issues,
            )

        plan = CollectionPlan(
            request_id=plan.request_id,
            collection_plan_id=plan.collection_plan_id,
            phase=plan.phase,
            input_mode=plan.input_mode,
            steps=plan.steps,
            guardrails_status="passed",
        )
        self.telemetry.emit(
            event_type="collection_guardrails_passed",
            stage="collection_guardrails",
            message="Collection guardrails passed",
            attributes={"request_id": request.request_id},
        )
        plan_artifact = self.artifact_store.persist_collection_plan(plan)

        raw_items: list[RawEvidence] = []
        raw_root = self.artifact_store.root / "raw"
        runtime_context = self.time_provider.runtime_context()
        now = runtime_context["current_datetime"]
        for step in plan.steps:
            self.telemetry.emit(
                event_type="collector_started",
                stage="collection",
                message="Collector started",
                attributes={
                    "request_id": request.request_id,
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                },
            )
            collected = self.collectors.collect(
                step=step,
                request=request,
                raw_root=raw_root,
                started_at=now,
                completed_at=now,
            )
            raw_items.extend(collected)
            for item in collected:
                event_type = (
                    "collector_completed"
                    if item.collection["status"] in {"collected", "not_implemented"}
                    else "collector_failed"
                )
                self.telemetry.emit(
                    event_type=event_type,
                    stage="collection",
                    message="Collector completed",
                    attributes={
                        "request_id": request.request_id,
                        "raw_evidence_id": item.raw_evidence_id,
                        "tool_name": step.tool_name,
                        "status": item.collection["status"],
                    },
                )

        index_artifact = self.artifact_store.persist_raw_evidence_index(
            request.request_id,
            tuple(raw_items),
        )
        for item in raw_items:
            self.telemetry.emit(
                event_type="raw_evidence_written",
                stage="artifacts",
                message="Raw evidence written",
                attributes={
                    "request_id": request.request_id,
                    "raw_evidence_id": item.raw_evidence_id,
                    "evidence_type": item.evidence_type,
                },
            )
        self.telemetry.emit(
            event_type="collection_plan_completed",
            stage="collection",
            message="Collection plan completed",
            attributes={
                "request_id": request.request_id,
                "raw_evidence_count": len(raw_items),
            },
        )
        telemetry_artifact = self.artifact_store.persist_collection_telemetry(
            request.request_id, self.telemetry.events
        )
        return EvidencePipelineResult(
            request_id=request.request_id,
            phase="phase-02",
            status="raw_evidence_collected",
            evidence_request=evidence_request,
            collection_plan=plan,
            raw_evidence=tuple(raw_items),
            artifacts=(
                evidence_artifact,
                plan_artifact,
                index_artifact,
                telemetry_artifact,
            ),
            telemetry=tuple(self.telemetry.events),
        )

    @staticmethod
    def create_collection_plan(
        evidence_request: EvidenceRequest,
        request: NormalizedRequest,
    ) -> CollectionPlan:
        steps: list[CollectionStep] = []
        required = evidence_request.evidence_request.get("required_evidence") or []
        for item in required:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_hint") or "")
            source = str(item.get("source") or "")
            paths = _source_paths_for_step(tool_name, request)
            if not paths:
                paths = [None]
            for source_path in paths:
                step_index = len(steps) + 1
                steps.append(
                    CollectionStep(
                        step_id=f"step_{step_index:03d}",
                        evidence_type=str(item.get("evidence_type") or "unknown"),
                        tool_name=tool_name,
                        target_ref=_target_ref(source, tool_name),
                        requires_secret_refs=_secret_refs_for_step(tool_name, request),
                        requires_approval=False,
                        timeout_seconds=30,
                        purpose=str(item.get("purpose") or ""),
                        source_path=source_path,
                    )
                )
        return CollectionPlan(
            request_id=request.request_id,
            collection_plan_id=stable_id("cp", request.request_id, len(steps)),
            phase="phase-02",
            input_mode=request.input_mode,
            steps=tuple(steps),
        )

    def _invoke_mysql_analyzer(self, request: NormalizedRequest) -> dict[str, Any]:
        if self.mysql_analyzer_runtime is None:
            raise RuntimeError("MySQL analyzer runtime is required for evidence planning")
        invoke = getattr(self.mysql_analyzer_runtime, "invoke", None)
        if not callable(invoke):
            raise TypeError("MySQL analyzer runtime must expose invoke()")
        context = {
            "mode": "evidence_planning",
            "normalized_request": request.to_dict(),
        }
        result = invoke(
            {
                "mode": "evidence_planning",
                "normalized_request": request,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Output only DBKit EvidenceRequest JSON for this "
                            "evidence planning context.\n\n"
                            "DBKit evidence planning context JSON:\n"
                            + json_dumps(context)
                        ),
                    }
                ],
            }
        )
        parsed = _extract_assistant_json(result)
        if parsed is None:
            raise ValueError("MySQL analyzer did not return parseable EvidenceRequest JSON")
        return parsed


def _source_paths_for_step(
    tool_name: str,
    request: NormalizedRequest,
) -> list[str | None]:
    if tool_name not in {"read_provided_evidence_file", "read_provided_evidence_directory"}:
        return []
    provided = request.provided_evidence or {}
    files = provided.get("files") or []
    return [str(path) for path in files if str(path).strip()]


def _target_ref(source: str, tool_name: str) -> str:
    if tool_name in {"read_provided_evidence_file", "read_provided_evidence_directory"}:
        return "provided_evidence"
    if source == "ssh":
        return "ssh_target"
    return "target"


def _secret_refs_for_step(
    tool_name: str,
    request: NormalizedRequest,
) -> tuple[str, ...]:
    if tool_name.startswith("read_provided_evidence_"):
        return ()
    target = request.target or {}
    password_ref = target.get("password_ref")
    return (str(password_ref),) if password_ref else ()


def _extract_assistant_json(result: object) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    messages = result.get("messages") or []
    for message in reversed(messages):
        content = _message_content(message)
        if not content:
            continue
        try:
            import json

            parsed = json.loads(content)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _message_content(message: object) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
