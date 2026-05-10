from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from dbkit.agents.evidence_structuring import EvidenceStructuringSubagentRegistration
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
        subagent_registration: EvidenceStructuringSubagentRegistration | None = None,
        max_workers: int = 4,
        per_item_timeout_seconds: int = 30,
        total_timeout_seconds: int = 120,
    ) -> None:
        self.artifact_store = artifact_store
        self.telemetry = telemetry
        self.subagent_registration = (
            subagent_registration
            or EvidenceStructuringSubagentRegistration.from_dirs(
                skills_dir=Path("skills"),
                agents_dir=Path("agents"),
            )
        )
        self.subagent_registration.validate()
        self.max_workers = max(1, int(max_workers))
        self.per_item_timeout_seconds = max(1, int(per_item_timeout_seconds))
        self.total_timeout_seconds = max(1, int(total_timeout_seconds))

    def run(self, raw_evidence_index_path: str | Path) -> EvidenceStructuringResult:
        started_ns = perf_counter_ns()
        index_path = Path(raw_evidence_index_path)
        request_id = "unknown"
        artifacts: list[Any] = []
        self._emit(
            "evidence_structuring_started",
            "Evidence structuring started",
            request_id=request_id,
            raw_evidence_index=str(index_path),
            status="started",
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
            raw_evidence_index=str(index_path),
            raw_evidence_count=len(raw_evidence),
        )
        self._emit(
            "evidence_subagent_invoked",
            "MySQL analyzer delegated evidence structuring to subagent",
            request_id=request_id,
            raw_evidence_index=str(index_path),
            status="started",
        )
        self._emit(
            "build_evidence_bundle_tool_started",
            "build_evidence_bundle deterministic tool started",
            request_id=request_id,
            raw_evidence_index=str(index_path),
            parallel_workers=self.max_workers,
            status="started",
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

        parse_inputs: list[tuple[int, dict[str, Any]]] = []
        skipped: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        deprecated: list[str] = []
        warnings: list[str] = []
        seen_keys: set[tuple[str, str]] = set()
        raw_bytes = 0

        for order, raw in enumerate(raw_evidence):
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
                tool_name="classify_raw_evidence",
                status="completed",
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
                "deduplication_started",
                "Evidence deduplication check started",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                tool_name="deduplicate_events",
                status="started",
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
                    "deduplication_completed",
                    "Duplicate raw evidence skipped",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    tool_name="deduplicate_events",
                    status="skipped",
                    deduplicated=True,
                )
                continue
            seen_keys.add(dedup_key)
            self._emit(
                "deduplication_completed",
                "Evidence deduplication check completed",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                tool_name="deduplicate_events",
                status="completed",
                deduplicated=False,
            )
            parse_inputs.append((order, raw))

        parallel_result = self._process_items_parallel(request_id, parse_inputs)
        evidence_items = [result.item for result in parallel_result if result.item is not None]
        for result in parallel_result:
            warnings.extend(result.warnings)
            skipped.extend(result.skipped)

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
        )
        for item in evidence_items:
            artifact = self.artifact_store.persist_evidence_item(item)
            artifacts.append(artifact)
            self._artifact_written(request_id, artifact)

        bundle_artifact = self.artifact_store.persist_evidence_bundle(bundle)
        tool_result_preview = {
            "status": "evidence_bundle_created",
            "request_id": request_id,
            "artifact": str(bundle_artifact.path),
            "evidence_items": len(bundle.evidence_items),
            "quality": bundle.quality.get("overall_status"),
            "warnings": list(bundle.quality.get("warnings") or []),
            "raw_bytes_processed_inside_tool": bundle.processing_summary.get("raw_bytes", 0),
            "parallel_workers": self.max_workers,
        }
        self._emit(
            "build_evidence_bundle_tool_completed",
            "build_evidence_bundle deterministic tool completed",
            request_id=request_id,
            raw_evidence_index=str(index_path),
            evidence_bundle_artifact=str(bundle_artifact.path),
            tool_result_chars=len(
                json.dumps(tool_result_preview, ensure_ascii=False, sort_keys=True)
            ),
            raw_bytes_processed_inside_tool=bundle.processing_summary.get("raw_bytes", 0),
            evidence_items_processed=len(bundle.evidence_items),
            parallel_workers=self.max_workers,
            duration_ms=max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            status="completed",
        )
        artifacts.append(bundle_artifact)
        self._emit(
            "evidence_bundle_created",
            "Evidence bundle created",
            request_id=request_id,
            raw_evidence_index=str(index_path),
            evidence_bundle_artifact=str(bundle_artifact.path),
            evidence_item_count=len(evidence_items),
            tool_name="build_evidence_bundle",
            status="completed",
        )
        self._emit(
            "evidence_subagent_completed",
            "Evidence structuring subagent completed",
            request_id=request_id,
            raw_evidence_index=str(index_path),
            evidence_bundle_artifact=str(bundle_artifact.path),
            evidence_item_count=len(evidence_items),
            status="completed",
        )
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

    def _process_items_parallel(
        self,
        request_id: str,
        parse_inputs: list[tuple[int, dict[str, Any]]],
    ) -> list["_ItemProcessingResult"]:
        if not parse_inputs:
            return []
        started_ns = perf_counter_ns()
        self._emit(
            "evidence_processing_parallel_started",
            "Evidence item processing started in parallel",
            request_id=request_id,
            max_workers=self.max_workers,
            per_item_timeout_seconds=self.per_item_timeout_seconds,
            total_timeout_seconds=self.total_timeout_seconds,
            item_count=len(parse_inputs),
            status="started",
        )
        results: list[_ItemProcessingResult] = []
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        try:
            futures = {
                executor.submit(
                    self._process_single_item,
                    request_id,
                    order,
                    raw,
                    f"worker-{index % self.max_workers}",
                ): (order, raw)
                for index, (order, raw) in enumerate(parse_inputs)
            }
            done, not_done = wait(futures, timeout=self.total_timeout_seconds)
            for future in done:
                try:
                    results.append(future.result(timeout=0))
                except Exception as exc:  # pragma: no cover - defensive worker boundary
                    order, raw = futures[future]
                    evidence_type = str(raw.get("evidence_type") or "")
                    raw_id = str(raw.get("raw_evidence_id") or "")
                    results.append(
                        _ItemProcessingResult(
                            order=order,
                            item=None,
                            skipped=(
                                {
                                    "raw_evidence_id": raw_id,
                                    "evidence_type": evidence_type,
                                    "reason": "evidence_parser_failed",
                                },
                            ),
                            warnings=(f"{evidence_type} parser failed",),
                        )
                    )
                    self._emit(
                        "evidence_item_processing_failed",
                        "Evidence item processing failed",
                        request_id=request_id,
                        raw_evidence_id=raw_id,
                        evidence_type=evidence_type,
                        error=type(exc).__name__,
                        worker_id="unknown",
                        status="failed",
                    )
            for future in not_done:
                future.cancel()
                order, raw = futures[future]
                evidence_type = str(raw.get("evidence_type") or "")
                raw_id = str(raw.get("raw_evidence_id") or "")
                results.append(
                    _ItemProcessingResult(
                        order=order,
                        item=None,
                        skipped=(
                            {
                                "raw_evidence_id": raw_id,
                                "evidence_type": evidence_type,
                                "reason": "evidence_item_processing_timeout",
                            },
                        ),
                        warnings=(f"{evidence_type} parser timed out",),
                    )
                )
                self._emit(
                    "evidence_item_processing_failed",
                    "Evidence item processing timed out",
                    request_id=request_id,
                    raw_evidence_id=raw_id,
                    evidence_type=evidence_type,
                    worker_id="timeout",
                    status="timeout",
                )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        results.sort(key=lambda result: result.order)
        self._emit(
            "evidence_processing_parallel_completed",
            "Evidence item processing completed in parallel",
            request_id=request_id,
            item_count=len(parse_inputs),
            completed_count=sum(1 for result in results if result.item is not None),
            failed_count=sum(1 for result in results if result.item is None),
            duration_ms=max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            status="completed",
        )
        return results

    def _process_single_item(
        self,
        request_id: str,
        order: int,
        raw: dict[str, Any],
        worker_id: str,
    ) -> "_ItemProcessingResult":
        started_ns = perf_counter_ns()
        raw_id = str(raw.get("raw_evidence_id") or "")
        evidence_type = str(raw.get("evidence_type") or "")
        payload = raw.get("payload") or {}
        content_ref = str(payload.get("content_ref") or "")
        self._emit(
            "evidence_item_processing_started",
            "Evidence item processing started",
            request_id=request_id,
            raw_evidence_id=raw_id,
            evidence_type=evidence_type,
            worker_id=worker_id,
            status="started",
        )
        try:
            raw_text, raw_payload = load_raw_artifact(content_ref)
            self._emit(
                "raw_artifact_loaded",
                "Raw artifact loaded",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                tool_name="load_raw_artifact",
                status="completed",
                worker_id=worker_id,
                bytes=len(raw_text.encode("utf-8")),
            )
            self._emit(
                "raw_artifact_loaded_inside_tool",
                "Raw artifact loaded inside build_evidence_bundle tool",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                tool_name="build_evidence_bundle",
                status="completed",
                worker_id=worker_id,
                bytes=len(raw_text.encode("utf-8")),
            )
        except (OSError, json.JSONDecodeError) as exc:
            self._emit(
                "raw_artifact_load_failed",
                "Raw artifact load failed",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                error=type(exc).__name__,
                worker_id=worker_id,
                status="failed",
            )
            self._emit(
                "evidence_item_processing_failed",
                "Evidence item processing failed",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                worker_id=worker_id,
                status="failed",
                duration_ms=max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            )
            return _ItemProcessingResult(
                order=order,
                item=None,
                skipped=(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "reason": "raw_artifact_load_failed",
                    },
                ),
                warnings=(f"{evidence_type} raw artifact load failed",),
            )

        self._emit(
            "evidence_parser_started",
            "Evidence parser started",
            request_id=request_id,
            raw_evidence_id=raw_id,
            evidence_type=evidence_type,
            tool_name=_parser_tool_name(evidence_type),
            status="started",
            worker_id=worker_id,
        )
        self._emit(
            "time_window_filter_started",
            "Time window filtering started",
            request_id=request_id,
            raw_evidence_id=raw_id,
            evidence_type=evidence_type,
            tool_name="filter_by_time_window",
            status="started",
            worker_id=worker_id,
        )
        parser_started_ns = perf_counter_ns()
        try:
            item = parse_raw_evidence(raw, raw_text, raw_payload)
        except Exception as exc:  # pragma: no cover - defensive parser boundary
            self._emit(
                "evidence_parser_failed",
                "Evidence parser failed",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                error=type(exc).__name__,
                worker_id=worker_id,
                status="failed",
            )
            self._emit(
                "evidence_item_processing_failed",
                "Evidence item processing failed",
                request_id=request_id,
                raw_evidence_id=raw_id,
                evidence_type=evidence_type,
                worker_id=worker_id,
                status="failed",
                duration_ms=max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            )
            return _ItemProcessingResult(
                order=order,
                item=None,
                skipped=(
                    {
                        "raw_evidence_id": raw_id,
                        "evidence_type": evidence_type,
                        "reason": "evidence_parser_failed",
                    },
                ),
                warnings=(f"{evidence_type} parser failed",),
            )

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
            tool_name="filter_by_time_window",
            status="completed",
            worker_id=worker_id,
        )
        self._emit(
            "evidence_parser_completed",
            "Evidence parser completed",
            request_id=request_id,
            raw_evidence_id=raw_id,
            evidence_type=evidence_type,
            tool_name=_parser_tool_name(evidence_type),
            status="partial" if item.quality_flags else "completed",
            duration_ms=parser_duration_ms,
            quality_flags=list(item.quality_flags),
            worker_id=worker_id,
        )
        self._emit(
            "evidence_item_created",
            "Evidence item created",
            request_id=request_id,
            raw_evidence_id=raw_id,
            evidence_id=item.evidence_id,
            evidence_type=evidence_type,
            tool_name="build_evidence_bundle",
            status="completed",
            quality_flags=list(item.quality_flags),
            worker_id=worker_id,
        )
        self._emit(
            "evidence_item_processing_completed",
            "Evidence item processing completed",
            request_id=request_id,
            raw_evidence_id=raw_id,
            evidence_type=evidence_type,
            evidence_id=item.evidence_id,
            worker_id=worker_id,
            duration_ms=max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            status="completed",
            quality_flags=list(item.quality_flags),
        )
        return _ItemProcessingResult(order=order, item=item, skipped=(), warnings=())

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
        raw_context_bytes = raw_bytes + len(
            json.dumps(raw_evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        tokens_before = max(1, raw_context_bytes // 4)
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
        for item in evidence_items:
            if item.quality_flags:
                bundle_warnings.append(
                    f"{item.evidence_type} parsed with low quality: {item.quality_flags[0]}"
                )
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
        runtime_cost_event = self.telemetry.emit_runtime_cost(
            stage="evidence_structuring",
            raw_bytes=raw_bytes,
            filtered_bytes=structured_bytes,
            compression_ratio=compression_ratio,
            estimated_tokens=tokens_after,
            tool_latency_ms=0,
        )
        runtime_cost_event.attributes.setdefault(
            "parent_agent", self.subagent_registration.parent_agent
        )
        runtime_cost_event.attributes.setdefault(
            "subagent", self.subagent_registration.name
        )
        runtime_cost_event.attributes.setdefault("status", "completed")
        runtime_cost_event.attributes.setdefault("tool_name", "estimate_token_size")
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
                "skill": "skills/evidence/SKILL.md",
                "subagent": "evidence_structuring",
                "parent_agent": "mysql_analyzer",
                "runtime_foundation": "DeepAgents SDK",
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
            "evidence_artifact_written",
            "Artifact written",
            request_id=request_id,
            kind=artifact.kind,
            path=str(artifact.path),
            status="completed",
        )

    def _emit(self, event_type: str, message: str, **attributes: Any) -> None:
        attributes.setdefault("parent_agent", self.subagent_registration.parent_agent)
        attributes.setdefault("subagent", self.subagent_registration.name)
        attributes.setdefault("duration_ms", 0)
        self.telemetry.emit(
            event_type=event_type,
            stage="evidence_structuring",
            message=message,
            attributes=attributes,
        )


@dataclass(frozen=True)
class _ItemProcessingResult:
    order: int
    item: EvidenceItem | None
    skipped: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


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


def _parser_tool_name(evidence_type: str) -> str:
    return {
        "mysql.processlist": "parse_mysql_processlist",
        "mysql.runtime_status": "parse_mysql_runtime_status",
        "mysql.innodb_status": "parse_mysql_innodb_status",
        "mysql.variables": "parse_mysql_variables",
        "mysql.service_metadata": "parse_mysql_service_metadata",
        "mysql.log_paths": "parse_mysql_log_paths",
        "mysql.error_log": "parse_mysql_error_log",
        "mysql.slow_log": "parse_mysql_slow_log",
        "metrics.os_cpu": "parse_os_cpu_snapshot",
        "metrics.os_memory": "parse_os_memory_snapshot",
        "metrics.os_disk": "parse_os_disk_snapshot",
        "os.mysql_service_status": "parse_os_mysql_service_status",
    }.get(evidence_type, "classify_raw_evidence")
