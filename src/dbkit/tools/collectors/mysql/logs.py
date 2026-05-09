from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from dbkit.runtime.collection_guardrails import is_mysql_sql_allowed
from dbkit.schemas.evidence import CollectionStep, RawEvidence
from dbkit.schemas.runtime import NormalizedRequest
from dbkit.tools.collectors.common import error_raw_evidence, json_raw_evidence, text_raw_evidence
from dbkit.tools.collectors.mysql.service import MySQLClient
from dbkit.tools.evidence import filter_log_text_to_time_window


_LOG_VARIABLE_SQL = (
    "SHOW GLOBAL VARIABLES LIKE 'log_error'",
    "SHOW GLOBAL VARIABLES LIKE 'slow_query_log_file'",
    "SHOW GLOBAL VARIABLES LIKE 'slow_query_log'",
    "SHOW GLOBAL VARIABLES LIKE 'log_output'",
    "SHOW GLOBAL VARIABLES LIKE 'datadir'",
)


def discover_mysql_log_paths(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    mysql_client: MySQLClient,
    started_at: str,
    completed_at: str,
) -> RawEvidence:
    try:
        data = discover_log_paths(mysql_client)
        status = "not_available" if data.get("reason") else "collected"
        return json_raw_evidence(
            step=step,
            request=request,
            raw_root=raw_root,
            data=data,
            source={
                "kind": "mysql",
                "path": None,
                "host": (request.target or {}).get("host"),
                "tool_name": step.tool_name,
            },
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            reason=data.get("reason"),
        )
    except Exception as exc:
        return error_raw_evidence(
            step=step,
            request=request,
            status="failed",
            started_at=started_at,
            completed_at=completed_at,
            error=str(exc),
            source_kind="mysql",
        )


def collect_mysql_log_file(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    mysql_client: MySQLClient,
    ssh_client: Any | None,
    ssh_client_factory: Callable[[], Any | None] | None,
    started_at: str,
    completed_at: str,
    tail_lines: int = 5000,
    max_bytes: int = 10_485_760,
    time_window_scan_max_bytes: int = 52_428_800,
    prefer_time_window_scan: bool = True,
) -> RawEvidence:
    log_kind = "slow" if step.tool_name == "collect_mysql_slow_log" else "error"
    try:
        discovery = discover_log_paths(mysql_client)
        unavailable = _unavailable_reason(discovery, log_kind)
        if unavailable:
            return error_raw_evidence(
                step=step,
                request=request,
                status="not_available",
                started_at=started_at,
                completed_at=completed_at,
                error=unavailable,
                source_kind="mysql",
                reason=unavailable,
            )
        path = str(discovery[f"{log_kind}_log_path"])
        if ssh_client is None and ssh_client_factory is not None:
            ssh_client = ssh_client_factory()
        if ssh_client is None:
            return error_raw_evidence(
                step=step,
                request=request,
                status="not_available",
                started_at=started_at,
                completed_at=completed_at,
                error="ssh_target_required_for_remote_log_read",
                source_kind="ssh_file",
                reason="ssh_target_required_for_remote_log_read",
            )
        time_window = (request.event or {}).get("time_window") or {}
        if prefer_time_window_scan and time_window:
            try:
                scan_content = _tail_bytes(ssh_client, path, time_window_scan_max_bytes)
            except Exception:
                scan_content = ""
            if scan_content:
                filtered_content, stats = filter_log_text_to_time_window(scan_content, time_window)
                scan_bytes = len(scan_content.encode("utf-8"))
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
                if coverage == "partial_or_unknown":
                    metadata["coverage_warning"] = "scan chunk may not cover requested time_window"
                return text_raw_evidence(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    content=filtered_content,
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
            metadata={
                "collection_strategy": "bounded_tail_fallback",
                "time_window_aware": False,
                "time_window_coverage": "unknown",
                "coverage_warning": "tail_lines may not cover requested time_window",
                "tail_lines": int(tail_lines),
                "max_bytes": int(max_bytes),
            },
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


def _tail_bytes(ssh_client: Any, path: str, max_bytes: int) -> str:
    tail_bytes = getattr(ssh_client, "tail_bytes", None)
    if callable(tail_bytes):
        return str(tail_bytes(path, int(max_bytes)))
    return str(ssh_client.exec(f"tail -c {int(max_bytes)} -- {path}"))


def discover_log_paths(mysql_client: MySQLClient) -> dict[str, Any]:
    values: dict[str, str] = {}
    for sql in _LOG_VARIABLE_SQL:
        if not is_mysql_sql_allowed(sql):
            raise RuntimeError(f"blocked unsafe SQL: {sql}")
        rows = mysql_client.execute(sql)
        for row in rows:
            name = str(row.get("Variable_name") or row.get("variable_name") or "")
            values[name] = str(row.get("Value") or row.get("value") or "")
    datadir = values.get("datadir", "")
    log_output = values.get("log_output", "FILE").upper()
    if log_output == "TABLE":
        return {
            "error_log_path": None,
            "slow_log_path": None,
            "slow_query_log_enabled": _truthy(values.get("slow_query_log")),
            "log_output": log_output,
            "datadir": datadir,
            "reason": "log_output_table_not_supported_in_phase_02_1",
        }
    return {
        "error_log_path": _resolve_path(values.get("log_error"), datadir),
        "slow_log_path": _resolve_path(values.get("slow_query_log_file"), datadir),
        "slow_query_log_enabled": _truthy(values.get("slow_query_log")),
        "log_output": log_output,
        "datadir": datadir,
    }


def _unavailable_reason(discovery: dict[str, Any], log_kind: str) -> str | None:
    if discovery.get("reason"):
        return str(discovery["reason"])
    if log_kind == "slow" and not discovery.get("slow_query_log_enabled"):
        return "slow_query_log_disabled"
    if not discovery.get(f"{log_kind}_log_path"):
        return f"{log_kind}_log_path_empty"
    return None


def _resolve_path(path: str | None, datadir: str) -> str | None:
    if not path:
        return None
    value = str(path)
    if value.startswith("/"):
        return value
    return str(Path(datadir or "/").joinpath(value))


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().upper() in {"ON", "1", "YES", "TRUE"}
