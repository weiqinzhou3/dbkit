from __future__ import annotations

from pathlib import Path
from time import perf_counter

from dbkit.schemas.evidence import CollectionStep, RawEvidence, stable_id
from dbkit.schemas.runtime import NormalizedRequest


class CollectorRegistry:
    def __init__(self, *, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def collect(
        self,
        *,
        step: CollectionStep,
        request: NormalizedRequest,
        raw_root: Path,
        started_at: str,
        completed_at: str,
    ) -> tuple[RawEvidence, ...]:
        if step.tool_name == "read_provided_evidence_file":
            return (
                self._read_file_step(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    started_at=started_at,
                    completed_at=completed_at,
                ),
            )
        if step.tool_name == "read_provided_evidence_directory":
            return self._read_directory_step(
                step=step,
                request=request,
                raw_root=raw_root,
                started_at=started_at,
                completed_at=completed_at,
            )
        return (
            self._not_implemented_step(
                step=step,
                request=request,
                started_at=started_at,
                completed_at=completed_at,
            ),
        )

    def _read_directory_step(
        self,
        *,
        step: CollectionStep,
        request: NormalizedRequest,
        raw_root: Path,
        started_at: str,
        completed_at: str,
    ) -> tuple[RawEvidence, ...]:
        directory = self._virtual_to_host_path(step.source_path or "")
        if not directory.is_dir():
            return (
                _error_raw_evidence(
                    step=step,
                    request=request,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    error=f"provided evidence directory not found: {step.source_path}",
                ),
            )

        raw_items: list[RawEvidence] = []
        for path in sorted(p for p in directory.iterdir() if p.is_file()):
            virtual_path = self._host_to_virtual_path(path)
            raw_items.append(
                self._read_file_path(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    virtual_path=virtual_path,
                    host_path=path,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )
        return tuple(raw_items)

    def _read_file_step(
        self,
        *,
        step: CollectionStep,
        request: NormalizedRequest,
        raw_root: Path,
        started_at: str,
        completed_at: str,
    ) -> RawEvidence:
        host_path = self._virtual_to_host_path(step.source_path or "")
        if not host_path.is_file():
            return _error_raw_evidence(
                step=step,
                request=request,
                status="failed",
                started_at=started_at,
                completed_at=completed_at,
                error=f"provided evidence file not found: {step.source_path}",
            )
        return self._read_file_path(
            step=step,
            request=request,
            raw_root=raw_root,
            virtual_path=step.source_path or "",
            host_path=host_path,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _read_file_path(
        self,
        *,
        step: CollectionStep,
        request: NormalizedRequest,
        raw_root: Path,
        virtual_path: str,
        host_path: Path,
        started_at: str,
        completed_at: str,
    ) -> RawEvidence:
        started = perf_counter()
        raw_root.mkdir(parents=True, exist_ok=True)
        content = host_path.read_bytes()
        raw_id = stable_id("rawev", request.request_id, step.step_id, virtual_path)
        content_path = raw_root / f"{raw_id}.txt"
        content_path.write_bytes(content)
        try:
            text = content.decode("utf-8")
            line_count = len(text.splitlines())
        except UnicodeDecodeError:
            line_count = 0
        return RawEvidence(
            raw_evidence_id=raw_id,
            request_id=request.request_id,
            evidence_type=step.evidence_type,
            source={
                "kind": "file",
                "path": virtual_path,
                "host": None,
                "tool_name": step.tool_name,
            },
            collection={
                "status": "collected",
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_ms": int((perf_counter() - started) * 1000),
                "errors": [],
            },
            payload={
                "content_ref": str(content_path),
                "bytes": len(content),
                "line_count": line_count,
            },
            metadata={"time_window": _time_window_metadata(request)},
        )

    def _not_implemented_step(
        self,
        *,
        step: CollectionStep,
        request: NormalizedRequest,
        started_at: str,
        completed_at: str,
    ) -> RawEvidence:
        return _error_raw_evidence(
            step=step,
            request=request,
            status="not_implemented",
            started_at=started_at,
            completed_at=completed_at,
            error=f"collector not implemented: {step.tool_name}",
        )

    def _virtual_to_host_path(self, virtual_path: str) -> Path:
        if not virtual_path.startswith("/workspace/"):
            raise ValueError(f"provided evidence path must be under /workspace/: {virtual_path}")
        relative = virtual_path.removeprefix("/workspace/").strip("/")
        return self.workspace_root / relative

    def _host_to_virtual_path(self, host_path: Path) -> str:
        relative = host_path.relative_to(self.workspace_root)
        return "/workspace/" + relative.as_posix()


def _error_raw_evidence(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    status: str,
    started_at: str,
    completed_at: str,
    error: str,
) -> RawEvidence:
    raw_id = stable_id("rawev", request.request_id, step.step_id, status, error)
    return RawEvidence(
        raw_evidence_id=raw_id,
        request_id=request.request_id,
        evidence_type=step.evidence_type,
        source={
            "kind": "tool",
            "path": step.source_path,
            "host": (request.target or {}).get("host"),
            "tool_name": step.tool_name,
        },
        collection={
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": 0,
            "errors": [error],
        },
        payload={"content_ref": None, "bytes": 0, "line_count": 0},
        metadata={"time_window": _time_window_metadata(request)},
    )


def _time_window_metadata(request: NormalizedRequest) -> dict:
    if not request.event:
        return {}
    time_window = request.event.get("time_window") or {}
    return {
        key: time_window[key]
        for key in ("start", "end", "before", "after", "source")
        if key in time_window
    }
