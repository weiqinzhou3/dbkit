from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from dbkit.schemas.evidence import EvidenceItem, stable_id


DEPRECATED_EVIDENCE_TYPES = frozenset(
    {"metrics.mysql", "metrics.mysql_status", "metrics.mysql_variables"}
)
FORBIDDEN_OUTPUT_KEYS = frozenset(
    {"root_cause", "findings", "verdict", "final_summary", "recommendations"}
)
SELECTED_STATUS_COUNTERS = (
    "Threads_connected",
    "Threads_running",
    "Max_used_connections",
    "Connections",
    "Aborted_connects",
    "Aborted_clients",
    "Questions",
    "Queries",
    "Slow_queries",
    "Created_tmp_disk_tables",
    "Created_tmp_tables",
    "Handler_read_rnd_next",
    "Innodb_row_lock_waits",
    "Innodb_buffer_pool_reads",
)
SELECTED_VARIABLES = (
    "max_connections",
    "slow_query_log",
    "slow_query_log_file",
    "log_output",
    "long_query_time",
    "datadir",
    "log_error",
    "innodb_buffer_pool_size",
)


def load_raw_artifact(content_ref: str) -> tuple[str, Any]:
    path = Path(content_ref)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return text, json.loads(text)
    return text, text


def parse_raw_evidence(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    evidence_type = str(raw["evidence_type"])
    parser = {
        "mysql.processlist": parse_mysql_processlist,
        "mysql.runtime_status": parse_mysql_runtime_status,
        "mysql.innodb_status": parse_mysql_innodb_status,
        "mysql.variables": parse_mysql_variables,
        "mysql.service_metadata": parse_mysql_service_metadata,
        "mysql.log_paths": parse_mysql_log_paths,
        "mysql.error_log": parse_mysql_error_log,
        "mysql.slow_log": parse_mysql_slow_log,
        "metrics.os_cpu": parse_os_cpu_snapshot,
        "metrics.os_memory": parse_os_memory_snapshot,
        "metrics.os_disk": parse_os_disk_snapshot,
        "os.mysql_service_status": parse_os_mysql_service_status,
    }.get(evidence_type)
    if parser is None:
        return _generic_item(raw, raw_text, "Unsupported evidence type", {"parser": "generic"})
    return parser(raw, raw_text, raw_payload)


def parse_mysql_processlist(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    rows = _rows(raw_payload)
    commands = Counter(str(row.get("Command") or "unknown") for row in rows)
    states = Counter(str(row.get("State") or "none") for row in rows)
    users = Counter(str(row.get("User") or "unknown") for row in rows)
    hosts = Counter(str(row.get("Host") or "unknown").split(":")[0] for row in rows)
    active = [row for row in rows if str(row.get("Command") or "").lower() != "sleep"]
    sleeping = [row for row in rows if str(row.get("Command") or "").lower() == "sleep"]
    long_running = [row for row in rows if _to_int(row.get("Time")) >= 60]
    samples = [
        {
            "id": row.get("Id"),
            "user": row.get("User"),
            "host": row.get("Host"),
            "command": row.get("Command"),
            "time": _to_int(row.get("Time")),
            "state": row.get("State"),
            "info_sample": _sample(str(row.get("Info") or ""), 160),
        }
        for row in active[:5]
    ]
    return _item(
        raw,
        summary=f"Processlist contains {len(rows)} connections and {len(active)} active queries.",
        structured_payload={
            "total_connections": len(rows),
            "active_queries": len(active),
            "sleeping_connections": len(sleeping),
            "long_running_queries": len(long_running),
            "top_users": dict(users.most_common(5)),
            "top_hosts": dict(hosts.most_common(5)),
            "top_command_types": dict(commands.most_common(5)),
            "top_states": dict(states.most_common(5)),
            "query_samples": samples,
        },
        raw_text=raw_text,
    )


def parse_mysql_runtime_status(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    values = _name_value_map(_rows(raw_payload))
    selected = {
        key: _numeric_if_possible(values[key])
        for key in SELECTED_STATUS_COUNTERS
        if key in values
    }
    return _item(
        raw,
        summary=f"Runtime status contains {len(values)} status variables.",
        structured_payload={
            "selected_counters": selected,
            "status_variable_count": len(values),
        },
        raw_text=raw_text,
    )


def parse_mysql_innodb_status(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    status = "\n".join(str(row.get("Status") or "") for row in _rows(raw_payload))
    upper = status.upper()
    sections = [
        section
        for section in ("TRANSACTIONS", "BUFFER POOL", "ROW OPERATIONS", "LATEST DETECTED DEADLOCK")
        if section in upper
    ]
    return _item(
        raw,
        summary=f"InnoDB status contains {len(sections)} detectable sections.",
        structured_payload={
            "sections_detected": sections,
            "deadlock_section_present": "LATEST DETECTED DEADLOCK" in upper,
            "transaction_section_present": "TRANSACTIONS" in upper,
            "buffer_pool_section_present": "BUFFER POOL" in upper,
            "status_sample": _sample(status, 500),
        },
        raw_text=raw_text,
    )


def parse_mysql_variables(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    values = _name_value_map(_rows(raw_payload))
    selected = {key: values[key] for key in SELECTED_VARIABLES if key in values}
    return _item(
        raw,
        summary=f"MySQL variables contain {len(values)} configuration values.",
        structured_payload={
            "selected_variables": selected,
            "variable_count": len(values),
        },
        raw_text=raw_text,
    )


def parse_mysql_service_metadata(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    rows = []
    for query in raw_payload.get("queries", []) if isinstance(raw_payload, dict) else []:
        rows.extend(query.get("rows") or [])
    merged: dict[str, Any] = {}
    for row in rows:
        merged.update(row)
    version = str(merged.get("VERSION()") or merged.get("version") or "")
    payload = {
        "version": version,
        "version_family": _version_family(version),
        "hostname": merged.get("@@hostname"),
        "port": merged.get("@@port"),
        "datadir": merged.get("@@datadir"),
        "log_error": merged.get("@@log_error"),
        "slow_query_log_file": merged.get("@@slow_query_log_file"),
    }
    return _item(
        raw,
        summary=f"MySQL service metadata identifies version {version or 'unknown'}.",
        structured_payload=payload,
        raw_text=raw_text,
    )


def parse_mysql_log_paths(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    payload = {
        key: raw_payload.get(key)
        for key in ("error_log_path", "slow_log_path", "slow_query_log_enabled", "log_output", "datadir")
        if isinstance(raw_payload, dict) and key in raw_payload
    }
    return _item(
        raw,
        summary="MySQL log path discovery is available.",
        structured_payload=payload,
        raw_text=raw_text,
    )


def parse_mysql_error_log(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    return _parse_log(raw, raw_text, log_name="Error log")


def parse_mysql_slow_log(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    patterns = Counter(_slow_digest(line) for line in raw_text.splitlines() if line.strip())
    return _item(
        raw,
        summary=f"Slow log contains {sum(patterns.values())} retained lines grouped into {len(patterns)} patterns.",
        structured_payload={
            "top_query_patterns": [{"pattern": pattern, "count": count} for pattern, count in patterns.most_common(10)],
            "retained_lines": sum(patterns.values()),
            "discarded_lines": 0,
        },
        raw_text=raw_text,
        timestamp_parse_status="failed",
        quality_flags=("timestamp_parse_failed",),
    )


def parse_os_cpu_snapshot(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    load_match = re.search(r"load average[s]?:\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)", raw_text)
    payload = {
        "load_average": [float(value) for value in load_match.groups()] if load_match else [],
        "has_vmstat": "vmstat" in raw_text,
        "sample": _sample(raw_text, 500),
    }
    return _item(raw, summary="OS CPU snapshot structured.", structured_payload=payload, raw_text=raw_text, timestamp_parse_status="not_applicable")


def parse_os_memory_snapshot(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    payload = {"has_free_output": "Mem:" in raw_text, "has_swap_output": "Swap:" in raw_text, "sample": _sample(raw_text, 500)}
    return _item(raw, summary="OS memory snapshot structured.", structured_payload=payload, raw_text=raw_text, timestamp_parse_status="not_applicable")


def parse_os_disk_snapshot(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    usages = re.findall(r"\s(\d+)%\s", raw_text)
    payload = {"max_usage_percent": max([int(value) for value in usages], default=None), "sample": _sample(raw_text, 500)}
    return _item(raw, summary="OS disk snapshot structured.", structured_payload=payload, raw_text=raw_text, timestamp_parse_status="not_applicable")


def parse_os_mysql_service_status(raw: dict[str, Any], raw_text: str, raw_payload: Any) -> EvidenceItem:
    active = "active (running)" in raw_text.lower()
    payload = {"active_running": active, "mysqld_process_present": "mysqld" in raw_text, "sample": _sample(raw_text, 500)}
    return _item(raw, summary="MySQL service status snapshot structured.", structured_payload=payload, raw_text=raw_text, timestamp_parse_status="not_applicable")


def estimate_tokens(text: str) -> int:
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _parse_log(raw: dict[str, Any], raw_text: str, *, log_name: str) -> EvidenceItem:
    window = _time_window(raw)
    lines = raw_text.splitlines()
    retained: list[tuple[int, str]] = []
    discarded = 0
    parsed_timestamps = 0
    for index, line in enumerate(lines, start=1):
        timestamp = _extract_timestamp(line)
        if timestamp is None:
            retained.append((index, line))
            continue
        parsed_timestamps += 1
        if _in_window(timestamp, window):
            retained.append((index, line))
        else:
            discarded += 1
    status = "ok" if parsed_timestamps else "failed"
    patterns = Counter(_log_pattern(line) for _, line in retained if line.strip())
    flags = () if status == "ok" else ("timestamp_parse_failed",)
    return _item(
        raw,
        summary=f"{log_name} has {len(retained)} retained lines and {len(patterns)} top patterns.",
        structured_payload={
            "top_patterns": [{"pattern": pattern, "count": count} for pattern, count in patterns.most_common(10)],
            "retained_lines": len(retained),
            "discarded_lines": discarded,
        },
        raw_text=raw_text,
        raw_refs=_line_refs(raw, retained),
        timestamp_parse_status=status,
        quality_flags=flags,
    )


def _item(
    raw: dict[str, Any],
    *,
    summary: str,
    structured_payload: dict[str, Any],
    raw_text: str,
    raw_refs: tuple[dict[str, Any], ...] | None = None,
    timestamp_parse_status: str = "not_applicable",
    quality_flags: tuple[str, ...] = (),
) -> EvidenceItem:
    content_ref = str((raw.get("payload") or {}).get("content_ref") or "")
    evidence_id = stable_id("ev", raw.get("raw_evidence_id"), raw.get("evidence_type"))
    refs = raw_refs or ({
        "raw_evidence_id": raw["raw_evidence_id"],
        "content_ref": content_ref,
        "line_start": 1,
        "line_end": max(1, len(raw_text.splitlines())),
    },)
    window = _time_window(raw)
    return EvidenceItem(
        evidence_id=evidence_id,
        raw_evidence_id=str(raw["raw_evidence_id"]),
        evidence_type=str(raw["evidence_type"]),
        source=dict(raw.get("source") or {}),
        time_range={
            "start": window.get("start"),
            "end": window.get("end"),
            "timestamp_parse_status": timestamp_parse_status,
        },
        summary=summary,
        structured_payload=_strip_forbidden(structured_payload),
        raw_refs=tuple(refs),
        quality_flags=quality_flags,
        llm_safe=True,
    )


def _generic_item(raw: dict[str, Any], raw_text: str, summary: str, payload: dict[str, Any]) -> EvidenceItem:
    return _item(raw, summary=summary, structured_payload=payload, raw_text=raw_text, quality_flags=("parser_partial",))


def _rows(raw_payload: Any) -> list[dict[str, Any]]:
    rows = raw_payload.get("rows", []) if isinstance(raw_payload, dict) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _name_value_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in rows:
        name = row.get("Variable_name") or row.get("variable_name")
        if name is not None:
            values[str(name)] = str(row.get("Value") or row.get("value") or "")
    return values


def _line_refs(raw: dict[str, Any], retained: list[tuple[int, str]]) -> tuple[dict[str, Any], ...]:
    if not retained:
        return ()
    return ({
        "raw_evidence_id": raw["raw_evidence_id"],
        "content_ref": str((raw.get("payload") or {}).get("content_ref") or ""),
        "line_start": retained[0][0],
        "line_end": retained[-1][0],
    },)


def _extract_timestamp(line: str) -> datetime | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2})?)", line)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1))
    except ValueError:
        return None


def _in_window(timestamp: datetime, window: dict[str, Any]) -> bool:
    start = _parse_dt(window.get("start"))
    end = _parse_dt(window.get("end"))
    if start and timestamp < start:
        return False
    if end and timestamp > end:
        return False
    return True


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _time_window(raw: dict[str, Any]) -> dict[str, Any]:
    return dict(((raw.get("metadata") or {}).get("time_window") or {}))


def _log_pattern(line: str) -> str:
    value = re.sub(r"^\d{4}-\d{2}-\d{2}T\S+\s*", "", line)
    value = re.sub(r"\b\d+\b", "<num>", value)
    return _sample(value.strip(), 180)


def _slow_digest(line: str) -> str:
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "?", line.lower())
    return _sample(value, 180)


def _numeric_if_possible(value: str) -> int | float | str:
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _version_family(version: str) -> str:
    lower = version.lower()
    if "mariadb" in lower:
        return "mariadb"
    if version.startswith("8."):
        return "mysql-8.0"
    if version.startswith("5.7"):
        return "mysql-5.7"
    return "unknown"


def _sample(text: str, limit: int) -> str:
    return text[:limit]


def _strip_forbidden(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in FORBIDDEN_OUTPUT_KEYS}
