from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from dbkit.runtime.artifact_paths import to_repo_relative_path
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.json_extraction import extract_json_from_invoke_result
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.schemas.analysis import (
    ALLOWED_SEVERITIES,
    VALIDATION_STATUS_ALIASES,
    Phase04AnalysisResult,
    evidence_id_set,
    finding_by_id,
    normalize_findings_categories,
    validate_evidence_bundle_payload,
    validate_findings_draft,
    validate_validation_result,
)


class Phase04AnalysisPipeline:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        telemetry: TelemetryRecorder,
        mysql_analyzer_runtime: Any,
        validation_runtime: Any,
        repo_dir: Path | None = None,
        max_prompt_chars: int = 30_000,
        findings_generation_timeout_seconds: int = 120,
        validation_timeout_seconds: int = 60,
        max_findings_generation_retries: int = 1,
        max_validation_retries: int = 1,
        max_agent_iterations: int = 6,
        max_findings: int = 5,
        model_name: str | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.telemetry = telemetry
        self.mysql_analyzer_runtime = mysql_analyzer_runtime
        self.validation_runtime = validation_runtime
        self.repo_dir = repo_dir or Path.cwd()
        self.max_prompt_chars = max_prompt_chars
        self.findings_generation_timeout_seconds = findings_generation_timeout_seconds
        self.validation_timeout_seconds = validation_timeout_seconds
        self.max_findings_generation_retries = max_findings_generation_retries
        self.max_validation_retries = max_validation_retries
        self.max_agent_iterations = max_agent_iterations
        self.max_findings = max_findings
        self.model_name = model_name
        self._last_timeout_stage: str | None = None

    def run(
        self,
        evidence_bundle_path: str | Path,
        *,
        expected_request_id: str | None = None,
    ) -> Phase04AnalysisResult:
        self._last_timeout_stage = None
        started_ns = perf_counter_ns()
        bundle_path = Path(evidence_bundle_path)
        request_id = "unknown"
        artifacts: list[Any] = []
        try:
            loaded_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return self._failed(
                request_id=request_id,
                artifacts=artifacts,
                issues=(f"evidence_bundle_load_failed: {exc}",),
                started_ns=started_ns,
            )
        if not isinstance(loaded_bundle, dict) or not loaded_bundle.get("request_id"):
            return self._failed(
                request_id=expected_request_id or "unknown",
                artifacts=artifacts,
                issues=("request_id_missing",),
                started_ns=started_ns,
            )
        try:
            evidence_bundle = validate_evidence_bundle_payload(loaded_bundle)
        except ValueError as exc:
            return self._failed(
                request_id=expected_request_id or str(loaded_bundle.get("request_id")),
                artifacts=artifacts,
                issues=(f"evidence_bundle_load_failed: {exc}",),
                started_ns=started_ns,
            )

        request_id = str(evidence_bundle["request_id"])
        input_bundle_ref = _artifact_ref(bundle_path, self.repo_dir)
        self._emit(
            "phase04_started",
            "Phase-04 findings, validation, verdict, and summary started",
            request_id=expected_request_id or request_id,
            input_evidence_bundle=input_bundle_ref,
            status="started",
        )
        lineage_issue = _lineage_issue(
            expected_request_id=expected_request_id,
            evidence_bundle=evidence_bundle,
            input_bundle_ref=input_bundle_ref,
        )
        if lineage_issue is not None:
            self._emit(
                "artifact_lineage_checked",
                "Artifact lineage check failed",
                request_id=expected_request_id or request_id,
                input_evidence_bundle=input_bundle_ref,
                lineage_check_status="failed",
                reason=lineage_issue,
                status="blocked",
            )
            return self._failed(
                request_id=expected_request_id or request_id,
                artifacts=artifacts,
                issues=("artifact_lineage_mismatch",),
                started_ns=started_ns,
            )
        self._emit(
            "artifact_lineage_checked",
            "Artifact lineage check passed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            lineage_check_status="passed",
            status="completed",
        )
        self._emit(
            "evidence_bundle_loaded",
            "EvidenceBundle loaded",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            evidence_item_count=len(evidence_bundle.get("evidence_items") or []),
            status="completed",
        )
        compact_context = self._create_compact_analysis_context(
            evidence_bundle=evidence_bundle,
            input_bundle_ref=input_bundle_ref,
        )

        findings_payload = self._invoke_findings_generation(
            evidence_bundle=evidence_bundle,
            compact_context=compact_context,
            input_bundle_ref=input_bundle_ref,
        )
        if findings_payload is None:
            if self._last_timeout_stage == "findings_generation":
                return self._analysis_timeout(
                    request_id=request_id,
                    artifacts=artifacts,
                    issues=("findings_generation_timeout",),
                    reason="findings_generation_timeout",
                    input_bundle_ref=input_bundle_ref,
                    started_ns=started_ns,
                )
            return self._failed(
                request_id=request_id,
                artifacts=artifacts,
                issues=("findings_generation_parse_failed",),
                started_ns=started_ns,
            )
        findings_draft, findings_error = self._prepare_findings_draft(
            findings_payload=findings_payload,
            evidence_bundle=evidence_bundle,
            compact_context=compact_context,
            input_bundle_ref=input_bundle_ref,
            artifacts=artifacts,
        )
        if findings_draft is None:
            return self._failed(
                request_id=request_id,
                artifacts=artifacts,
                issues=(f"findings_draft_invalid: {findings_error}",),
                started_ns=started_ns,
            )

        findings_artifact = self.artifact_store.persist_findings_draft(
            request_id, findings_draft
        )
        artifacts.append(findings_artifact)
        self._emit(
            "findings_draft_created",
            "FindingsDraft artifact created",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            finding_count=len(findings_draft.get("findings") or []),
            findings_artifact=str(findings_artifact.path),
            status="completed",
        )

        validation_payload = self._invoke_validation(
            evidence_bundle=evidence_bundle,
            compact_context=compact_context,
            findings_draft=findings_draft,
            input_bundle_ref=input_bundle_ref,
            findings_artifact=findings_artifact.path,
        )
        if self._last_timeout_stage == "validation":
            return self._analysis_timeout(
                request_id=request_id,
                artifacts=artifacts,
                issues=("validation_timeout",),
                reason="validation_timeout",
                input_bundle_ref=input_bundle_ref,
                started_ns=started_ns,
            )
        if validation_payload is None:
            return self._failed(
                request_id=request_id,
                artifacts=artifacts,
                issues=("validation_parse_failed",),
                started_ns=started_ns,
            )
        validation_result, validation_error = self._prepare_validation_result(
            validation_payload=validation_payload,
            evidence_bundle=evidence_bundle,
            compact_context=compact_context,
            findings_draft=findings_draft,
            input_bundle_ref=input_bundle_ref,
            findings_artifact=findings_artifact.path,
            artifacts=artifacts,
        )
        if validation_result is None:
            return self._failed(
                request_id=request_id,
                artifacts=artifacts,
                issues=(f"validation_result_invalid: {validation_error}",),
                started_ns=started_ns,
            )

        validation_result = self._enforce_evidence_ref_validation(
            validation_result=validation_result,
            findings_draft=findings_draft,
            evidence_bundle=evidence_bundle,
        )
        validation_artifact = self.artifact_store.persist_validation_result(
            request_id, validation_result
        )
        artifacts.append(validation_artifact)
        self._emit_validation_events(
            request_id=request_id,
            validation_result=validation_result,
            validation_artifact=str(validation_artifact.path),
        )

        verdict = self._build_verdict(
            request_id=request_id,
            input_bundle_ref=input_bundle_ref,
            findings_artifact=findings_artifact.path,
            validation_artifact=validation_artifact.path,
            findings_draft=findings_draft,
            validation_result=validation_result,
        )
        if verdict["status"] == "validation_failed":
            self._emit(
                "validation_failed",
                "Validation failed after evidence-reference checks",
                request_id=request_id,
                input_evidence_bundle=input_bundle_ref,
                blocked_count=len(validation_result.get("blocked_findings") or []),
                status="validation_failed",
            )
        verdict_artifact = self.artifact_store.persist_verdict(request_id, verdict)
        artifacts.append(verdict_artifact)
        self._emit(
            "verdict_created",
            "Verdict artifact created after validation",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            overall_confidence=verdict["overall_confidence"],
            overall_severity=verdict["overall_severity"],
            status=verdict["status"],
            verdict_artifact=str(verdict_artifact.path),
        )

        summary = self._render_summary(
            evidence_bundle=evidence_bundle,
            findings_draft=findings_draft,
            validation_result=validation_result,
            verdict=verdict,
        )
        summary_artifact = self.artifact_store.persist_summary(request_id, summary)
        artifacts.append(summary_artifact)
        self._emit(
            "summary_created",
            "Human-readable analysis summary created",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            summary_artifact=str(summary_artifact.path),
            status="completed",
        )
        self._emit(
            "phase04_completed",
            "Phase-04 completed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            finding_count=len(findings_draft.get("findings") or []),
            validated_count=len(validation_result.get("validated_findings") or []),
            blocked_count=len(validation_result.get("blocked_findings") or []),
            overall_confidence=verdict["overall_confidence"],
            overall_severity=verdict["overall_severity"],
            duration_ms=_duration_ms(started_ns),
            status=verdict["status"],
        )
        telemetry_artifact = self.artifact_store.persist_analysis_telemetry(
            request_id, self.telemetry.events
        )
        artifacts.append(telemetry_artifact)
        return Phase04AnalysisResult(
            request_id=request_id,
            phase="phase-04",
            status=str(verdict["status"]),
            findings_draft=findings_draft,
            validation_result=validation_result,
            verdict=verdict,
            summary=summary,
            artifacts=tuple(artifacts),
            telemetry=tuple(self.telemetry.events),
        )

    def _create_compact_analysis_context(
        self,
        *,
        evidence_bundle: dict[str, Any],
        input_bundle_ref: str,
    ) -> dict[str, Any]:
        started_ns = perf_counter_ns()
        request_id = str(evidence_bundle["request_id"])
        before = len(json.dumps(evidence_bundle, ensure_ascii=False, sort_keys=True))
        context = _compact_analysis_context(
            evidence_bundle,
            input_bundle_ref=input_bundle_ref,
        )
        context = _bound_compact_context(context, self.max_prompt_chars)
        after = len(json.dumps(context, ensure_ascii=False, sort_keys=True))
        self._emit(
            "compact_analysis_context_created",
            "Compact analysis context created for Phase-04 LLM calls",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            input_chars_before=before,
            input_chars_after=after,
            compression_ratio=round(after / before, 6) if before else 1,
            max_prompt_chars=self.max_prompt_chars,
            status="completed",
            duration_ms=_duration_ms(started_ns),
        )
        return context

    def _invoke_findings_generation(
        self,
        *,
        evidence_bundle: dict[str, Any],
        compact_context: dict[str, Any],
        input_bundle_ref: str,
        retry_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        request_id = str(evidence_bundle["request_id"])
        started_ns = perf_counter_ns()
        self._emit(
            "mysql_analyzer_findings_generation_started",
            "MySQL analyzer findings_generation started",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            mode="findings_generation",
            compact_context_chars=len(json.dumps(compact_context, ensure_ascii=False, sort_keys=True)),
            model_name=self.model_name,
            timeout_seconds=self.findings_generation_timeout_seconds,
            status="started",
        )
        payload = {
            "mode": "findings_generation",
            "input_evidence_bundle": input_bundle_ref,
            "compact_analysis_context": compact_context,
            "retry_context": retry_context or {},
            "max_agent_iterations": self.max_agent_iterations,
            "max_findings": self.max_findings,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Output only DBKit FindingsDraft JSON. Consume only the "
                        "compact_analysis_context; do not read RawEvidence and do not "
                        f"request collection. Return at most {self.max_findings} findings. "
                        "Use concise evidence-bound statements only.\n\n"
                        + (
                            "Previous FindingsDraft was invalid. Fix these issues:\n"
                            + json.dumps(retry_context, ensure_ascii=False, sort_keys=True)
                            + "\n\n"
                            if retry_context
                            else ""
                        )
                        + "compact_analysis_context JSON:\n"
                        + json.dumps(compact_context, ensure_ascii=False, sort_keys=True)
                    ),
                }
            ],
        }
        try:
            result = self._invoke_runtime_with_timeout(
                self.mysql_analyzer_runtime,
                payload,
                timeout_seconds=self.findings_generation_timeout_seconds,
            )
        except TimeoutError:
            self._last_timeout_stage = "findings_generation"
            self._emit(
                "mysql_analyzer_findings_generation_completed",
                "MySQL analyzer findings_generation timed out",
                request_id=request_id,
                input_evidence_bundle=input_bundle_ref,
                mode="findings_generation",
                timeout_seconds=self.findings_generation_timeout_seconds,
                model_name=self.model_name,
                status="timeout",
                duration_ms=_duration_ms(started_ns),
            )
            return None
        parsed = extract_json_from_invoke_result(result)
        self._emit(
            "mysql_analyzer_findings_generation_completed",
            "MySQL analyzer findings_generation completed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            mode="findings_generation",
            model_name=self.model_name,
            output_chars=len(json.dumps(parsed, ensure_ascii=False, sort_keys=True)) if parsed is not None else 0,
            status="completed" if parsed is not None else "failed",
            duration_ms=_duration_ms(started_ns),
        )
        return parsed

    def _prepare_findings_draft(
        self,
        *,
        findings_payload: dict[str, Any],
        evidence_bundle: dict[str, Any],
        compact_context: dict[str, Any],
        input_bundle_ref: str,
        artifacts: list[Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        request_id = str(evidence_bundle["request_id"])
        draft, errors = self._validate_findings_draft_attempt(
            findings_payload=findings_payload,
            evidence_bundle=evidence_bundle,
            input_bundle_ref=input_bundle_ref,
            retry_attempt=0,
        )
        if not errors:
            return draft, None

        invalid_artifact = self._persist_invalid_findings_draft(
            request_id=request_id,
            invalid_payload=findings_payload,
            validation_errors=errors,
            retry_attempt=0,
        )
        artifacts.append(invalid_artifact)
        if self.max_findings_generation_retries < 1:
            return None, errors[0]
        retry_context = {
            "validation_errors": list(errors),
            "allowed_schema": {
                "severity": "one of critical/high/medium/low/info",
                "confidence": "numeric value between 0.0 and 1.0, for example 0.78",
            },
            "instructions": [
                "Regenerate FindingsDraft JSON only.",
                "Do not re-analyze RawEvidence.",
                "Do not read raw logs.",
                "Only fix FindingsDraft JSON schema.",
                "Do not use high, medium, or low for confidence.",
            ],
        }
        self._emit(
            "findings_generation_retry_requested",
            "Findings generation retry requested by schema validation",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            validation_errors=list(errors),
            retry_attempt=1,
            status="retry_requested",
        )
        retry_payload = self._invoke_findings_generation(
            evidence_bundle=evidence_bundle,
            compact_context=compact_context,
            input_bundle_ref=input_bundle_ref,
            retry_context=retry_context,
        )
        self._emit(
            "findings_generation_retry_completed",
            "Findings generation retry completed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            retry_attempt=1,
            status="completed" if retry_payload is not None else "failed",
        )
        if retry_payload is None:
            return None, "retry_parse_failed"
        draft, errors = self._validate_findings_draft_attempt(
            findings_payload=retry_payload,
            evidence_bundle=evidence_bundle,
            input_bundle_ref=input_bundle_ref,
            retry_attempt=1,
        )
        if errors:
            invalid_artifact = self._persist_invalid_findings_draft(
                request_id=request_id,
                invalid_payload=retry_payload,
                validation_errors=errors,
                retry_attempt=1,
            )
            artifacts.append(invalid_artifact)
            return None, errors[0]
        return draft, None

    def _validate_findings_draft_attempt(
        self,
        *,
        findings_payload: dict[str, Any],
        evidence_bundle: dict[str, Any],
        input_bundle_ref: str,
        retry_attempt: int,
    ) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
        request_id = str(evidence_bundle["request_id"])
        self._emit(
            "findings_draft_schema_validation_started",
            "FindingsDraft schema validation started",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            retry_attempt=retry_attempt,
            status="started",
        )
        normalized_payload, category_events, invalid_categories = normalize_findings_categories(
            findings_payload
        )
        self._emit_category_events(
            request_id=str(evidence_bundle["request_id"]),
            category_events=category_events,
            invalid_categories=invalid_categories,
        )
        errors: list[str] = []
        if invalid_categories:
            errors.append(f"Finding.category is invalid: {','.join(invalid_categories)}")
        normalized_payload, confidence_events, confidence_errors = _normalize_confidence_values(
            normalized_payload
        )
        self._emit_confidence_events(
            request_id=request_id,
            confidence_events=confidence_events,
            confidence_errors=confidence_errors,
        )
        errors.extend(confidence_errors)
        if errors:
            self._emit(
                "findings_draft_schema_validation_failed",
                "FindingsDraft schema validation failed",
                request_id=request_id,
                input_evidence_bundle=input_bundle_ref,
                validation_errors=errors,
                retry_attempt=retry_attempt,
                confidence_normalization_status=_confidence_status(confidence_events, confidence_errors),
                status="failed",
            )
            return None, tuple(errors)
        try:
            draft = validate_findings_draft(normalized_payload, evidence_bundle)
        except ValueError as exc:
            errors = [str(exc)]
            self._emit(
                "findings_draft_schema_validation_failed",
                "FindingsDraft schema validation failed",
                request_id=request_id,
                input_evidence_bundle=input_bundle_ref,
                validation_errors=errors,
                retry_attempt=retry_attempt,
                confidence_normalization_status=_confidence_status(confidence_events, tuple(errors)),
                status="failed",
            )
            return None, tuple(errors)
        self._emit(
            "findings_draft_schema_validation_passed",
            "FindingsDraft schema validation passed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            retry_attempt=retry_attempt,
            confidence_normalization_status=_confidence_status(confidence_events, ()),
            status="completed",
        )
        self._emit(
            "findings_draft_schema_validation_completed",
            "FindingsDraft schema validation completed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            retry_attempt=retry_attempt,
            confidence_normalization_status=_confidence_status(confidence_events, ()),
            status="completed",
        )
        return draft, ()

    def _emit_category_events(
        self,
        *,
        request_id: str,
        category_events: tuple[dict[str, str], ...],
        invalid_categories: tuple[str, ...],
    ) -> None:
        for event in category_events:
            self._emit(
                "finding_category_normalized",
                "Finding category alias normalized",
                request_id=request_id,
                finding_id=event["finding_id"],
                original_category=event["original_category"],
                normalized_category=event["normalized_category"],
                category_validation_status="normalized",
                status="completed",
            )
        for category in invalid_categories:
            self._emit(
                "finding_category_invalid",
                "Finding category failed validation",
                request_id=request_id,
                original_category=category,
                normalized_category=category,
                category_validation_status="invalid",
                status="failed",
            )

    def _emit_confidence_events(
        self,
        *,
        request_id: str,
        confidence_events: tuple[dict[str, Any], ...],
        confidence_errors: tuple[str, ...],
    ) -> None:
        for event in confidence_events:
            self._emit(
                "finding_confidence_normalized",
                "Finding confidence normalized",
                request_id=request_id,
                finding_id=event["finding_id"],
                original_confidence=event["original_confidence"],
                normalized_confidence=event["normalized_confidence"],
                confidence_normalization_status="normalized",
                status="completed",
            )
        for error in confidence_errors:
            self._emit(
                "finding_confidence_invalid",
                "Finding confidence failed validation",
                request_id=request_id,
                validation_error=error,
                confidence_normalization_status="invalid",
                status="failed",
            )

    def _invoke_runtime_with_timeout(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> Any:
        if timeout_seconds <= 0:
            return runtime.invoke(payload)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(runtime.invoke, payload)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError("agent invocation timed out") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _persist_invalid_findings_draft(
        self,
        *,
        request_id: str,
        invalid_payload: dict[str, Any],
        validation_errors: tuple[str, ...] | list[str],
        retry_attempt: int,
    ) -> Any:
        artifact = self.artifact_store.persist_invalid_findings_draft(
            request_id,
            {
                "request_id": request_id,
                "phase": "phase-04",
                "status": "invalid",
                "retry_attempt": retry_attempt,
                "validation_errors": list(validation_errors),
                "parsed_invalid_object": _sanitize_for_artifact(invalid_payload),
            },
        )
        self._emit(
            "artifact_written",
            "Artifact written: InvalidFindingsDraft",
            request_id=request_id,
            kind=artifact.kind,
            path=str(artifact.path),
            retry_attempt=retry_attempt,
            status="completed",
        )
        return artifact

    def _invoke_validation(
        self,
        *,
        evidence_bundle: dict[str, Any],
        compact_context: dict[str, Any],
        findings_draft: dict[str, Any],
        input_bundle_ref: str,
        findings_artifact: Path,
        retry_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        request_id = str(evidence_bundle["request_id"])
        started_ns = perf_counter_ns()
        self._emit(
            "validation_started",
            "Validation agent started",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            input_findings_artifact=_artifact_ref(findings_artifact, self.repo_dir),
            finding_count=len(findings_draft.get("findings") or []),
            model_name=self.model_name,
            timeout_seconds=self.validation_timeout_seconds,
            status="started",
        )
        payload = {
            "mode": "validation",
            "input_evidence_bundle": input_bundle_ref,
            "findings_draft": findings_draft,
            "compact_analysis_context": compact_context,
            "retry_context": retry_context or {},
            "max_agent_iterations": self.max_agent_iterations,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Output only DBKit ValidationResult JSON. Validate "
                        "FindingsDraft against compact_analysis_context evidence_refs. "
                        "Do not read RawEvidence and do not generate new findings.\n\n"
                        + (
                            "Previous ValidationResult was invalid. Fix these issues:\n"
                            + json.dumps(retry_context, ensure_ascii=False, sort_keys=True)
                            + "\n\n"
                            if retry_context
                            else ""
                        )
                        + "FindingsDraft JSON:\n"
                        + json.dumps(findings_draft, ensure_ascii=False, sort_keys=True)
                        + "\n\ncompact_analysis_context JSON:\n"
                        + json.dumps(compact_context, ensure_ascii=False, sort_keys=True)
                    ),
                }
            ],
        }
        try:
            result = self._invoke_runtime_with_timeout(
                self.validation_runtime,
                payload,
                timeout_seconds=self.validation_timeout_seconds,
            )
        except TimeoutError:
            self._last_timeout_stage = "validation"
            self._emit(
                "validation_completed",
                "Validation agent timed out",
                request_id=request_id,
                input_evidence_bundle=input_bundle_ref,
                input_findings_artifact=_artifact_ref(findings_artifact, self.repo_dir),
                timeout_seconds=self.validation_timeout_seconds,
                status="timeout",
                duration_ms=_duration_ms(started_ns),
            )
            return None
        parsed = extract_json_from_invoke_result(result)
        self._emit(
            "validation_completed",
            "Validation agent completed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            input_findings_artifact=_artifact_ref(findings_artifact, self.repo_dir),
            status="completed" if parsed is not None else "failed",
            output_chars=len(json.dumps(parsed, ensure_ascii=False, sort_keys=True)) if parsed is not None else 0,
            duration_ms=_duration_ms(started_ns),
        )
        return parsed

    def _prepare_validation_result(
        self,
        *,
        validation_payload: dict[str, Any],
        evidence_bundle: dict[str, Any],
        compact_context: dict[str, Any],
        findings_draft: dict[str, Any],
        input_bundle_ref: str,
        findings_artifact: Path,
        artifacts: list[Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        request_id = str(evidence_bundle["request_id"])
        result, errors = self._validate_validation_result_attempt(
            validation_payload=validation_payload,
            findings_draft=findings_draft,
            input_bundle_ref=input_bundle_ref,
            retry_attempt=0,
        )
        if not errors:
            return result, None

        invalid_artifact = self._persist_invalid_validation_result(
            request_id=request_id,
            invalid_payload=validation_payload,
            validation_errors=errors,
            retry_attempt=0,
        )
        artifacts.append(invalid_artifact)
        if self.max_validation_retries < 1:
            return None, errors[0]

        retry_context = {
            "validation_errors": list(errors),
            "allowed_schema": {
                "validation_status": "one of passed/downgraded/blocked/requires_human_review",
            },
            "instructions": [
                "Regenerate ValidationResult JSON only.",
                "Do not re-analyze RawEvidence.",
                "Do not generate new findings.",
                "Only fix ValidationResult JSON schema.",
                "Do not use valid, invalid, pass, fail, approved, warning, needs_review, or review_required for validation_status.",
            ],
        }
        self._emit(
            "validation_retry_requested",
            "Validation retry requested by schema validation",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            validation_errors=list(errors),
            retry_attempt=1,
            status="retry_requested",
        )
        retry_payload = self._invoke_validation(
            evidence_bundle=evidence_bundle,
            compact_context=compact_context,
            findings_draft=findings_draft,
            input_bundle_ref=input_bundle_ref,
            findings_artifact=findings_artifact,
            retry_context=retry_context,
        )
        self._emit(
            "validation_retry_completed",
            "Validation retry completed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            retry_attempt=1,
            status="completed" if retry_payload is not None else "failed",
        )
        if retry_payload is None:
            return None, "retry_parse_failed"
        result, errors = self._validate_validation_result_attempt(
            validation_payload=retry_payload,
            findings_draft=findings_draft,
            input_bundle_ref=input_bundle_ref,
            retry_attempt=1,
        )
        if errors:
            invalid_artifact = self._persist_invalid_validation_result(
                request_id=request_id,
                invalid_payload=retry_payload,
                validation_errors=errors,
                retry_attempt=1,
            )
            artifacts.append(invalid_artifact)
            return None, errors[0]
        return result, None

    def _validate_validation_result_attempt(
        self,
        *,
        validation_payload: dict[str, Any],
        findings_draft: dict[str, Any],
        input_bundle_ref: str,
        retry_attempt: int,
    ) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
        request_id = str(findings_draft["request_id"])
        self._emit(
            "validation_result_schema_validation_started",
            "ValidationResult schema validation started",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            retry_attempt=retry_attempt,
            status="started",
        )
        normalized_payload, status_events, status_errors = _normalize_validation_statuses(
            validation_payload
        )
        self._emit_validation_status_events(
            request_id=request_id,
            status_events=status_events,
            status_errors=status_errors,
        )
        errors = list(status_errors)
        if errors:
            self._emit(
                "validation_result_schema_validation_failed",
                "ValidationResult schema validation failed",
                request_id=request_id,
                input_evidence_bundle=input_bundle_ref,
                validation_errors=errors,
                retry_attempt=retry_attempt,
                validation_status_normalization_status=_validation_status_status(
                    status_events,
                    status_errors,
                ),
                status="failed",
            )
            return None, tuple(errors)
        try:
            result = validate_validation_result(normalized_payload, findings_draft)
        except ValueError as exc:
            errors = [str(exc)]
            self._emit(
                "validation_result_schema_validation_failed",
                "ValidationResult schema validation failed",
                request_id=request_id,
                input_evidence_bundle=input_bundle_ref,
                validation_errors=errors,
                retry_attempt=retry_attempt,
                validation_status_normalization_status=_validation_status_status(
                    status_events,
                    tuple(errors),
                ),
                status="failed",
            )
            return None, tuple(errors)
        self._emit(
            "validation_result_schema_validation_passed",
            "ValidationResult schema validation passed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            retry_attempt=retry_attempt,
            validation_status_normalization_status=_validation_status_status(
                status_events,
                (),
            ),
            status="completed",
        )
        self._emit(
            "validation_result_schema_validation_completed",
            "ValidationResult schema validation completed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            retry_attempt=retry_attempt,
            validation_status_normalization_status=_validation_status_status(
                status_events,
                (),
            ),
            status="completed",
        )
        return result, ()

    def _emit_validation_status_events(
        self,
        *,
        request_id: str,
        status_events: tuple[dict[str, str], ...],
        status_errors: tuple[str, ...],
    ) -> None:
        for event in status_events:
            self._emit(
                "validation_status_normalized",
                "ValidationResult validation_status alias normalized",
                request_id=request_id,
                finding_id=event.get("finding_id"),
                original_validation_status=event["original_validation_status"],
                normalized_validation_status=event["normalized_validation_status"],
                validation_status_normalization_status="normalized",
                status="completed",
            )
        for error in status_errors:
            self._emit(
                "validation_status_invalid",
                "ValidationResult validation_status failed validation",
                request_id=request_id,
                validation_error=error,
                validation_status_normalization_status="invalid",
                status="failed",
            )

    def _persist_invalid_validation_result(
        self,
        *,
        request_id: str,
        invalid_payload: dict[str, Any],
        validation_errors: tuple[str, ...] | list[str],
        retry_attempt: int,
    ) -> Any:
        artifact = self.artifact_store.persist_invalid_validation_result(
            request_id,
            {
                "request_id": request_id,
                "phase": "phase-04",
                "status": "invalid",
                "retry_attempt": retry_attempt,
                "validation_errors": list(validation_errors),
                "parsed_invalid_object": _sanitize_for_artifact(invalid_payload),
            },
        )
        self._emit(
            "artifact_written",
            "Artifact written: InvalidValidationResult",
            request_id=request_id,
            kind=artifact.kind,
            path=str(artifact.path),
            retry_attempt=retry_attempt,
            status="completed",
        )
        return artifact

    def _enforce_evidence_ref_validation(
        self,
        *,
        validation_result: dict[str, Any],
        findings_draft: dict[str, Any],
        evidence_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        valid_evidence_ids = evidence_id_set(evidence_bundle)
        findings = finding_by_id(findings_draft)
        blocked = list(validation_result.get("blocked_findings") or [])
        validated = []
        blocked_ids = {str(item.get("finding_id")) for item in blocked if isinstance(item, dict)}
        for item in validation_result.get("validated_findings") or []:
            finding_id = str(item.get("finding_id") or "")
            finding = findings.get(finding_id)
            missing_ref = False
            if finding is None:
                missing_ref = True
            else:
                refs = finding.get("evidence_refs") or []
                missing_ref = any(str(ref.get("evidence_id") or "") not in valid_evidence_ids for ref in refs)
            if missing_ref:
                if finding_id not in blocked_ids:
                    blocked.append(
                        {
                            "finding_id": finding_id,
                            "reason": "evidence_ref_not_found",
                            "validation_status": "blocked",
                        }
                    )
                    self._emit(
                        "finding_blocked",
                        "Finding blocked by evidence reference validation",
                        request_id=str(findings_draft["request_id"]),
                        finding_id=finding_id,
                        reason="evidence_ref_not_found",
                        status="blocked",
                    )
                continue
            validated.append(item)

        result = dict(validation_result)
        result["validated_findings"] = validated
        result["blocked_findings"] = blocked
        summary = dict(result.get("validation_summary") or {})
        summary["passed"] = len(validated)
        summary["blocked"] = len(blocked)
        summary["downgraded"] = len(result.get("downgraded_findings") or [])
        result["validation_summary"] = summary
        return result

    def _emit_validation_events(
        self,
        *,
        request_id: str,
        validation_result: dict[str, Any],
        validation_artifact: str,
    ) -> None:
        for item in validation_result.get("downgraded_findings") or []:
            self._emit(
                "finding_downgraded",
                "Finding downgraded by validation",
                request_id=request_id,
                finding_id=item.get("finding_id"),
                status="downgraded",
            )
        self._emit(
            "validation_completed",
            "Validation agent completed",
            request_id=request_id,
            validation_artifact=validation_artifact,
            validated_count=len(validation_result.get("validated_findings") or []),
            blocked_count=len(validation_result.get("blocked_findings") or []),
            downgraded_count=len(validation_result.get("downgraded_findings") or []),
            status="completed",
        )

    def _build_verdict(
        self,
        *,
        request_id: str,
        input_bundle_ref: str,
        findings_artifact: Path,
        validation_artifact: Path,
        findings_draft: dict[str, Any],
        validation_result: dict[str, Any],
    ) -> dict[str, Any]:
        findings = finding_by_id(findings_draft)
        validated_items = validation_result.get("validated_findings") or []
        passed_ids = [
            str(item["finding_id"])
            for item in validated_items
            if item.get("validation_status") in {"passed", "downgraded"}
        ]
        passed_findings = [findings[finding_id] for finding_id in passed_ids if finding_id in findings]
        blocked = validation_result.get("blocked_findings") or []
        downgraded = validation_result.get("downgraded_findings") or []
        if validation_result.get("requires_human_review"):
            status = "human_review_required"
        elif not passed_findings and blocked:
            status = "validation_failed"
        elif blocked or downgraded or findings_draft.get("insufficient_evidence"):
            status = "analysis_completed_with_warnings"
        else:
            status = "analysis_completed"

        confidence_values = [
            float(item.get("confidence_after_validation"))
            for item in validated_items
            if item.get("finding_id") in passed_ids and item.get("confidence_after_validation") is not None
        ]
        overall_confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0
        overall_severity = _highest_severity([str(item.get("severity")) for item in passed_findings])
        return {
            "request_id": request_id,
            "phase": "phase-04",
            "status": status,
            "overall_severity": overall_severity,
            "overall_confidence": overall_confidence,
            "primary_findings": passed_ids[:1],
            "secondary_findings": passed_ids[1:],
            "insufficient_evidence": findings_draft.get("insufficient_evidence") or [],
            "requires_human_review": bool(validation_result.get("requires_human_review")),
            "human_review_reasons": [
                item.get("reason")
                for item in blocked
                if isinstance(item, dict) and item.get("reason")
            ],
            "next_actions": _next_actions(passed_findings),
            "artifact_refs": {
                "evidence_bundle": input_bundle_ref,
                "findings": _artifact_ref(findings_artifact, self.repo_dir),
                "validation": _artifact_ref(validation_artifact, self.repo_dir),
            },
        }

    def _render_summary(
        self,
        *,
        evidence_bundle: dict[str, Any],
        findings_draft: dict[str, Any],
        validation_result: dict[str, Any],
        verdict: dict[str, Any],
    ) -> str:
        findings = finding_by_id(findings_draft)
        primary = [findings[fid] for fid in verdict["primary_findings"] if fid in findings]
        secondary = [findings[fid] for fid in verdict["secondary_findings"] if fid in findings]
        lines = [
            "# DBKit MySQL Analysis Summary",
            "",
            "## 1. Analysis Scope",
            f"- Request: `{evidence_bundle['request_id']}`",
            f"- Status: `{verdict['status']}`",
            f"- Overall severity: `{verdict['overall_severity']}`",
            f"- Overall confidence: `{verdict['overall_confidence']}`",
            "",
            "## 2. Evidence Used",
        ]
        for item in evidence_bundle.get("evidence_items") or []:
            lines.append(f"- `{item.get('evidence_id')}` `{item.get('evidence_type')}`: {item.get('summary', '')}")
        lines.extend(["", "## 3. Primary Findings"])
        if primary:
            for finding in primary:
                lines.append(f"- {finding['title']} ({finding['severity']}, confidence {finding['confidence']})")
                lines.append(f"  Evidence: {', '.join(ref['evidence_id'] for ref in finding.get('evidence_refs') or [])}")
        else:
            lines.append("- No validated primary findings.")
        if secondary:
            lines.extend(["", "## 4. Supporting Evidence"])
            for finding in secondary:
                lines.append(f"- {finding['title']}")
        else:
            lines.extend(["", "## 4. Supporting Evidence", "- See evidence references attached to validated findings."])
        lines.extend(["", "## 5. Evidence Gaps / Limitations"])
        gaps = findings_draft.get("insufficient_evidence") or []
        if gaps:
            for gap in gaps:
                lines.append(f"- `{gap.get('evidence_type', 'unknown')}`: {gap.get('reason', 'insufficient')}")
        else:
            lines.append("- No explicit evidence gaps reported by the analyzer.")
        blocked = validation_result.get("blocked_findings") or []
        if blocked:
            lines.append(f"- Validation blocked {len(blocked)} finding(s).")
        lines.extend(["", "## 6. Suggested Next Checks"])
        for action in verdict.get("next_actions") or []:
            lines.append(f"- {action['action']} (risk: {action['risk']})")
        lines.extend(["", "## 7. Artifact References"])
        for key, value in verdict.get("artifact_refs", {}).items():
            lines.append(f"- {key}: `{value}`")
        return "\n".join(lines) + "\n"

    def _analysis_timeout(
        self,
        *,
        request_id: str,
        artifacts: list[Any],
        issues: tuple[str, ...],
        reason: str,
        input_bundle_ref: str,
        started_ns: int,
    ) -> Phase04AnalysisResult:
        timeout_artifact = self.artifact_store.persist_analysis_timeout(
            request_id,
            {
                "request_id": request_id,
                "phase": "phase-04",
                "status": "analysis_timeout",
                "reason": reason,
                "input_evidence_bundle": input_bundle_ref,
                "blocking_issues": list(issues),
            },
        )
        artifacts.append(timeout_artifact)
        self._emit(
            "phase04_completed",
            "Phase-04 stopped after analysis timeout",
            request_id=request_id,
            blocking_issues=list(issues),
            reason=reason,
            input_evidence_bundle=input_bundle_ref,
            duration_ms=_duration_ms(started_ns),
            status="analysis_timeout",
        )
        telemetry_artifact = self.artifact_store.persist_analysis_telemetry(
            request_id, self.telemetry.events
        )
        artifacts.append(telemetry_artifact)
        return Phase04AnalysisResult(
            request_id=request_id,
            phase="phase-04",
            status="analysis_timeout",
            findings_draft=None,
            validation_result=None,
            verdict=None,
            summary=None,
            artifacts=tuple(artifacts),
            telemetry=tuple(self.telemetry.events),
            blocking_issues=issues,
            metadata={"reason": reason, "input_evidence_bundle": input_bundle_ref},
        )

    def _failed(
        self,
        *,
        request_id: str,
        artifacts: list[Any],
        issues: tuple[str, ...],
        started_ns: int,
    ) -> Phase04AnalysisResult:
        self._emit(
            "phase04_failed",
            "Phase-04 failed",
            request_id=request_id,
            blocking_issues=list(issues),
            duration_ms=_duration_ms(started_ns),
            status="blocked",
        )
        telemetry_artifact = self.artifact_store.persist_analysis_telemetry(
            request_id, self.telemetry.events
        )
        artifacts.append(telemetry_artifact)
        return Phase04AnalysisResult(
            request_id=request_id,
            phase="phase-04",
            status="blocked",
            findings_draft=None,
            validation_result=None,
            verdict=None,
            summary=None,
            artifacts=tuple(artifacts),
            telemetry=tuple(self.telemetry.events),
            blocking_issues=issues,
        )

    def _emit(self, event_type: str, message: str, **attributes: Any) -> None:
        attributes.setdefault("target_agent", "mysql_analyzer")
        attributes.setdefault("mode", "findings_generation")
        attributes.setdefault("duration_ms", 0)
        self.telemetry.emit(
            event_type=event_type,
            stage="phase04",
            message=message,
            attributes=attributes,
        )


def _artifact_ref(path: str | Path, repo_dir: Path) -> str:
    return to_repo_relative_path(Path(path), repo_dir=repo_dir)


def _lineage_issue(
    *,
    expected_request_id: str | None,
    evidence_bundle: dict[str, Any],
    input_bundle_ref: str,
) -> str | None:
    request_id = str(evidence_bundle.get("request_id") or "")
    if not request_id:
        return "request_id_missing"
    if expected_request_id is not None and request_id != expected_request_id:
        return "artifact_lineage_mismatch"
    if input_bundle_ref and not input_bundle_ref.endswith(f"{request_id}.evidence-bundle.json"):
        return "artifact_lineage_mismatch"
    raw_index = str(evidence_bundle.get("input_raw_evidence_index") or "")
    if raw_index and not raw_index.endswith(f"{request_id}.raw-evidence-index.json"):
        return "artifact_lineage_mismatch"
    return None


def _compact_analysis_context(
    evidence_bundle: dict[str, Any],
    *,
    input_bundle_ref: str,
) -> dict[str, Any]:
    items = []
    for item in evidence_bundle.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        items.append(_compact_evidence_item(item))
    return {
        "request_id": evidence_bundle.get("request_id"),
        "phase": "phase-04",
        "input_evidence_bundle": input_bundle_ref,
        "context_truncated": False,
        "omitted_sections": [],
        "truncation_policy": "none",
        "incident": {
            "event": evidence_bundle.get("event") or {},
            "time_window": evidence_bundle.get("time_window") or {},
        },
        "coverage": {
            "source_raw_evidence_count": evidence_bundle.get("source_raw_evidence_count"),
            "processed_raw_evidence_count": evidence_bundle.get("processed_raw_evidence_count"),
            "unavailable_evidence": _limit_list(
                (evidence_bundle.get("coverage") or {}).get("unavailable_evidence"),
                20,
            ),
        },
        "quality": {
            "overall_status": (evidence_bundle.get("quality") or {}).get("overall_status"),
            "warnings": _limit_list((evidence_bundle.get("quality") or {}).get("warnings"), 20),
        },
        "evidence_items": items,
        "artifact_refs": {
            "evidence_bundle": input_bundle_ref,
            "raw_evidence_index": evidence_bundle.get("input_raw_evidence_index"),
        },
        "processing_summary": {
            key: (evidence_bundle.get("processing_summary") or {}).get(key)
            for key in (
                "estimated_tokens_after",
                "estimated_tokens_before",
                "compression_ratio",
            )
            if key in (evidence_bundle.get("processing_summary") or {})
        },
    }


def _compact_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    evidence_type = str(item.get("evidence_type") or "")
    structured = item.get("structured_payload") or {}
    compact: dict[str, Any] = {
        "evidence_id": item.get("evidence_id"),
        "evidence_type": evidence_type,
        "summary": item.get("summary"),
        "quality_flags": _limit_list(item.get("quality_flags"), 10),
        "time_range": item.get("time_range") or {},
        "raw_refs": [_compact_raw_ref(ref) for ref in _limit_list(item.get("raw_refs"), 10)],
    }
    if evidence_type == "mysql.error_log":
        compact["error_log"] = {
            "retained_lines": structured.get("retained_lines"),
            "discarded_lines": structured.get("discarded_lines"),
            "time_window_filter_status": structured.get("time_window_filter_status"),
            "timezone_handling": structured.get("timezone_handling"),
            "collection_time_window_coverage": structured.get("collection_time_window_coverage"),
            "severity_counts": structured.get("severity_counts") or {},
            "top_patterns": _compact_top_patterns(structured.get("top_patterns")),
            "sample_events": [
                _compact_sample_event(event)
                for event in _limit_list(structured.get("sample_events"), 5)
            ],
        }
    elif evidence_type == "mysql.processlist":
        compact["processlist"] = {
            "aggregates": _selected_dict(
                structured,
                ("total_processes", "user_counts", "command_counts", "state_counts"),
            ),
            "samples": _limit_list(structured.get("samples"), 10),
        }
    elif evidence_type in {"mysql.runtime_status", "mysql.variables"}:
        compact["mysql_counters"] = _selected_dict(
            structured,
            ("selected_counters", "selected_variables", "top_counters", "summary"),
        )
    elif evidence_type in {"metrics.os_cpu", "metrics.os_memory", "metrics.os_disk"}:
        compact["os_metrics"] = _selected_dict(
            structured,
            ("summary", "usage", "load_average", "cpu", "memory", "disk"),
        )
    else:
        compact["structured_summary"] = _selected_dict(
            structured,
            ("summary", "status", "reason", "selected_counters", "selected_variables"),
        )
    return compact


def _compact_top_patterns(patterns: Any) -> list[dict[str, Any]]:
    result = []
    for pattern in _limit_list(patterns, 10):
        if not isinstance(pattern, dict):
            continue
        result.append(
            {
                "pattern": _trim_text(str(pattern.get("pattern") or ""), 240),
                "count": pattern.get("count"),
                "semantic_hint": pattern.get("semantic_hint"),
                "operational_relevance": pattern.get("operational_relevance"),
                "raw_refs": [
                    _compact_raw_ref(ref)
                    for ref in _limit_list(pattern.get("raw_refs"), 5)
                ],
            }
        )
    return result


def _compact_sample_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {"value_type": type(event).__name__}
    return {
        "line_start": event.get("line_start"),
        "line_end": event.get("line_end"),
        "timestamp": event.get("timestamp"),
        "severity": event.get("severity"),
        "semantic_hint": event.get("semantic_hint"),
        "message_length": len(str(event.get("message") or event.get("pattern") or "")),
    }


def _compact_raw_ref(ref: Any) -> dict[str, Any]:
    if not isinstance(ref, dict):
        return {}
    return {
        key: ref.get(key)
        for key in ("content_ref", "line_start", "line_end", "raw_evidence_id")
        if key in ref
    }


def _bound_compact_context(context: dict[str, Any], max_chars: int) -> dict[str, Any]:
    bounded = dict(context)
    if len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)) <= max_chars:
        return bounded

    omitted: list[str] = []
    bounded["context_truncated"] = True
    bounded["truncation_policy"] = "priority_based"

    items = []
    for item in bounded.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        if isinstance(copied.get("error_log"), dict):
            error_log = dict(copied["error_log"])
            if len(error_log.get("sample_events") or []) > 0:
                error_log["sample_events"] = []
                omitted.append(f"{copied.get('evidence_id')}.sample_events")
            if len(error_log.get("top_patterns") or []) > 5:
                error_log["top_patterns"] = error_log["top_patterns"][:5]
                omitted.append(f"{copied.get('evidence_id')}.top_patterns_tail")
            copied["error_log"] = error_log
        if isinstance(copied.get("raw_refs"), list) and len(copied["raw_refs"]) > 3:
            copied["raw_refs"] = copied["raw_refs"][:3]
            omitted.append(f"{copied.get('evidence_id')}.raw_refs_tail")
        items.append(copied)
    bounded["evidence_items"] = items

    if len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)) > max_chars:
        omitted.append("structured_details")
        bounded["evidence_items"] = [
            {
                "evidence_id": item.get("evidence_id"),
                "evidence_type": item.get("evidence_type"),
                "summary": _trim_text(str(item.get("summary") or ""), 220),
                "quality_flags": _limit_list(item.get("quality_flags"), 5),
                "raw_refs": _limit_list(item.get("raw_refs"), 2),
            }
            for item in bounded.get("evidence_items") or []
            if isinstance(item, dict)
        ]
    if len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)) > max_chars:
        omitted.append("long_summaries")
        for item in bounded.get("evidence_items") or []:
            if isinstance(item, dict):
                item["summary"] = _trim_text(str(item.get("summary") or ""), 100)
    if len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)) > max_chars:
        omitted.append("raw_refs")
        for item in bounded.get("evidence_items") or []:
            if isinstance(item, dict):
                item["raw_refs"] = []
                item["summary"] = _trim_text(str(item.get("summary") or ""), 60)
    if len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)) > max_chars:
        omitted.append("coverage_details")
        bounded["coverage"] = {
            "source_raw_evidence_count": (bounded.get("coverage") or {}).get("source_raw_evidence_count"),
            "processed_raw_evidence_count": (bounded.get("coverage") or {}).get("processed_raw_evidence_count"),
        }
        bounded["quality"] = {
            "overall_status": (bounded.get("quality") or {}).get("overall_status"),
            "warnings": _limit_list((bounded.get("quality") or {}).get("warnings"), 3),
        }
    if len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)) > max_chars:
        omitted.append("secondary_metadata")
        bounded.pop("processing_summary", None)
        bounded.pop("artifact_refs", None)
        bounded["incident"] = {
            "time_window": (bounded.get("incident") or {}).get("time_window") or {}
        }
    bounded["omitted_sections"] = list(dict.fromkeys(omitted))
    bounded["actual_prompt_chars"] = len(
        json.dumps(bounded, ensure_ascii=False, sort_keys=True)
    )
    bounded["max_prompt_chars"] = max_chars
    bounded["actual_prompt_chars"] = len(
        json.dumps(bounded, ensure_ascii=False, sort_keys=True)
    )
    if bounded["actual_prompt_chars"] > max_chars:
        omitted.append("quality_warning_tail")
        bounded["quality"] = {
            "overall_status": (bounded.get("quality") or {}).get("overall_status"),
            "warnings": [],
        }
        bounded["omitted_sections"] = list(dict.fromkeys(omitted))
        bounded["actual_prompt_chars"] = len(
            json.dumps(bounded, ensure_ascii=False, sort_keys=True)
        )
    if bounded["actual_prompt_chars"] > max_chars:
        omitted.append("nonessential_context")
        bounded["coverage"] = {}
        bounded["incident"] = {}
        bounded["quality"] = {}
        bounded["omitted_sections"] = list(dict.fromkeys(omitted))
        bounded["actual_prompt_chars"] = len(
            json.dumps(bounded, ensure_ascii=False, sort_keys=True)
        )
    if bounded["actual_prompt_chars"] > max_chars:
        omitted.append("summary_tail")
        for item in bounded.get("evidence_items") or []:
            if isinstance(item, dict):
                item["summary"] = _trim_text(str(item.get("summary") or ""), 40)
        bounded["omitted_sections"] = list(dict.fromkeys(omitted))
        bounded["actual_prompt_chars"] = len(
            json.dumps(bounded, ensure_ascii=False, sort_keys=True)
        )
    return bounded


