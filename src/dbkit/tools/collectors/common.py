from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from dbkit.schemas.evidence import CollectionStep, RawEvidence, stable_id
from dbkit.schemas.runtime import NormalizedRequest


def text_raw_evidence(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    content: str,
    source: dict[str, Any],
    started_at: str,
    completed_at: str,
    status: str = "collected",
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> RawEvidence:
    started = perf_counter()
    raw_root.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    raw_id = stable_id("rawev", request.request_id, step.step_id, source.get("path"), content[:128])
    suffix = ".json" if content.lstrip().startswith(("{", "[")) else ".txt"
    content_path = raw_root / f"{raw_id}{suffix}"
    content_path.write_bytes(encoded)
    collection: dict[str, Any] = {
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": int((perf_counter() - started) * 1000),
        "errors": [],
    }
    if reason:
        collection["reason"] = reason
    payload: dict[str, Any] = {
        "content_ref": str(content_path),
        "bytes": len(encoded),
        "line_count": len(content.splitlines()),
    }
    if data is not None:
        payload["data"] = data
    return RawEvidence(
        raw_evidence_id=raw_id,
        request_id=request.request_id,
        evidence_type=step.evidence_type,
        source=source,
        collection=collection,
        payload=payload,
        metadata={"time_window": time_window_metadata(request)},
    )


def json_raw_evidence(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    data: dict[str, Any],
    source: dict[str, Any],
    started_at: str,
    completed_at: str,
    status: str = "collected",
    reason: str | None = None,
) -> RawEvidence:
    return text_raw_evidence(
        step=step,
        request=request,
        raw_root=raw_root,
        content=json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        source=source,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        reason=reason,
        data=data,
    )


def error_raw_evidence(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    status: str,
    started_at: str,
    completed_at: str,
    error: str,
    source_kind: str = "tool",
    reason: str | None = None,
) -> RawEvidence:
    raw_id = stable_id("rawev", request.request_id, step.step_id, status, error)
    collection: dict[str, Any] = {
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": 0,
        "errors": [error] if status in {"failed", "blocked"} else [],
    }
    if reason:
        collection["reason"] = reason
    return RawEvidence(
        raw_evidence_id=raw_id,
        request_id=request.request_id,
        evidence_type=step.evidence_type,
        source={
            "kind": source_kind,
            "path": step.source_path,
            "host": (request.target or {}).get("host"),
            "tool_name": step.tool_name,
        },
        collection=collection,
        payload={"content_ref": None, "bytes": 0, "line_count": 0},
        metadata={"time_window": time_window_metadata(request)},
    )


def time_window_metadata(request: NormalizedRequest) -> dict[str, Any]:
    if not request.event:
        return {}
    time_window = request.event.get("time_window") or {}
    return {
        key: time_window[key]
        for key in ("start", "end", "before", "after", "source")
        if key in time_window
    }
