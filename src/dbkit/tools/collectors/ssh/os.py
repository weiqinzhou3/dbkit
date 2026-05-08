from __future__ import annotations

from pathlib import Path
from typing import Protocol

from dbkit.runtime.collection_guardrails import is_ssh_command_allowed
from dbkit.schemas.evidence import CollectionStep, RawEvidence
from dbkit.schemas.runtime import NormalizedRequest
from dbkit.tools.collectors.common import error_raw_evidence, text_raw_evidence


class SSHClient(Protocol):
    def exec(self, command: str) -> str:
        ...

    def tail(self, path: str, lines: int) -> str:
        ...


class ParamikoSSHClient:
    def __init__(self, request: NormalizedRequest, password: str | None) -> None:
        ssh_target = request.ssh_target or {}
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("paramiko is required for SSH live collection") from exc
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=str(ssh_target.get("host") or ""),
            port=int(ssh_target.get("port") or 22),
            username=str(ssh_target.get("username") or ""),
            password=password,
            timeout=5,
            banner_timeout=5,
            auth_timeout=5,
        )

    def exec(self, command: str) -> str:
        if not is_ssh_command_allowed(command):
            raise RuntimeError(f"blocked unsafe SSH command: {command}")
        _stdin, stdout, stderr = self._client.exec_command(command, timeout=30)
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")
        return output + (("\n" + error) if error else "")

    def tail(self, path: str, lines: int) -> str:
        command = f"tail -n {int(lines)} -- {path}"
        return self.exec(command)


_OS_COMMANDS = {
    "collect_os_cpu_snapshot": ("uptime", "top -b -n 1 | head -50", "vmstat 1 3"),
    "collect_os_memory_snapshot": ("free -m", "vmstat 1 3"),
    "collect_os_disk_snapshot": ("df -h",),
    "collect_os_service_status": (
        "systemctl status mysqld --no-pager",
        "systemctl status mysql --no-pager",
        "ps -ef | grep -E 'mysqld|mysql' | grep -v grep",
    ),
}


def collect_os_snapshot(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    ssh_client: SSHClient,
    started_at: str,
    completed_at: str,
) -> RawEvidence:
    try:
        outputs: list[str] = []
        for command in _OS_COMMANDS[step.tool_name]:
            if not is_ssh_command_allowed(command):
                raise RuntimeError(f"blocked unsafe SSH command: {command}")
            outputs.append(f"$ {command}\n{ssh_client.exec(command)}")
        return text_raw_evidence(
            step=step,
            request=request,
            raw_root=raw_root,
            content="\n\n".join(outputs),
            source={
                "kind": "ssh",
                "path": None,
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
            source_kind="ssh",
        )
