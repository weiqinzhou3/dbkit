from __future__ import annotations

from pathlib import Path
from typing import Any

from dbkit.runtime.collection_guardrails import is_ssh_command_allowed
from dbkit.schemas.evidence import CollectionStep, RawEvidence
from dbkit.schemas.runtime import NormalizedRequest
from dbkit.tools.collectors.common import error_raw_evidence, text_raw_evidence
from dbkit.tools.evidence import filter_log_text_to_time_window


def read_remote_file(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    ssh_client: Any,
    started_at: str,
    completed_at: str,
    tail_lines: int = 5000,
    max_bytes: int = 10_485_760,
    time_window_scan_max_bytes: int = 52_428_800,
    prefer_time_window_scan: bool = True,
) -> RawEvidence:
    path = step.source_path or ""
    time_window = (request.event or {}).get("time_window") or {}
    if prefer_time_window_scan and time_window:
        command = f"tail -c {int(time_window_scan_max_bytes)} -- {path}"
    else:
        command = f"tail -n {int(tail_lines)} -- {path}"
    if not is_ssh_command_allowed(command):
        return error_raw_evidence(
            step=step,
            request=request,
            status="blocked",
            started_at=started_at,
            completed_at=completed_at,
            error=f"blocked unsafe SSH command: {command}",
            source_kind="ssh_file",
            reason="unsafe_remote_file_path",
        )
    try:
        metadata = {
            "collection_strategy": "bounded_tail_fallback",
            "time_window_aware": False,
            "time_window_coverage": "unknown",
            "coverage_warning": "tail_lines may not cover requested time_window",
            "tail_lines": int(tail_lines),
            "max_bytes": int(max_bytes),
        }
        if prefer_time_window_scan and time_window:
            tail_bytes = getattr(ssh_client, "tail_bytes", None)
            scan_content = (
                tail_bytes(path, int(time_window_scan_max_bytes))
                if callable(tail_bytes)
                else ssh_client.exec(command)
            )
            if scan_content:
                content, stats = filter_log_text_to_time_window(
                    str(scan_content),
                    time_window,
                )
                scan_bytes = len(str(scan_content).encode("utf-8"))
                coverage = (
                    "partial_or_unknown"
                    if scan_bytes >= int(time_window_scan_max_bytes)
                    else "attempted"
                )
                metadata = {
                    "collection_strategy": "time_window_scan",
                    "time_window_aware": True,
                    "time_window_coverage": coverage,
                    "scan_scope": f"tail -c {int(time_window_scan_max_bytes)}",
                    "matched_lines": stats["matched_lines"],
                    "discarded_lines": stats["discarded_lines"],
                    "matched_events": stats["matched_events"],
                    "discarded_events": stats["discarded_events"],
                    "tail_lines": int(tail_lines),
                    "max_bytes": int(max_bytes),
                    "time_window_scan_max_bytes": int(time_window_scan_max_bytes),
                }
            else:
                content = ssh_client.tail(path, tail_lines)
        else:
            content = ssh_client.tail(path, tail_lines)
        return text_raw_evidence(
            step=step,
            request=request,
            raw_root=raw_root,
            content=content,
            source={
                "kind": "ssh_file",
                "path": path,
                "host": (request.ssh_target or {}).get("host"),
                "tool_name": step.tool_name,
            },
            started_at=started_at,
            completed_at=completed_at,
            metadata=metadata,
        )
    except Exception as exc:
        return error_raw_evidence(
            step=step,
            request=request,
            status="failed",
            started_at=started_at,
            completed_at=completed_at,
            error=str(exc),
            source_kind="ssh_file",
        )
