from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from time import perf_counter_ns
from typing import Any, Callable

from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.collection_preflight import (
    INSTALL_HINT,
    find_missing_collection_dependencies,
)
from dbkit.runtime.collection_guardrails import CollectionGuardrails
from dbkit.runtime.json_extraction import extract_json_from_invoke_result
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.time_context import TimeProvider
from dbkit.schemas.evidence import (
    CollectionPlan,
    CollectionStep,
    EvidencePipelineResult,
    EvidenceRequest,
    RawEvidence,
    collection_status,
    collection_summary,
    stable_id,
    validate_evidence_request,
)
from dbkit.schemas.runtime import NormalizedRequest
from dbkit.tools.collectors import CollectorRegistry

_MYSQL_BASELINE_TOOL_HINTS = (
    "collect_mysql_processlist",
    "collect_mysql_runtime_status",
    "collect_mysql_innodb_status",
    "collect_mysql_variables",
    "collect_mysql_service_metadata",
    "discover_mysql_log_paths",
)
_PHASE_02_1_DETAIL = "phase-02.1-real-mysql-evidence-collection"
_DEPRECATED_MYSQL_METRIC_TOOL_HINTS = frozenset(
    {
        "collect_mysql_metrics_snapshot",
        "collect_mysql_status_metrics",
        "collect_mysql_variable_metrics",
    }
)
_DEPRECATED_MYSQL_METRIC_EVIDENCE_TYPES = frozenset(
    {
        "metrics.mysql",
        "metrics.mysql_status",
        "metrics.mysql_variables",
    }
)