def _limit_list(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _selected_dict(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value}


def _trim_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _normalize_confidence_values(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[str, ...]]:
    result = dict(payload)
    findings = []
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for finding in payload.get("findings") or []:
        if not isinstance(finding, dict):
            findings.append(finding)
            continue
        copied = dict(finding)
        confidence = copied.get("confidence")
        finding_id = str(copied.get("finding_id") or "")
        if isinstance(confidence, bool):
            errors.append("Finding.confidence must be a number between 0.0 and 1.0")
        elif isinstance(confidence, (int, float)):
            value = float(confidence)
            if 0 <= value <= 1:
                copied["confidence"] = value
            else:
                errors.append("Finding.confidence must be a number between 0.0 and 1.0")
        elif isinstance(confidence, str):
            try:
                value = float(confidence.strip())
            except ValueError:
                errors.append(
                    "Finding.confidence must be a number between 0.0 and 1.0, "
                    f"got string '{confidence}'"
                )
            else:
                if 0 <= value <= 1:
                    copied["confidence"] = value
                    events.append(
                        {
                            "finding_id": finding_id,
                            "original_confidence": confidence,
                            "normalized_confidence": value,
                        }
                    )
                else:
                    errors.append(
                        "Finding.confidence must be a number between 0.0 and 1.0"
                    )
        else:
            errors.append("Finding.confidence must be a number between 0.0 and 1.0")
        findings.append(copied)
    result["findings"] = findings
    return result, tuple(events), tuple(errors)


def _confidence_status(
    confidence_events: tuple[dict[str, Any], ...],
    confidence_errors: tuple[str, ...],
) -> str:
    if confidence_errors:
        return "invalid"
    if confidence_events:
        return "normalized"
    return "not_needed"


def _normalize_validation_statuses(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, str], ...], tuple[str, ...]]:
    result = dict(payload)
    validated = []
    events: list[dict[str, str]] = []
    errors: list[str] = []
    for item in payload.get("validated_findings") or []:
        if not isinstance(item, dict):
            validated.append(item)
            continue
        copied = dict(item)
        original = str(copied.get("validation_status") or "")
        normalized = VALIDATION_STATUS_ALIASES.get(original, original)
        if normalized != original:
            copied["validation_status"] = normalized
            events.append(
                {
                    "finding_id": str(copied.get("finding_id") or ""),
                    "original_validation_status": original,
                    "normalized_validation_status": normalized,
                }
            )
        if normalized not in {
            "passed",
            "downgraded",
            "blocked",
            "requires_human_review",
        }:
            errors.append(
                "validation_status must be one of "
                "passed/downgraded/blocked/requires_human_review, "
                f"got '{original}'"
            )
        validated.append(copied)
    result["validated_findings"] = validated
    return result, tuple(events), tuple(errors)


def _validation_status_status(
    status_events: tuple[dict[str, str], ...],
    status_errors: tuple[str, ...],
) -> str:
    if status_errors:
        return "invalid"
    if status_events:
        return "normalized"
    return "not_needed"


def _sanitize_for_artifact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _sanitize_for_artifact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_artifact(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_artifact(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    text = re.sub(r"<SECRET_REF:[^>]+>", "<SECRET_REF:redacted>", value)
    secret_like = re.compile(
        r"(?i)(password|passwd|pwd|token|secret|api_key|authorization)\s*[:=]\s*[^,\s}]+"
    )
    text = secret_like.sub(lambda match: match.group(1) + "=<redacted>", text)
    return text


def _duration_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)


def _highest_severity(severities: list[str]) -> str:
    order = {severity: index for index, severity in enumerate(ALLOWED_SEVERITIES)}
    if not severities:
        return "info"
    return min(severities, key=lambda severity: order.get(severity, len(order)))


def _next_actions(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for finding in findings:
        for check in finding.get("recommended_next_checks") or []:
            actions.append(
                {
                    "action": str(check),
                    "risk": "low",
                    "requires_approval": False,
                }
            )
    return actions
