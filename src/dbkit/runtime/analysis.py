from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from dbkit.runtime.artifact_paths import to_repo_relative_path
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.json_extraction import extract_json_from_invoke_result
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.schemas.analysis import (
    ALLOWED_SEVERITIES,
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
    ) -> None:
        self.artifact_store = artifact_store
        self.telemetry = telemetry
        self.mysql_analyzer_runtime = mysql_analyzer_runtime
        self.validation_runtime = validation_runtime
        self.repo_dir = repo_dir or Path.cwd()

    def run(
        self,
        evidence_bundle_path: str | Path,
        *,
        expected_request_id: str | None = None,
    ) -> Phase04AnalysisResult:
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

        findings_payload = self._invoke_findings_generation(
            evidence_bundle=evidence_bundle,
            input_bundle_ref=input_bundle_ref,
        )
        if findings_payload is None:
            return self._failed(
                request_id=request_id,
                artifacts=artifacts,
                issues=("findings_generation_parse_failed",),
                started_ns=started_ns,
            )
        findings_draft, findings_error = self._prepare_findings_draft(
            findings_payload=findings_payload,
            evidence_bundle=evidence_bundle,
            input_bundle_ref=input_bundle_ref,
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
            findings_draft=findings_draft,
            input_bundle_ref=input_bundle_ref,
            findings_artifact=findings_artifact.path,
        )
        if validation_payload is None:
            return self._failed(
                request_id=request_id,
                artifacts=artifacts,
                issues=("validation_parse_failed",),
                started_ns=started_ns,
            )
        try:
            validation_result = validate_validation_result(validation_payload, findings_draft)
        except ValueError as exc:
            return self._failed(
                request_id=request_id,
                artifacts=artifacts,
                issues=(f"validation_result_invalid: {exc}",),
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

    def _invoke_findings_generation(
        self,
        *,
        evidence_bundle: dict[str, Any],
        input_bundle_ref: str,
        retry_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        request_id = str(evidence_bundle["request_id"])
        self._emit(
            "mysql_analyzer_findings_generation_started",
            "MySQL analyzer findings_generation started",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            mode="findings_generation",
            status="started",
        )
        result = self.mysql_analyzer_runtime.invoke(
            {
                "mode": "findings_generation",
                "input_evidence_bundle": input_bundle_ref,
                "evidence_bundle": evidence_bundle,
                "retry_context": retry_context or {},
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Output only DBKit FindingsDraft JSON. Consume the "
                            "EvidenceBundle only; do not read RawEvidence.\n\n"
                            + (
                                "Previous FindingsDraft was invalid. Fix these issues:\n"
                                + json.dumps(retry_context, ensure_ascii=False, sort_keys=True)
                                + "\n\n"
                                if retry_context
                                else ""
                            )
                            +
                            "EvidenceBundle JSON:\n"
                            + json.dumps(evidence_bundle, ensure_ascii=False, sort_keys=True)
                        ),
                    }
                ],
            }
        )
        parsed = extract_json_from_invoke_result(result)
        self._emit(
            "mysql_analyzer_findings_generation_completed",
            "MySQL analyzer findings_generation completed",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            mode="findings_generation",
            status="completed" if parsed is not None else "failed",
        )
        return parsed

    def _prepare_findings_draft(
        self,
        *,
        findings_payload: dict[str, Any],
        evidence_bundle: dict[str, Any],
        input_bundle_ref: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        normalized_payload, category_events, invalid_categories = normalize_findings_categories(
            findings_payload
        )
        self._emit_category_events(
            request_id=str(evidence_bundle["request_id"]),
            category_events=category_events,
            invalid_categories=invalid_categories,
        )
        if invalid_categories:
            retry_context = {
                "reason": "invalid_finding_category",
                "invalid_categories": list(invalid_categories),
                "allowed_categories": [
                    "connection",
                    "availability",
                    "performance",
                    "high_cpu",
                    "lock_contention",
                    "slow_query",
                    "configuration",
                    "resource_pressure",
                    "log_signal",
                    "service_state",
                    "unknown",
                ],
            }
            self._emit(
                "findings_generation_retry_requested",
                "Findings generation retry requested by category validation",
                request_id=str(evidence_bundle["request_id"]),
                input_evidence_bundle=input_bundle_ref,
                category_validation_status="retry_requested",
                invalid_categories=list(invalid_categories),
                status="retry_requested",
            )
            retry_payload = self._invoke_findings_generation(
                evidence_bundle=evidence_bundle,
                input_bundle_ref=input_bundle_ref,
                retry_context=retry_context,
            )
            if retry_payload is None:
                return None, "retry_parse_failed"
            normalized_payload, category_events, invalid_categories = normalize_findings_categories(
                retry_payload
            )
            self._emit_category_events(
                request_id=str(evidence_bundle["request_id"]),
                category_events=category_events,
                invalid_categories=invalid_categories,
            )
            if invalid_categories:
                return None, f"Finding.category is invalid: {','.join(invalid_categories)}"
        try:
            return validate_findings_draft(normalized_payload, evidence_bundle), None
        except ValueError as exc:
            return None, str(exc)

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

    def _invoke_validation(
        self,
        *,
        evidence_bundle: dict[str, Any],
        findings_draft: dict[str, Any],
        input_bundle_ref: str,
        findings_artifact: Path,
    ) -> dict[str, Any] | None:
        request_id = str(evidence_bundle["request_id"])
        self._emit(
            "validation_started",
            "Validation agent started",
            request_id=request_id,
            input_evidence_bundle=input_bundle_ref,
            input_findings_artifact=_artifact_ref(findings_artifact, self.repo_dir),
            finding_count=len(findings_draft.get("findings") or []),
            status="started",
        )
        result = self.validation_runtime.invoke(
            {
                "mode": "validation",
                "input_evidence_bundle": input_bundle_ref,
                "findings_draft": findings_draft,
                "evidence_bundle": evidence_bundle,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Output only DBKit ValidationResult JSON. Validate "
                            "FindingsDraft against EvidenceBundle evidence_refs.\n\n"
                            "FindingsDraft JSON:\n"
                            + json.dumps(findings_draft, ensure_ascii=False, sort_keys=True)
                            + "\n\nEvidenceBundle JSON:\n"
                            + json.dumps(evidence_bundle, ensure_ascii=False, sort_keys=True)
                        ),
                    }
                ],
            }
        )
        return extract_json_from_invoke_result(result)

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