@dataclass
class EvidencePipeline:
    artifact_store: ArtifactStore
    telemetry: TelemetryRecorder
    collectors: CollectorRegistry
    time_provider: Any
    guardrails: CollectionGuardrails
    mysql_analyzer_runtime: Any | None
    collection_dependency_checker: Callable[[CollectionPlan], tuple[str, ...]] | None

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        telemetry: TelemetryRecorder,
        collectors: CollectorRegistry,
        time_provider: Any | None = None,
        guardrails: CollectionGuardrails | None = None,
        mysql_analyzer_runtime: Any | None = None,
        collection_dependency_checker: Callable[[CollectionPlan], tuple[str, ...]] | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.telemetry = telemetry
        self.collectors = collectors
        if getattr(self.collectors, "telemetry", None) is None:
            self.collectors.telemetry = telemetry
        self.time_provider = time_provider or TimeProvider()
        self.guardrails = guardrails or CollectionGuardrails()
        self.mysql_analyzer_runtime = mysql_analyzer_runtime
        self.collection_dependency_checker = collection_dependency_checker

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
        if evidence_request_json is None:
            self.telemetry.emit(
                event_type="evidence_request_parse_failed",
                stage="evidence_planning",
                message="MySQL analyzer did not return parseable EvidenceRequest JSON",
                attributes={
                    "request_id": request.request_id,
                    "reason": "evidence_request_parse_failed",
                },
            )
            failed_artifact = self.artifact_store.persist_evidence_request_failed(
                request,
                reason="evidence_request_parse_failed",
            )
            self.telemetry.emit(
                event_type="artifact_written",
                stage="artifacts",
                message="Artifact written: EvidenceRequestFailed",
                attributes={
                    "request_id": request.request_id,
                    "kind": "EvidenceRequestFailed",
                    "path": str(failed_artifact.path),
                },
            )
            telemetry_artifact = self.artifact_store.persist_collection_telemetry(
                request.request_id, self.telemetry.events
            )
            return EvidencePipelineResult(
                request_id=request.request_id,
                phase=request.phase if request.phase.startswith("phase-02.1") else "phase-02",
                status="evidence_request_parse_failed",
                evidence_request=None,
                collection_plan=None,
                raw_evidence=(),
                artifacts=(failed_artifact, telemetry_artifact),
                telemetry=tuple(self.telemetry.events),
                blocking_issues=("evidence_request_parse_failed",),
            )
        try:
            evidence_request = validate_evidence_request(evidence_request_json)
        except ValueError as exc:
            self.telemetry.emit(
                event_type="evidence_request_validation_failed",
                stage="evidence_planning",
                message="EvidenceRequest JSON failed validation",
                attributes={
                    "request_id": request.request_id,
                    "reason": "evidence_request_validation_failed",
                    "error": str(exc),
                },
            )
            failed_artifact = self.artifact_store.persist_evidence_request_failed(
                request,
                reason="evidence_request_validation_failed",
                details=[str(exc)],
            )
            self.telemetry.emit(
                event_type="artifact_written",
                stage="artifacts",
                message="Artifact written: EvidenceRequestFailed",
                attributes={
                    "request_id": request.request_id,
                    "kind": "EvidenceRequestFailed",
                    "path": str(failed_artifact.path),
                },
            )
            telemetry_artifact = self.artifact_store.persist_collection_telemetry(
                request.request_id, self.telemetry.events
            )
            return EvidencePipelineResult(
                request_id=request.request_id,
                phase=request.phase if request.phase.startswith("phase-02.1") else "phase-02",
                status="evidence_request_validation_failed",
                evidence_request=None,
                collection_plan=None,
                raw_evidence=(),
                artifacts=(failed_artifact, telemetry_artifact),
                telemetry=tuple(self.telemetry.events),
                blocking_issues=("evidence_request_validation_failed",),
            )
        evidence_request = _with_phase_detail(evidence_request, request)
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
        revision_used = False

        contract_issues = self._evidence_request_contract_issues(
            evidence_request, request
        )
        if contract_issues:
            self.telemetry.emit(
                event_type="evidence_request_contract_failed",
                stage="evidence_planning",
                message="Evidence request contract failed",
                attributes={
                    "request_id": request.request_id,
                    "blocking_issues": list(contract_issues),
                },
            )
            revised = self._revise_evidence_request(
                request=request,
                evidence_request=evidence_request,
                blocking_issues=contract_issues,
            )
            if revised is not None:
                revision_used = True
                evidence_request = _with_phase_detail(revised, request)
                evidence_artifact = self.artifact_store.persist_evidence_request(
                    evidence_request
                )
                contract_issues = self._evidence_request_contract_issues(
                    evidence_request, request
                )
            if contract_issues:
                plan = self.create_collection_plan(evidence_request, request)
                return self._blocked_collection_result(
                    request=request,
                    evidence_request=evidence_request,
                    evidence_artifact=evidence_artifact,
                    plan=plan,
                    blocking_issues=contract_issues,
                    event_type="evidence_request_contract_blocked",
                    message="Evidence request contract blocked collection",
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
        if (
            not guardrails_result.passed
            and not revision_used
            and self._should_revise_evidence_request(
                guardrails_result.blocking_issues
            )
        ):
            revised = self._revise_evidence_request(
                request=request,
                evidence_request=evidence_request,
                blocking_issues=guardrails_result.blocking_issues,
            )
            if revised is not None:
                revision_used = True
                evidence_request = _with_phase_detail(revised, request)
                evidence_artifact = self.artifact_store.persist_evidence_request(
                    evidence_request
                )
                contract_issues = self._evidence_request_contract_issues(
                    evidence_request, request
                )
                if contract_issues:
                    plan = self.create_collection_plan(evidence_request, request)
                    return self._blocked_collection_result(
                        request=request,
                        evidence_request=evidence_request,
                        evidence_artifact=evidence_artifact,
                        plan=plan,
                        blocking_issues=contract_issues,
                        event_type="evidence_request_contract_blocked",
                        message="Revised evidence request contract blocked collection",
                    )
                plan = self.create_collection_plan(evidence_request, request)
                self.telemetry.emit(
                    event_type="collection_plan_created",
                    stage="collection_planning",
                    message="Collection plan created from revised evidence request",
                    attributes={
                        "request_id": request.request_id,
                        "collection_plan_id": plan.collection_plan_id,
                        "step_count": len(plan.steps),
                        "revision": 1,
                    },
                )
                self.telemetry.emit(
                    event_type="collection_guardrails_started",
                    stage="collection_guardrails",
                    message="Collection guardrails started for revised evidence request",
                    attributes={"request_id": request.request_id, "revision": 1},
                )
                guardrails_result = self.guardrails.validate(plan, request)
        if not guardrails_result.passed:
            return self._blocked_collection_result(
                request=request,
                evidence_request=evidence_request,
                evidence_artifact=evidence_artifact,
                plan=plan,
                blocking_issues=guardrails_result.blocking_issues,
                event_type="collection_guardrails_blocked",
                message="Collection guardrails blocked",
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

        missing_dependencies = self._missing_collection_dependencies(plan, request)
        if missing_dependencies:
            self.telemetry.emit(
                event_type="missing_collection_dependency",
                stage="collection_preflight",
                message="Collection dependencies are missing",
                attributes={
                    "request_id": request.request_id,
                    "missing_dependencies": list(missing_dependencies),
                    "install_hint": INSTALL_HINT,
                },
            )
            blocked_artifact = self.artifact_store.persist_collection_blocked(
                request,
                plan,
                reason="missing_collection_dependencies",
                missing_dependencies=list(missing_dependencies),
                install_hint=INSTALL_HINT,
            )
            self.telemetry.emit(
                event_type="artifact_written",
                stage="artifacts",
                message="Artifact written: CollectionBlocked",
                attributes={
                    "request_id": request.request_id,
                    "kind": "CollectionBlocked",
                    "path": str(blocked_artifact.path),
                },
            )
            telemetry_artifact = self.artifact_store.persist_collection_telemetry(
                request.request_id, self.telemetry.events
            )
            return EvidencePipelineResult(
                request_id=request.request_id,
                phase=request.phase if request.phase.startswith("phase-02.1") else "phase-02",
                status="missing_collection_dependencies",
                evidence_request=evidence_request,
                collection_plan=plan,
                raw_evidence=(),
                artifacts=(
                    evidence_artifact,
                    plan_artifact,
                    blocked_artifact,
                    telemetry_artifact,
                ),
                telemetry=tuple(self.telemetry.events),
                blocking_issues=("missing_collection_dependencies",),
                metadata={
                    "missing_dependencies": list(missing_dependencies),
                    "install_hint": INSTALL_HINT,
                },
            )

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
            started_ns = perf_counter_ns()
            collected = self.collectors.collect(
                step=step,
                request=request,
                raw_root=raw_root,
                started_at=now,
                completed_at=now,
            )
            elapsed_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
            collected = tuple(
                _with_collection_duration(item, elapsed_ms)
                for item in collected
            )
            raw_items.extend(collected)
            for item in collected:
                status = str(item.collection["status"])
                if status == "blocked":
                    event_type = "collector_blocked"
                elif status == "failed":
                    event_type = "collector_failed"
                else:
                    event_type = "collector_completed"
                self.telemetry.emit(
                    event_type=event_type,
                    stage="collection",
                    message="Collector completed",
                    attributes={
                        "request_id": request.request_id,
                        "raw_evidence_id": item.raw_evidence_id,
                        "tool_name": step.tool_name,
                        "evidence_type": item.evidence_type,
                        "status": status,
                        "bytes": item.payload.get("bytes", 0),
                        "line_count": item.payload.get("line_count", 0),
                        "duration_ms": item.collection.get("duration_ms", 0),
                        **_log_collection_telemetry_attributes(item),
                    },
                )
                log_attrs = _log_collection_telemetry_attributes(item)
                if log_attrs:
                    self.telemetry.emit(
                        event_type="log_collection_strategy",
                        stage="collection",
                        message="Log collection strategy recorded",
                        attributes={
                            "request_id": request.request_id,
                            "raw_evidence_id": item.raw_evidence_id,
                            "tool_name": step.tool_name,
                            "evidence_type": item.evidence_type,
                            **log_attrs,
                        },
                    )

        summary = collection_summary(tuple(raw_items))
        self._close_collectors(request)
        status = collection_status(tuple(raw_items))
        index_artifact = self.artifact_store.persist_raw_evidence_index(
            request.request_id,
            tuple(raw_items),
            phase=request.phase if request.phase.startswith("phase-02.1") else "phase-02",
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
        self.telemetry.emit(
            event_type="collection_summary_created",
            stage="collection",
            message="Collection summary created",
            attributes={"request_id": request.request_id, **summary},
        )
        telemetry_artifact = self.artifact_store.persist_collection_telemetry(
            request.request_id, self.telemetry.events
        )
        return EvidencePipelineResult(
            request_id=request.request_id,
            phase=request.phase if request.phase.startswith("phase-02.1") else "phase-02",
            status=status,
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
        evidence_items = []
        for field_name in ("required_evidence", "optional_evidence"):
            values = evidence_request.evidence_request.get(field_name) or []
            if isinstance(values, list):
                evidence_items.extend(values)
        for item in evidence_items:
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
            phase=request.phase if request.phase.startswith("phase-02.1") else "phase-02",
            input_mode=request.input_mode,
            steps=tuple(steps),
        )

    def _invoke_mysql_analyzer(self, request: NormalizedRequest) -> dict[str, Any] | None:
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
        return extract_json_from_invoke_result(result)

    def _blocked_collection_result(
        self,
        *,
        request: NormalizedRequest,
        evidence_request: EvidenceRequest,
        evidence_artifact: Any,
        plan: CollectionPlan,
        blocking_issues: tuple[str, ...],
        event_type: str,
        message: str,
    ) -> EvidencePipelineResult:
        self.telemetry.emit(
            event_type=event_type,
            stage="collection_guardrails",
            message=message,
            attributes={
                "request_id": request.request_id,
                "blocking_issues": list(blocking_issues),
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
            phase=request.phase if request.phase.startswith("phase-02.1") else "phase-02",
            status="collection_blocked",
            evidence_request=evidence_request,
            collection_plan=blocked_plan,
            raw_evidence=(),
            artifacts=(evidence_artifact, plan_artifact, telemetry_artifact),
            telemetry=tuple(self.telemetry.events),
            blocking_issues=blocking_issues,
        )

    def _revise_evidence_request(
        self,
        *,
        request: NormalizedRequest,
        evidence_request: EvidenceRequest,
        blocking_issues: tuple[str, ...],
    ) -> EvidenceRequest | None:
        if self.mysql_analyzer_runtime is None:
            return None
        invoke = getattr(self.mysql_analyzer_runtime, "invoke", None)
        if not callable(invoke):
            return None

        self.telemetry.emit(
            event_type="evidence_request_revision_requested",
            stage="evidence_planning",
            message="Evidence request revision requested after guardrail block",
            attributes={
                "request_id": request.request_id,
                "blocking_issues": list(blocking_issues),
            },
        )
        context = {
            "mode": "evidence_planning_revision",
            "normalized_request": request.to_dict(),
            "collection_policy": request.collection_policy or {},
            "previous_evidence_request": evidence_request.to_dict(),
            "blocking_issues": list(blocking_issues),
            "instruction": (
                "Output revised DBKit EvidenceRequest JSON only. Fix the listed "
                "blocking issues. Add missing MySQL baseline evidence when the "
                "baseline policy requires it. Remove tools blocked by "
                "collection_policy. Remove deprecated duplicate MySQL metrics "
                "evidence. Do not add collection tools that are not permitted by "
                "the collection_policy."
            ),
        }
        result = invoke(
            {
                "mode": "evidence_planning_revision",
                "normalized_request": request,
                "collection_policy": request.collection_policy or {},
                "previous_evidence_request": evidence_request,
                "blocking_issues": list(blocking_issues),
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Output only revised DBKit EvidenceRequest JSON for "
                            "this evidence planning guardrail block.\n\n"
                            "DBKit evidence planning revision context JSON:\n"
                            + json_dumps(context)
                        ),
                    }
                ],
            }
        )
        revised_json = extract_json_from_invoke_result(result)
        if revised_json is None:
            self.telemetry.emit(
                event_type="evidence_request_revision_parse_failed",
                stage="evidence_planning",
                message="Revised EvidenceRequest JSON could not be parsed",
                attributes={"request_id": request.request_id},
            )
            return None

        try:
            revised = validate_evidence_request(revised_json)
        except ValueError as exc:
            self.telemetry.emit(
                event_type="evidence_request_revision_validation_failed",
                stage="evidence_planning",
                message="Revised EvidenceRequest JSON failed validation",
                attributes={"request_id": request.request_id, "error": str(exc)},
            )
            return None

        self.telemetry.emit(
            event_type="evidence_request_revision_completed",
            stage="evidence_planning",
            message="Evidence request revision completed",
            attributes={"request_id": request.request_id},
        )
        return revised

    @staticmethod
    def _should_revise_evidence_request(blocking_issues: tuple[str, ...]) -> bool:
        return any(
            "collection_policy does not permit" in issue
            for issue in blocking_issues
        )

    @staticmethod
    def _evidence_request_contract_issues(
        evidence_request: EvidenceRequest,
        request: NormalizedRequest,
    ) -> tuple[str, ...]:
        issues: list[str] = []
        items = _collection_plan_source_items(evidence_request)
        for field_name, index, item in items:
            if not str(item.get("tool_hint") or "").strip():
                issues.append(f"evidence item missing tool_hint: {field_name}[{index}]")

        policy = request.collection_policy or {}
        if (
            request.target_domain == "mysql"
            and request.input_mode in {"live_collection", "hybrid"}
            and bool(policy.get("allow_mysql_login"))
        ):
            tool_hints = {
                str(item.get("tool_hint") or "")
                for _, _, item in items
                if isinstance(item, dict)
            }
            missing = [
                tool_hint
                for tool_hint in _MYSQL_BASELINE_TOOL_HINTS
                if tool_hint not in tool_hints
            ]
            if missing:
                issues.append("missing MySQL baseline evidence: " + ", ".join(missing))
            deprecated = sorted(
                {
                    str(item.get("tool_hint") or "")
                    for _, _, item in items
                    if str(item.get("tool_hint") or "")
                    in _DEPRECATED_MYSQL_METRIC_TOOL_HINTS
                }
                | {
                    str(item.get("evidence_type") or "")
                    for _, _, item in items
                    if str(item.get("evidence_type") or "")
                    in _DEPRECATED_MYSQL_METRIC_EVIDENCE_TYPES
                }
            )
            if deprecated:
                issues.append(
                    "deprecated duplicate MySQL metrics evidence not allowed by default: "
                    + ", ".join(deprecated)
                )

        return tuple(dict.fromkeys(issues))

    def _missing_collection_dependencies(
        self,
        plan: CollectionPlan,
        request: NormalizedRequest,
    ) -> tuple[str, ...]:
        if self.collection_dependency_checker is not None:
            return self.collection_dependency_checker(plan)
        return find_missing_collection_dependencies(plan, request)

    def _close_collectors(self, request: NormalizedRequest) -> None:
        close = getattr(self.collectors, "close", None)
        if not callable(close):
            return
        close()


def _source_paths_for_step(
    tool_name: str,
    request: NormalizedRequest,
) -> list[str | None]:
    if tool_name not in {"read_provided_evidence_file", "read_provided_evidence_directory"}:
        return []
    provided = request.provided_evidence or {}
    files = provided.get("files") or []
    return [str(path) for path in files if str(path).strip()]


def _collection_plan_source_items(
    evidence_request: EvidenceRequest,
) -> list[tuple[str, int, dict[str, Any]]]:
    items: list[tuple[str, int, dict[str, Any]]] = []
    for field_name in ("required_evidence", "optional_evidence"):
        values = evidence_request.evidence_request.get(field_name) or []
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if isinstance(item, dict):
                items.append((field_name, index, item))
    return items


def _with_phase_detail(
    evidence_request: EvidenceRequest,
    request: NormalizedRequest,
) -> EvidenceRequest:
    if not request.phase.startswith("phase-02.1"):
        return evidence_request
    metadata = dict(evidence_request.metadata)
    metadata.setdefault("phase_detail", _PHASE_02_1_DETAIL)
    return replace(evidence_request, metadata=metadata)


def _with_collection_duration(item: RawEvidence, duration_ms: int) -> RawEvidence:
    collection = dict(item.collection)
    existing = int(collection.get("duration_ms") or 0)
    collection["duration_ms"] = max(existing, duration_ms)
    return replace(item, collection=collection)


def _target_ref(source: str, tool_name: str) -> str:
    if tool_name in {"read_provided_evidence_file", "read_provided_evidence_directory"}:
        return "provided_evidence"
    if source == "ssh" or tool_name.startswith("collect_os_") or tool_name == "read_remote_file":
        return "ssh_target"
    return "target"


def _secret_refs_for_step(
    tool_name: str,
    request: NormalizedRequest,
) -> tuple[str, ...]:
    if tool_name.startswith("read_provided_evidence_"):
        return ()
    if tool_name in {"collect_mysql_error_log", "collect_mysql_slow_log"}:
        refs = []
        mysql_ref = (request.target or {}).get("password_ref")
        ssh_ref = (request.ssh_target or {}).get("password_ref")
        if mysql_ref:
            refs.append(str(mysql_ref))
        if ssh_ref:
            refs.append(str(ssh_ref))
        return tuple(refs)
    if tool_name.startswith("collect_os_") or tool_name == "read_remote_file":
        target = request.ssh_target or {}
    else:
        target = request.target or {}
    password_ref = target.get("password_ref")
    return (str(password_ref),) if password_ref else ()


def _log_collection_telemetry_attributes(item: RawEvidence) -> dict[str, object]:
    if item.evidence_type not in {"mysql.error_log", "mysql.slow_log"}:
        return {}
    metadata = item.metadata or {}
    return {
        key: metadata[key]
        for key in (
            "collection_strategy",
            "time_window_aware",
            "time_window_coverage",
            "tail_lines",
            "max_bytes",
            "matched_lines",
            "discarded_lines",
        )
        if key in metadata
    }


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
