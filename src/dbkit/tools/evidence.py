from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
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
    return _parse_log(raw, raw_text, log_name="Slow log")


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
    parsed = parse_log_for_time_window(
        raw_text,
        _time_window(raw),
        source_timezone=_source_timezone(raw),
    )
    retained_lines = parsed.retained_lines
    patterns: dict[str, dict[str, Any]] = {}
    for event in parsed.retained_events:
        pattern = _log_pattern(event.first_line)
        if not pattern:
            continue
        entry = patterns.setdefault(
            pattern,
            {
                "pattern": pattern,
                "count": 0,
                "raw_refs": [],
            },
        )
        entry["count"] += 1
        entry["raw_refs"].append(_event_ref(raw, event))

    flags = list(parsed.quality_flags)
    coverage = str(raw.get("metadata", {}).get("time_window_coverage") or "unknown")
    if coverage in {"unknown", "partial_or_unknown"}:
        flags.append("time_window_coverage_unknown")
    if not parsed.retained_events and parsed.discarded_events:
        flags.extend(["out_of_time_window", "low_signal"])

    severity_counts = Counter(
        severity
        for severity in (_severity(event.first_line) for event in parsed.retained_events)
        if severity
    )
    top_patterns = sorted(
        patterns.values(),
        key=lambda item: (-int(item["count"]), str(item["pattern"])),
    )[:10]
    for pattern in top_patterns:
        pattern["raw_refs"] = pattern["raw_refs"][:3]
        pattern.update(_semantic_hint(str(pattern["pattern"])))

    if parsed.retained_lines:
        summary = _log_summary(log_name, len(parsed.retained_lines), top_patterns)
    elif parsed.discarded_lines:
        summary = f"{log_name} parsed but no lines matched the requested time window."
    else:
        summary = f"{log_name} parsed with partial timestamp coverage."
    return _item(
        raw,
        summary=summary,
        structured_payload={
            "total_lines": parsed.total_lines,
            "parsed_timestamp_lines": parsed.parsed_timestamp_lines,
            "unparseable_lines": parsed.unparseable_lines,
            "retained_lines": len(parsed.retained_lines),
            "discarded_lines": len(parsed.discarded_lines),
            "retained_events": len(parsed.retained_events),
            "discarded_events": len(parsed.discarded_events),
            "timestamp_parse_status": parsed.timestamp_parse_status,
            "time_window_filter_status": parsed.time_window_filter_status,
            "timezone_handling": parsed.timezone_handling,
            "source_timezone": parsed.source_timezone,
            "collection_time_window_coverage": coverage,
            "severity_counts": dict(severity_counts),
            "top_patterns": top_patterns,
            "sample_events": [
                {
                    "line_start": event.line_start,
                    "line_end": event.line_end,
                    "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                    "severity": _severity(event.first_line),
                    "sample": _sample("\n".join(event.lines), 300),
                }
                for event in parsed.retained_events[:5]
            ],
        },
        raw_text=raw_text,
        raw_refs=_line_refs(raw, retained_lines) or _fallback_log_ref(raw, parsed.total_lines),
        timestamp_parse_status=parsed.timestamp_parse_status,
        timezone_handling=parsed.timezone_handling,
        source_timezone=parsed.source_timezone,
        quality_flags=tuple(dict.fromkeys(flags)),
    )


@dataclass(frozen=True)
class LogEvent:
    line_start: int
    line_end: int
    lines: tuple[str, ...]
    timestamp: datetime | None
    timestamp_line_parsed: bool
    timestamp_had_timezone: bool = False

    @property
    def first_line(self) -> str:
        return self.lines[0] if self.lines else ""


@dataclass(frozen=True)
class ParsedLogWindow:
    total_lines: int
    parsed_timestamp_lines: int
    unparseable_lines: int
    retained_events: tuple[LogEvent, ...]
    discarded_events: tuple[LogEvent, ...]
    retained_lines: tuple[tuple[int, str], ...]
    discarded_lines: tuple[tuple[int, str], ...]
    timestamp_parse_status: str
    time_window_filter_status: str
    timezone_handling: str
    source_timezone: str
    quality_flags: tuple[str, ...]


