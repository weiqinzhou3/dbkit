from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from dbkit.runtime.secret_store import SecretStore
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.schemas.evidence import CollectionStep, RawEvidence
from dbkit.schemas.runtime import NormalizedRequest
from dbkit.tools.collectors.common import error_raw_evidence, text_raw_evidence
from dbkit.tools.collectors.mysql.logs import collect_mysql_log_file, discover_mysql_log_paths
from dbkit.tools.collectors.mysql.service import (
    PyMySQLClient,
    collect_mysql_metrics_snapshot,
    collect_mysql_service,
    collect_mysql_status_metrics,
    collect_mysql_variable_metrics,
)
from dbkit.tools.collectors.ssh.files import read_remote_file
from dbkit.tools.collectors.ssh.os import ParamikoSSHClient, collect_os_snapshot

MySQLClientFactory = Callable[[NormalizedRequest, SecretStore], Any]
SSHClientFactory = Callable[[NormalizedRequest, SecretStore], Any]


class CollectorRegistry:
    def __init__(
        self,
        *,
        workspace_root: Path,
        mysql_client_factory: MySQLClientFactory | None = None,
        ssh_client_factory: SSHClientFactory | None = None,
        secret_store: SecretStore | None = None,
        log_tail_lines: int = 5000,
        log_max_bytes: int = 10_485_760,
        log_time_window_scan_max_bytes: int = 52_428_800,
        log_prefer_time_window_scan: bool = True,
        mysql_connect_timeout_seconds: int = 5,
        mysql_read_timeout_seconds: int = 30,
        mysql_write_timeout_seconds: int = 30,
        telemetry: TelemetryRecorder | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.mysql_client_factory = mysql_client_factory or _default_mysql_client
        self.ssh_client_factory = ssh_client_factory or _default_ssh_client
        self.secret_store = secret_store or SecretStore()
        self.log_tail_lines = log_tail_lines
        self.log_max_bytes = log_max_bytes
        self.log_time_window_scan_max_bytes = log_time_window_scan_max_bytes
        self.log_prefer_time_window_scan = log_prefer_time_window_scan
        self.mysql_connect_timeout_seconds = mysql_connect_timeout_seconds
        self.mysql_read_timeout_seconds = mysql_read_timeout_seconds
        self.mysql_write_timeout_seconds = mysql_write_timeout_seconds
        self.telemetry = telemetry
        self._mysql_client: Any | None = None
        self._ssh_client: Any | None = None
        self._ssh_client_error: RuntimeError | None = None

    def collect(
        self,
        *,
        step: CollectionStep,
        request: NormalizedRequest,
        raw_root: Path,
        started_at: str,
        completed_at: str,
    ) -> tuple[RawEvidence, ...]:
        try:
            return self._collect(
                step=step,
                request=request,
                raw_root=raw_root,
                started_at=started_at,
                completed_at=completed_at,
            )
        except Exception as exc:
            return (
                error_raw_evidence(
                    step=step,
                    request=request,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    error=str(exc),
                ),
            )

    def _collect(
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
        if step.tool_name in {
            "collect_mysql_processlist",
            "collect_processlist",
            "collect_mysql_runtime_status",
            "collect_mysql_innodb_status",
            "collect_innodb_status",
            "collect_mysql_variables",
            "collect_mysql_service_metadata",
        }:
            return (
                collect_mysql_service(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    mysql_client=self._mysql(request),
                    started_at=started_at,
                    completed_at=completed_at,
                ),
            )
        if step.tool_name == "discover_mysql_log_paths":
            return (
                discover_mysql_log_paths(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    mysql_client=self._mysql(request),
                    started_at=started_at,
                    completed_at=completed_at,
                ),
            )
        if step.tool_name in {"collect_mysql_error_log", "collect_mysql_slow_log"}:
            return (
                collect_mysql_log_file(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    mysql_client=self._mysql(request),
                    ssh_client=None,
                    ssh_client_factory=lambda: self._ssh(request) if request.ssh_target else None,
                    started_at=started_at,
                    completed_at=completed_at,
                    tail_lines=self.log_tail_lines,
                    max_bytes=self.log_max_bytes,
                    time_window_scan_max_bytes=self.log_time_window_scan_max_bytes,
                    prefer_time_window_scan=self.log_prefer_time_window_scan,
                ),
            )
        if step.tool_name in {"collect_mysql_metrics_snapshot", "collect_metrics_snapshot"}:
            return (
                collect_mysql_metrics_snapshot(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    mysql_client=self._mysql(request),
                    started_at=started_at,
                    completed_at=completed_at,
                ),
            )
        if step.tool_name == "collect_mysql_status_metrics":
            return (
                collect_mysql_status_metrics(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    mysql_client=self._mysql(request),
                    started_at=started_at,
                    completed_at=completed_at,
                ),
            )
        if step.tool_name == "collect_mysql_variable_metrics":
            return (
                collect_mysql_variable_metrics(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    mysql_client=self._mysql(request),
                    started_at=started_at,
                    completed_at=completed_at,
                ),
            )
        if step.tool_name in {
            "collect_os_service_status",
            "collect_os_cpu_snapshot",
            "collect_os_memory_snapshot",
            "collect_os_disk_snapshot",
        }:
            return (
                collect_os_snapshot(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    ssh_client=self._ssh(request),
                    started_at=started_at,
                    completed_at=completed_at,
                ),
            )
        if step.tool_name == "read_remote_file":
            return (
                read_remote_file(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    ssh_client=self._ssh(request),
                    started_at=started_at,
                    completed_at=completed_at,
                    tail_lines=self.log_tail_lines,
                    max_bytes=self.log_max_bytes,
                    time_window_scan_max_bytes=self.log_time_window_scan_max_bytes,
                    prefer_time_window_scan=self.log_prefer_time_window_scan,
                ),
            )
        return (
            error_raw_evidence(
                step=step,
                request=request,
                status="not_implemented",
                started_at=started_at,
                completed_at=completed_at,
                error=f"collector not implemented: {step.tool_name}",
                reason="collector_not_implemented",
            ),
        )

    def _mysql(self, request: NormalizedRequest) -> Any:
        if self._mysql_client is None:
            if self.mysql_client_factory is _default_mysql_client:
                self._mysql_client = _default_mysql_client(
                    request,
                    self.secret_store,
                    telemetry=self.telemetry,
                    connect_timeout_seconds=self.mysql_connect_timeout_seconds,
                    read_timeout_seconds=self.mysql_read_timeout_seconds,
                    write_timeout_seconds=self.mysql_write_timeout_seconds,
                )
            else:
                self._mysql_client = self.mysql_client_factory(request, self.secret_store)
        return self._mysql_client

    def _ssh(self, request: NormalizedRequest) -> Any:
        if self._ssh_client_error is not None:
            raise self._ssh_client_error
        if self._ssh_client is None:
            try:
                self._ssh_client = self.ssh_client_factory(request, self.secret_store)
            except Exception as exc:
                message = str(exc).strip() or exc.__class__.__name__
                if not message.startswith("SSH connection failed:"):
                    message = f"SSH connection failed: {message}"
                self._ssh_client_error = RuntimeError(message)
                raise self._ssh_client_error from None
        return self._ssh_client

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
                error_raw_evidence(
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
            raw_items.append(
                self._read_file_path(
                    step=step,
                    request=request,
                    raw_root=raw_root,
                    virtual_path=self._host_to_virtual_path(path),
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
            return error_raw_evidence(
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
        content = host_path.read_text(encoding="utf-8", errors="replace")
        return text_raw_evidence(
            step=step,
            request=request,
            raw_root=raw_root,
            content=content,
            source={
                "kind": "file",
                "path": virtual_path,
                "host": None,
                "tool_name": step.tool_name,
            },
            started_at=started_at,
            completed_at=completed_at,
        )

    def _virtual_to_host_path(self, virtual_path: str) -> Path:
        if not virtual_path.startswith("/workspace/"):
            raise ValueError(f"provided evidence path must be under /workspace/: {virtual_path}")
        relative = virtual_path.removeprefix("/workspace/").strip("/")
        return self.workspace_root / relative

    def _host_to_virtual_path(self, host_path: Path) -> str:
        relative = host_path.relative_to(self.workspace_root)
        return "/workspace/" + relative.as_posix()

    def close(self) -> None:
        mysql_client = self._mysql_client
        close = getattr(mysql_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                if self.telemetry is not None:
                    self.telemetry.emit(
                        event_type="mysql_connection_close_failed",
                        stage="mysql_connection",
                        message="MySQL connection close failed",
                        attributes={"error": type(exc).__name__},
                    )


def _default_mysql_client(
    request: NormalizedRequest,
    secret_store: SecretStore,
    *,
    telemetry: TelemetryRecorder | None = None,
    connect_timeout_seconds: int = 5,
    read_timeout_seconds: int = 30,
    write_timeout_seconds: int = 30,
) -> PyMySQLClient:
    target = request.target or {}
    return PyMySQLClient(
        request,
        secret_store.get(str(target.get("password_ref") or "")),
        telemetry=telemetry,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        write_timeout_seconds=write_timeout_seconds,
    )


def _default_ssh_client(request: NormalizedRequest, secret_store: SecretStore) -> ParamikoSSHClient:
    ssh_target = request.ssh_target or {}
    return ParamikoSSHClient(request, secret_store.get(str(ssh_target.get("password_ref") or "")))
