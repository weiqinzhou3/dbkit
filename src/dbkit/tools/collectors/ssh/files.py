from __future__ import annotations

from pathlib import Path
from typing import Any

from dbkit.runtime.collection_guardrails import is_ssh_command_allowed
from dbkit.schemas.evidence import CollectionStep, RawEvidence
from dbkit.schemas.runtime import NormalizedRequest
from dbkit.tools.collectors.common import error_raw_evidence, text_raw_evidence


def read_remote_file(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    ssh_client: Any,
    started_at: str,
    completed_at: str,
    tail_lines: int = 5000,
) -> RawEvidence:
    path = step.source_path or ""
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