def filter_log_text_to_time_window(
    raw_text: str,
    time_window: dict[str, Any],
    source_timezone: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    parsed = parse_log_for_time_window(
        raw_text,
        time_window,
        source_timezone=source_timezone,
    )
    retained_text = "\n".join(line for _, line in parsed.retained_lines)
    if retained_text:
        retained_text += "\n"
    return retained_text, {
        "total_lines": parsed.total_lines,
        "matched_lines": len(parsed.retained_lines),
        "discarded_lines": len(parsed.discarded_lines),
        "matched_events": len(parsed.retained_events),
        "discarded_events": len(parsed.discarded_events),
        "timestamp_parse_status": parsed.timestamp_parse_status,
        "time_window_filter_status": parsed.time_window_filter_status,
        "timezone_handling": parsed.timezone_handling,
        "source_timezone": parsed.source_timezone,
    }


def parse_log_for_time_window(
    raw_text: str,
    time_window: dict[str, Any],
    source_timezone: dict[str, Any] | None = None,
) -> ParsedLogWindow:
    lines = raw_text.splitlines()
    source_tz = _infer_source_timezone(source_timezone or {})
    events = _split_log_events(lines, source_tz)
    retained_events: list[LogEvent] = []
    discarded_events: list[LogEvent] = []
    retained_lines: list[tuple[int, str]] = []
    discarded_lines: list[tuple[int, str]] = []
    parsed_timestamp_lines = sum(1 for event in events if event.timestamp_line_parsed)
    unparseable_lines = sum(1 for event in events for _line in event.lines) - parsed_timestamp_lines
    start = _parse_dt(time_window.get("start"))
    end = _parse_dt(time_window.get("end"))
    start_utc = _to_utc(start) if start else None
    end_utc = _to_utc(end) if end else None
    window_available = bool(start_utc or end_utc)
    naive_events_without_timezone = [
        event for event in events
        if event.timestamp is not None and not event.timestamp_had_timezone and source_tz is None
    ]

    for event in events:
        event_lines = list(zip(range(event.line_start, event.line_end + 1), event.lines))
        if event.timestamp is None:
            retained_events.append(event)
            retained_lines.extend(event_lines)
            continue
        event_utc = _to_utc(event.timestamp)
        if event_utc is None or (
            event_utc.tzinfo is None
            and ((start_utc and start_utc.tzinfo is not None) or (end_utc and end_utc.tzinfo is not None))
        ):
            retained_events.append(event)
            retained_lines.extend(event_lines)
            continue
        if not window_available or _in_window_utc(event_utc, start_utc, end_utc):
            retained_events.append(event)
            retained_lines.extend(event_lines)
        else:
            discarded_events.append(event)
            discarded_lines.extend(event_lines)

    if parsed_timestamp_lines == 0:
        timestamp_status = "failed"
    elif unparseable_lines > 0:
        timestamp_status = "partial"
    else:
        timestamp_status = "ok"

    if parsed_timestamp_lines == 0:
        filter_status = "unavailable"
    elif unparseable_lines > 0:
        filter_status = "partial"
    else:
        filter_status = "applied"

    flags: list[str] = []
    if timestamp_status == "failed":
        flags.extend(["timestamp_parse_failed", "parser_partial"])
    elif timestamp_status == "partial":
        flags.append("timestamp_parse_partial")
    if filter_status in {"partial", "unavailable"}:
        flags.append(f"time_window_filter_{filter_status}")
    timezone_handling, source_timezone_label = _timezone_status(
        events,
        source_tz,
        naive_events_without_timezone,
    )
    if timezone_handling == "failed":
        flags.append("timezone_inference_failed")

    return ParsedLogWindow(
        total_lines=len(lines),
        parsed_timestamp_lines=parsed_timestamp_lines,
        unparseable_lines=unparseable_lines,
        retained_events=tuple(retained_events),
        discarded_events=tuple(discarded_events),
        retained_lines=tuple(retained_lines),
        discarded_lines=tuple(discarded_lines),
        timestamp_parse_status=timestamp_status,
        time_window_filter_status=filter_status,
        timezone_handling=timezone_handling,
        source_timezone=source_timezone_label,
        quality_flags=tuple(dict.fromkeys(flags)),
    )


def _item(
    raw: dict[str, Any],
    *,
    summary: str,
    structured_payload: dict[str, Any],
    raw_text: str,
    raw_refs: tuple[dict[str, Any], ...] | None = None,
    timestamp_parse_status: str = "not_applicable",
    timezone_handling: str = "not_applicable",
    source_timezone: str = "unknown",
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
    start = _parse_dt(window.get("start"))
    end = _parse_dt(window.get("end"))
    return EvidenceItem(
        evidence_id=evidence_id,
        raw_evidence_id=str(raw["raw_evidence_id"]),
        evidence_type=str(raw["evidence_type"]),
        source=dict(raw.get("source") or {}),
        time_range={
            "start": window.get("start"),
            "end": window.get("end"),
            "start_utc": _format_utc(start),
            "end_utc": _format_utc(end),
            "timestamp_parse_status": timestamp_parse_status,
            "timezone_handling": timezone_handling,
            "source_timezone": source_timezone,
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


def _fallback_log_ref(raw: dict[str, Any], total_lines: int) -> tuple[dict[str, Any], ...]:
    return ({
        "raw_evidence_id": raw["raw_evidence_id"],
        "content_ref": str((raw.get("payload") or {}).get("content_ref") or ""),
        "line_start": 1,
        "line_end": min(max(total_lines, 1), 1),
    },)


def _event_ref(raw: dict[str, Any], event: LogEvent) -> dict[str, Any]:
    return {
        "raw_evidence_id": raw["raw_evidence_id"],
        "content_ref": str((raw.get("payload") or {}).get("content_ref") or ""),
        "line_start": event.line_start,
        "line_end": event.line_end,
    }


def _split_log_events(
    lines: list[str],
    source_tz: tzinfo | None,
) -> tuple[LogEvent, ...]:
    events: list[LogEvent] = []
    current_start = 1
    current_lines: list[str] = []
    current_ts: datetime | None = None
    current_parsed = False
    current_had_timezone = False

    def flush(end_line: int) -> None:
        nonlocal current_lines, current_start, current_ts, current_parsed, current_had_timezone
        if not current_lines:
            return
        events.append(
            LogEvent(
                line_start=current_start,
                line_end=end_line,
                lines=tuple(current_lines),
                timestamp=current_ts,
                timestamp_line_parsed=current_parsed,
                timestamp_had_timezone=current_had_timezone,
            )
        )
        current_lines = []
        current_ts = None
        current_parsed = False
        current_had_timezone = False

    for line_number, line in enumerate(lines, start=1):
        parsed = _extract_timestamp(line, default_tz=source_tz)
        if parsed.timestamp is not None:
            flush(line_number - 1)
            current_start = line_number
            current_lines = [line]
            current_ts = parsed.timestamp
            current_parsed = True
            current_had_timezone = parsed.had_timezone
        else:
            if not current_lines:
                current_start = line_number
            current_lines.append(line)
    flush(len(lines))
    return tuple(events)


@dataclass(frozen=True)
class ParsedTimestamp:
    timestamp: datetime | None
    had_timezone: bool = False


def _extract_timestamp_with_default_tz(
    line: str,
    *,
    default_tz: tzinfo | None = None,
) -> ParsedTimestamp:
    iso_match = re.search(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)",
        line,
    )
    if iso_match:
        value = iso_match.group(1)
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(value)
            had_timezone = parsed.tzinfo is not None
            if not had_timezone and default_tz is not None:
                parsed = parsed.replace(tzinfo=default_tz)
            return ParsedTimestamp(parsed, had_timezone=had_timezone)
        except ValueError:
            return ParsedTimestamp(None)

    space_match = re.search(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)",
        line,
    )
    if space_match:
        value = space_match.group(1)
        fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in value else "%Y-%m-%d %H:%M:%S"
        try:
            parsed = datetime.strptime(value, fmt)
            if default_tz is not None:
                parsed = parsed.replace(tzinfo=default_tz)
            return ParsedTimestamp(parsed, had_timezone=False)
        except ValueError:
            return ParsedTimestamp(None)

    old_match = re.search(r"\b(\d{6})\s+(\d{2}:\d{2}:\d{2})\b", line)
    if old_match:
        value = old_match.group(1) + " " + old_match.group(2)
        try:
            parsed = datetime.strptime(value, "%y%m%d %H:%M:%S")
            if default_tz is not None:
                parsed = parsed.replace(tzinfo=default_tz)
            return ParsedTimestamp(parsed, had_timezone=False)
        except ValueError:
            return ParsedTimestamp(None)

    return ParsedTimestamp(None)


def _extract_timestamp(line: str, *, default_tz: tzinfo | None = None) -> ParsedTimestamp:
    return _extract_timestamp_with_default_tz(line, default_tz=default_tz)


def _in_window_utc(timestamp: datetime, start: datetime | None, end: datetime | None) -> bool:
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


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime | None) -> str | None:
    utc_value = _to_utc(value)
    return utc_value.isoformat() if utc_value is not None else None


def _infer_source_timezone(source_timezone: dict[str, Any]) -> tzinfo | None:
    log_timestamps = str(source_timezone.get("mysql_log_timestamps") or "").upper()
    if log_timestamps == "UTC":
        return timezone.utc
    offset = _parse_timezone_offset(str(source_timezone.get("os_timezone_offset") or ""))
    if offset is not None:
        return offset
    return None


def _timezone_status(
    events: tuple[LogEvent, ...],
    source_tz: tzinfo | None,
    naive_events_without_timezone: list[LogEvent],
) -> tuple[str, str]:
    timestamped = [event for event in events if event.timestamp is not None]
    if not timestamped:
        return "failed", "unknown"
    if naive_events_without_timezone:
        return "failed", "unknown"
    if any(event.timestamp_had_timezone for event in timestamped):
        return "normalized_to_utc", _source_label_from_events(timestamped)
    if source_tz is not None:
        return "inferred", _tz_label(source_tz)
    return "failed", "unknown"


def _source_label_from_events(events: list[LogEvent]) -> str:
    labels = {_tz_label(event.timestamp.tzinfo) for event in events if event.timestamp and event.timestamp.tzinfo}
    if len(labels) == 1:
        return next(iter(labels))
    if labels:
        return "mixed"
    return "unknown"


def _tz_label(value: tzinfo | None) -> str:
    if value is None:
        return "unknown"
    offset = value.utcoffset(None)
    if offset == timedelta(0):
        return "UTC"
    if offset is None:
        return "unknown"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _parse_timezone_offset(value: str) -> timezone | None:
    match = re.fullmatch(r"([+-])(\d{2})(?::?(\d{2}))", value.strip())
    if not match:
        return None
    sign, hours, minutes = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    if sign == "-":
        delta = -delta
    return timezone(delta)


def _time_window(raw: dict[str, Any]) -> dict[str, Any]:
    return dict(((raw.get("metadata") or {}).get("time_window") or {}))


def _source_timezone(raw: dict[str, Any]) -> dict[str, Any]:
    source_timezone = (raw.get("metadata") or {}).get("source_timezone")
    return dict(source_timezone) if isinstance(source_timezone, dict) else {}


def _log_pattern(line: str) -> str:
    value = re.sub(r"^\d{4}-\d{2}-\d{2}T\S+\s*", "", line)
    value = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*", "", value)
    value = re.sub(r"^\d{6}\s+\d{2}:\d{2}:\d{2}\s*", "", value)
    value = re.sub(r"\bthread\s+\d+\b", "thread <num>", value, flags=re.IGNORECASE)
    value = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d+\b", "<ip:port>", value)
    value = re.sub(r"\b\d+\b", "<num>", value)
    return _sample(value.strip(), 180)


def _log_summary(
    log_name: str,
    retained_line_count: int,
    top_patterns: list[dict[str, Any]],
) -> str:
    if top_patterns:
        top = top_patterns[0]
        label = _semantic_summary_label(str(top.get("semantic_hint") or ""))
        count = int(top.get("count") or 0)
        if label and count > 0:
            return (
                f"{log_name} contains {count} {label} events inside "
                "the requested time window."
            )
    return (
        f"{log_name} parsed with {retained_line_count} retained lines "
        "inside the requested time window."
    )


def _semantic_hint(pattern: str) -> dict[str, str]:
    lower = pattern.lower()
    hints = (
        ("aborted connection", "aborted_connection", "high"),
        ("access denied", "access_denied", "high"),
        ("too many connections", "too_many_connections", "high"),
        ("ready for connections", "crash_or_restart", "medium"),
        ("shutdown complete", "crash_or_restart", "medium"),
        ("starting as process", "crash_or_restart", "medium"),
        ("out of memory", "oom_or_memory", "high"),
        ("oom", "oom_or_memory", "high"),
        ("timeout", "timeout", "medium"),
        ("timed out", "timeout", "medium"),
        ("replication", "replication_error", "high"),
        ("innodb", "innodb_error", "high"),
    )
    for needle, hint, relevance in hints:
        if needle in lower:
            return {
                "semantic_hint": hint,
                "operational_relevance": relevance,
            }
    return {}


def _semantic_summary_label(semantic_hint: str) -> str:
    return {
        "aborted_connection": "Aborted connection",
        "access_denied": "Access denied",
        "too_many_connections": "Too many connections",
        "crash_or_restart": "crash or restart",
        "oom_or_memory": "OOM or memory",
        "timeout": "timeout",
        "replication_error": "replication error",
        "innodb_error": "InnoDB error",
    }.get(semantic_hint, "")


def _severity(line: str) -> str | None:
    match = re.search(r"\[(ERROR|Warning|Note|System|MY-\d+)\]", line, re.IGNORECASE)
    if not match:
        return None
    value = match.group(1)
    if value.upper() == "ERROR":
        return "ERROR"
    if value.lower() == "warning":
        return "Warning"
    if value.lower() == "note":
        return "Note"
    return value


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
