from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.schemas.evidence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceStructuringResult,
    stable_id,
)
from dbkit.tools.evidence import (
    DEPRECATED_EVIDENCE_TYPES,
    estimate_tokens,
    load_raw_artifact,
    parse_raw_evidence,
)


class EvidenceStructuringPipeline:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        telemetry: TelemetryRecorder,
    ) -> None:
        self.artifact_store = artifact_store
        self.telemetry = telemetry

    def run(self, raw_evidence_index_path: str | Path) -> EvidenceStructuringResult:
        index_path = Path(raw_evidence_index_path)
        request_id = "unknown"
        artifacts: list[Any] = []
        self._emit(
            "evidence_structuring_started",
            "Evidence structuring started",
            input_raw_evidence_index=str(index_path),
        )

        if not index_path.exists():
            return self._blocked(
                request_id=request_id,
                artifacts=artifacts,
                issues=(f"raw_evidence_index not found: {index_path}",),
            )

        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return self._blocked(
                request_id=request_id,
                artifacts=artifacts,
                issues=(f"raw_evidence_index load failed: {exc}",),
            )

        request_id = str(index.get("request_id") or "unknown")
        raw_evidence = index.get("raw_evidence") or []
        if not isinstance(raw_evidence, list):
            return self._blocked(
                request_id=request_id,
                artifacts=artifacts,
                issues=("raw_evidence_index.raw_evidence must be a list",),
            )

        self._emit(
            "raw_evidence_index_loaded",
            "Raw evidence index loaded",
            request_id=request_id,
            raw_evidence_count=len(raw_evidence),
        )

        guardrail_issues = self._guardrail_issues(raw_evidence)
        if guardrail_issues:
            return self._blocked(
                request_id=request_id,
                artifacts=artifacts,
                issues=guardrail_issues,
            )

        self._emit(
            "evidence_guardrails_started",
            "Evidence structuring guardrails started",
            request_id=request_id,
        )

        evidence_items: list[EvidenceItem] = []
        skipped: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        deprecated: list[str] = []
        warnings: list[str] = []
        seen_keys: set[tuple[str, str]] = set()
        raw_bytes = 0
        loaded_raw_texts: list[str] = []

        for raw in raw_evidence:
            if not isinstance(raw, dict):
                skipped.append({"reason": "raw_evidence_entry_not_object"})
                continue

            raw_id = str(raw.get("raw_evidence_id") or "")
            evidence_type = str(raw.get("evidence_type") or "")
            status = str((raw.get("collection") or {}).get("status") or "failed")
            payload = raw.get("payload") or {}
            raw_bytes += int(payload.get("bytes") or 0)

            self._emit(
                "raw_evidence_classified",
                "Raw evidence classified",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                collection_status=status,
            )

            if evidence_type in DEPRECATED_EVIDENCE_TYPES:
                deprecated.append(evidence_type)
                skipped.append(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "reason": "deprecated_duplicate_evidence_type",
                    }
                )
                self._emit(
                    "evidence_item_skipped",
                    "Deprecated raw evidence skipped",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    reason="deprecated_duplicate_evidence_type",
                )
                continue

            if status == "not_available":
                reason = str((raw.get("collection") or {}).get("reason") or "not_available")
                unavailable.append(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "status": status,
                        "reason": reason,
                    }
                )
                warnings.append(f"{evidence_type} not available: {reason}")
                skipped.append(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "reason": reason,
                    }
                )
                self._emit(
                    "evidence_item_skipped",
                    "Unavailable raw evidence skipped",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    reason=reason,
                )
                continue

            if status != "collected":
                reason = str((raw.get("collection") or {}).get("reason") or status)
                warnings.append(f"{evidence_type} collection status {status}: {reason}")
                skipped.append(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "reason": reason,
                    }
                )
                self._emit(
                    "evidence_item_skipped",
                    "Non-collected raw evidence skipped",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    reason=reason,
                )
                continue

            content_ref = str(payload.get("content_ref") or "")
            dedup_key = (evidence_type, content_ref)
            self._emit(
                "dedup_started",
                "Evidence deduplication check started",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
            )
            if dedup_key in seen_keys:
                skipped.append(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "reason": "duplicate_content_ref",
                    }
                )
                self._emit(
                    "dedup_completed",
                    "Duplicate raw evidence skipped",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    deduplicated=True,
                )
                continue
            seen_keys.add(dedup_key)
            self._emit(
                "dedup_completed",
                "Evidence deduplication check completed",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                deduplicated=False,
            )

            try:
                raw_text, raw_payload = load_raw_artifact(content_ref)
                loaded_raw_texts.append(raw_text)
                self._emit(
                    "raw_artifact_loaded",
                    "Raw artifact loaded",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    bytes=len(raw_text.encode("utf-8")),
                )
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f"{evidence_type} raw artifact load failed")
                skipped.append(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "reason": "raw_artifact_load_failed",
                    }
                )
                self._emit(
                    "raw_artifact_load_failed",
                    "Raw artifact load failed",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    error=type(exc).__name__,
                )
                continue

            self._emit(
                "evidence_parser_started",
                "Evidence parser started",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
            )
            self._emit(
                "time_window_filter_started",
                "Time window filtering started",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
            )
            parser_started_ns = perf_counter_ns()
            try:
                item = parse_raw_evidence(raw, raw_text, raw_payload)
            except Exception as exc:  # pragma: no cover - defensive parser boundary
                warnings.append(f"{evidence_type} parser failed")
                skipped.append(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "reason": "evidence_parser_failed",
                    }
                )
                self._emit(
                    "evidence_parser_failed",
                    "Evidence parser failed",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    error=type(exc).__name__,
                )
                continue
            parser_duration_ms = max(0, (perf_counter_ns() - parser_started_ns) // 1_000_000)
            structured = item.structured_payload
            self._emit(
                "time_window_filter_completed",
                "Time window filtering completed",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                timestamp_parse_status=item.time_range.get("timestamp_parse_status"),
                total_lines=structured.get("total_lines"),
                parsed_timestamp_lines=structured.get("parsed_timestamp_lines"),
                unparseable_lines=structured.get("unparseable_lines"),
                retained_lines=structured.get("retained_lines"),
                discarded_lines=structured.get("discarded_lines"),
                time_window_filter_status=structured.get("time_window_filter_status"),
                collection_time_window_coverage=structured.get("collection_time_window_coverage"),
                duration_ms=parser_duration_ms,
            )
            self._emit(
                "evidence_parser_completed",
                "Evidence parser completed",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                status="partial" if item.quality_flags else "completed",
                duration_ms=parser_duration_ms,
            )
            evidence_items.append(item)
            self._emit(
                "evidence_item_created",
                "Evidence item created",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_id=item.evidence_id,
                evidence_type=evidence_type,
            )

        self._emit(
            "evidence_guardrails_passed",
            "Evidence structuring guardrails passed",
            request_id=request_id,
        )

        bundle = self._build_bundle(
            request_id=request_id,
            index_path=index_path,
            raw_evidence=raw_evidence,
            evidence_items=evidence_items,
            skipped=skipped,
            unavailable=unavailable,
            deprecated=deprecated,
            warnings=warnings,
            raw_bytes=raw_bytes,
            loaded_raw_texts=loaded_raw_texts,
        )
        self._emit(
            "evidence_bundle_created",
            "Evidence bundle created",
            request_id=request_id,
            evidence_item_count=len(evidence_items),
        )

        for item in evidence_items:
            artifact = self.artifact_store.persist_evidence_item(item)
            artifacts.append(artifact)
            self._artifact_written(request_id, artifact)

        bundle_artifact = self.artifact_store.persist_evidence_bundle(bundle)
        artifacts.append(bundle_artifact)
        self._artifact_written(request_id, bundle_artifact)
        telemetry_artifact = self.artifact_store.persist_evidence_processing_telemetry(
            request_id, self.telemetry.events
        )
        artifacts.append(telemetry_artifact)

        return EvidenceStructuringResult(
            request_id=request_id,
            phase="phase-03",
            status="evidence_bundle_created",
            bundle=bundle,
            bundle_artifact=bundle_artifact,
            artifacts=tuple(artifacts),
            telemetry=tuple(self.telemetry.events),
        )

    def _build_bundle(
        self,
        *,
        request_id: str,
        index_path: Path,
        raw_evidence: list[Any],
        evidence_items: list[EvidenceItem],
        skipped: list[dict[str, Any]],
        unavailable: list[dict[str, Any]],
        deprecated: list[str],
        warnings: list[str],
        raw_bytes: int,
        loaded_raw_texts: list[str],
    ) -> EvidenceBundle:
        items_payload = [item.to_dict() for item in evidence_items]
        llm_context_payload = [
            {
                "evidence_type": item.evidence_type,
                "summary": item.summary,
                "structured_payload": item.structured_payload,
                "quality_flags": list(item.quality_flags),
            }
            for item in evidence_items
        ]
        serialized_items = json.dumps(items_payload, ensure_ascii=False, sort_keys=True)
        serialized_llm_context = json.dumps(
            llm_context_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        structured_bytes = len(serialized_items.encode("utf-8"))
        raw_text = "\n".join(loaded_raw_texts)
        raw_context_text = raw_text + "\n" + json.dumps(
            raw_evidence,
            ensure_ascii=False,
            sort_keys=True,
        )
        tokens_before = (
            estimate_tokens(raw_context_text) if raw_context_text.strip() else max(1, raw_bytes // 4)
        )
        tokens_after = estimate_tokens(serialized_llm_context)
        compression_ratio = (
            round(structured_bytes / raw_bytes, 6) if raw_bytes > 0 else 1.0
        )
        bundle_warnings = list(dict.fromkeys(warnings))
        for item in evidence_items:
            if item.evidence_type != "mysql.error_log":
                continue
            if "time_window_coverage_unknown" in item.quality_flags:
                bundle_warnings.append("error_log collection coverage unknown")
                bundle_warnings.append("error_log time_window may be incomplete")
            if "timestamp_parse_partial" in item.quality_flags:
                bundle_warnings.append("error_log parsed with partial timestamp coverage")
            if "timestamp_parse_failed" in item.quality_flags:
                bundle_warnings.append("error_log timestamp parsing unavailable")
            if "timezone_inference_failed" in item.quality_flags:
                bundle_warnings.append("error_log timezone inference failed")
        if deprecated:
            bundle_warnings.append("deprecated duplicate MySQL metrics evidence skipped")
        quality = {
            "overall_status": _quality_status(evidence_items, bundle_warnings),
            "warnings": bundle_warnings,
            "llm_safe": True,
        }
        payload = {
            "available_evidence": [item.evidence_type for item in evidence_items],
            "unavailable_evidence": unavailable,
            "deprecated_evidence_types": sorted(set(deprecated)),
            "low_quality_evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "evidence_type": item.evidence_type,
                    "quality_flags": list(item.quality_flags),
                }
                for item in evidence_items
                if item.quality_flags
            ],
        }
        summary = {
            "raw_bytes": raw_bytes,
            "structured_bytes": structured_bytes,
            "compression_ratio": compression_ratio,
            "estimated_tokens_before": tokens_before,
            "estimated_tokens_after": tokens_after,
            "deduplicated_or_skipped_count": len(skipped),
            "unavailable_count": len(unavailable),
            "deprecated_count": len(set(deprecated)),
        }
        self.telemetry.emit_runtime_cost(
            stage="evidence_structuring",
            raw_bytes=raw_bytes,
            filtered_bytes=structured_bytes,
            compression_ratio=compression_ratio,
            estimated_tokens=tokens_after,
            tool_latency_ms=0,
        )
        return EvidenceBundle(
            request_id=request_id,
            phase="phase-03",
            bundle_id=stable_id("evb", request_id, str(index_path)),
            input_raw_evidence_index=str(index_path),
            source_raw_evidence_count=len(raw_evidence),
            processed_raw_evidence_count=len(evidence_items),
            time_window=_first_time_window(raw_evidence),
            evidence_items=tuple(evidence_items),
            coverage=payload,
            quality=quality,
            processing_summary=summary,
            skipped_raw_evidence=tuple(skipped),
            metadata={
                "phase_detail": "phase-03-evidence-structuring-mvp",
                "input_phase": "phase-02.1",
            },
        )

    def _guardrail_issues(self, raw_evidence: list[Any]) -> tuple[str, ...]:
        issues: list[str] = []
        for raw in raw_evidence:
            if not isinstance(raw, dict):
                issues.append("raw evidence entry is not an object")
                continue
            status = str((raw.get("collection") or {}).get("status") or "")
            if status != "collected":
                continue
            raw_id = str(raw.get("raw_evidence_id") or "unknown")
            payload = raw.get("payload") or {}
            content_ref = payload.get("content_ref")
            if not content_ref:
                issues.append(
                    f"content_ref missing for collected raw evidence: {raw_id}"
                )
                continue
            if not Path(str(content_ref)).exists():
                issues.append(
                    f"content_ref not found for collected raw evidence: {raw_id}"
                )
        return tuple(issues)

    def _blocked(
        self,
        *,
        request_id: str,
        artifacts: list[Any],
        issues: tuple[str, ...],
    ) -> EvidenceStructuringResult:
        self._emit(
            "evidence_guardrails_blocked",
            "Evidence structuring guardrails blocked",
            request_id=request_id,
            blocking_issues=list(issues),
        )
        telemetry_artifact = self.artifact_store.persist_evidence_processing_telemetry(
            request_id, self.telemetry.events
        )
        artifacts.append(telemetry_artifact)
        return EvidenceStructuringResult(
            request_id=request_id,
            phase="phase-03",
            status="evidence_guardrails_failed",
            bundle=None,
            bundle_artifact=None,
            artifacts=tuple(artifacts),
            telemetry=tuple(self.telemetry.events),
            blocking_issues=issues,
        )

    def _artifact_written(self, request_id: str, artifact: Any) -> None:
        self._emit(
            "artifact_written",
            "Artifact written",
            request_id=request_id,
            kind=artifact.kind,
            path=str(artifact.path),
        )

    def _emit(self, event_type: str, message: str, **attributes: Any) -> None:
        self.telemetry.emit(
            event_type=event_type,
            stage="evidence_structuring",
            message=message,
            attributes=attributes,
        )


def _first_time_window(raw_evidence: list[Any]) -> dict[str, Any]:
    for raw in raw_evidence:
        if isinstance(raw, dict):
            time_window = (raw.get("metadata") or {}).get("time_window")
            if isinstance(time_window, dict):
                return dict(time_window)
    return {}


def _quality_status(
    evidence_items: list[EvidenceItem],
    warnings: list[str],
) -> str:
    if not evidence_items:
        return "insufficient"
    return "usable_with_warnings" if warnings else "usable"
