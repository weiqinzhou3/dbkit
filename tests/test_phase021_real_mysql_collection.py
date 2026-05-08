import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.collection_guardrails import (
    is_mysql_sql_allowed,
    is_ssh_command_allowed,
)
from dbkit.runtime.evidence_pipeline import EvidencePipeline
from dbkit.runtime.secret_store import SecretStore
from dbkit.runtime.time_context import FixedTimeProvider
from dbkit.schemas.evidence import CollectionStep
from dbkit.tools.collectors import CollectorRegistry
from dbkit.tools.normalize_request import normalize_request


class Phase021RealMySQLCollectionTest(unittest.TestCase):
    def test_mysql_service_collectors_execute_read_only_sql(self) -> None:
        fake_mysql = FakeMySQLClient(
            {
                "SHOW FULL PROCESSLIST": [
                    {"Id": 1, "User": "app", "Host": "10.0.0.1", "Info": "select 1"}
                ],
                "SHOW GLOBAL STATUS": [{"Variable_name": "Threads_running", "Value": "3"}],
                "SHOW ENGINE INNODB STATUS": [
                    {"Type": "InnoDB", "Name": "", "Status": "LATEST DETECTED DEADLOCK"}
                ],
                "SHOW GLOBAL VARIABLES": [{"Variable_name": "max_connections", "Value": "151"}],
            }
        )
        request = _live_request()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = CollectorRegistry(
                workspace_root=root / "workspace",
                mysql_client_factory=lambda _request, _secrets: fake_mysql,
                secret_store=SecretStore({"<SECRET_REF:mysql_password_001>": "Root"}),
            )

            expectations = [
                ("collect_mysql_processlist", "mysql.processlist", "SHOW FULL PROCESSLIST"),
                ("collect_mysql_runtime_status", "mysql.runtime_status", "SHOW GLOBAL STATUS"),
                ("collect_mysql_innodb_status", "mysql.innodb_status", "SHOW ENGINE INNODB STATUS"),
                ("collect_mysql_variables", "mysql.variables", "SHOW GLOBAL VARIABLES"),
            ]
            for index, (tool_name, evidence_type, sql) in enumerate(expectations, start=1):
                item = registry.collect(
                    step=_step(index, tool_name, evidence_type),
                    request=request,
                    raw_root=root / "raw",
                    started_at=_now(),
                    completed_at=_now(),
                )[0]
                self.assertEqual(item.collection["status"], "collected")
                self.assertEqual(item.source["kind"], "mysql")
                self.assertEqual(item.source["tool_name"], tool_name)
                self.assertEqual(item.evidence_type, evidence_type)
                payload_text = Path(item.payload["content_ref"]).read_text(encoding="utf-8")
                self.assertIn(sql, payload_text)

        self.assertEqual(fake_mysql.queries, [item[2] for item in expectations])

    def test_log_path_discovery_resolves_absolute_and_relative_paths(self) -> None:
        fake_mysql = FakeMySQLClient(
            {
                "SHOW GLOBAL VARIABLES LIKE 'log_error'": [
                    {"Variable_name": "log_error", "Value": "mysqld.err"}
                ],
                "SHOW GLOBAL VARIABLES LIKE 'slow_query_log_file'": [
                    {"Variable_name": "slow_query_log_file", "Value": "/var/log/mysql/slow.log"}
                ],
                "SHOW GLOBAL VARIABLES LIKE 'slow_query_log'": [
                    {"Variable_name": "slow_query_log", "Value": "ON"}
                ],
                "SHOW GLOBAL VARIABLES LIKE 'log_output'": [
                    {"Variable_name": "log_output", "Value": "FILE"}
                ],
                "SHOW GLOBAL VARIABLES LIKE 'datadir'": [
                    {"Variable_name": "datadir", "Value": "/var/lib/mysql/"}
                ],
            }
        )
        request = _live_request()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = CollectorRegistry(
                workspace_root=root / "workspace",
                mysql_client_factory=lambda _request, _secrets: fake_mysql,
                secret_store=SecretStore({"<SECRET_REF:mysql_password_001>": "Root"}),
            )
            item = registry.collect(
                step=_step(1, "discover_mysql_log_paths", "mysql.log_paths"),
                request=request,
                raw_root=root / "raw",
                started_at=_now(),
                completed_at=_now(),
            )[0]

        self.assertEqual(item.collection["status"], "collected")
        self.assertEqual(item.payload["data"]["error_log_path"], "/var/lib/mysql/mysqld.err")
        self.assertEqual(item.payload["data"]["slow_log_path"], "/var/log/mysql/slow.log")
        self.assertTrue(item.payload["data"]["slow_query_log_enabled"])

    def test_error_log_collection_reads_discovered_path_through_ssh(self) -> None:
        fake_mysql = FakeMySQLClient(_log_variables(error_log="/var/log/mysql/error.log"))
        fake_ssh = FakeSSHClient({("tail", 5000, "/var/log/mysql/error.log"): "error line\n"})
        request = _live_request(with_ssh=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = CollectorRegistry(
                workspace_root=root / "workspace",
                mysql_client_factory=lambda _request, _secrets: fake_mysql,
                ssh_client_factory=lambda _request, _secrets: fake_ssh,
                secret_store=SecretStore(
                    {
                        "<SECRET_REF:mysql_password_001>": "Root",
                        "<SECRET_REF:ssh_password_001>": "Root",
                    }
                ),
            )
            item = registry.collect(
                step=_step(1, "collect_mysql_error_log", "mysql.error_log"),
                request=request,
                raw_root=root / "raw",
                started_at=_now(),
                completed_at=_now(),
            )[0]

            self.assertEqual(item.collection["status"], "collected")
            self.assertEqual(item.source["kind"], "ssh_file")
            self.assertEqual(item.payload["line_count"], 1)
            self.assertIn("error line", Path(item.payload["content_ref"]).read_text(encoding="utf-8"))

    def test_slow_log_disabled_returns_not_available(self) -> None:
        fake_mysql = FakeMySQLClient(_log_variables(slow_enabled=False))
        request = _live_request(with_ssh=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = CollectorRegistry(
                workspace_root=root / "workspace",
                mysql_client_factory=lambda _request, _secrets: fake_mysql,
                ssh_client_factory=lambda _request, _secrets: FakeSSHClient({}),
                secret_store=SecretStore({"<SECRET_REF:mysql_password_001>": "Root"}),
            )
            item = registry.collect(
                step=_step(1, "collect_mysql_slow_log", "mysql.slow_log"),
                request=request,
                raw_root=root / "raw",
                started_at=_now(),
                completed_at=_now(),
            )[0]

        self.assertEqual(item.collection["status"], "not_available")
        self.assertEqual(item.collection["reason"], "slow_query_log_disabled")

    def test_os_cpu_snapshot_uses_allowlisted_commands(self) -> None:
        fake_ssh = FakeSSHClient(
            {
                ("exec", "uptime"): "load average: 0.10\n",
                ("exec", "top -b -n 1 | head -50"): "top output\n",
                ("exec", "vmstat 1 3"): "vmstat output\n",
            }
        )
        request = _live_request(with_ssh=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = CollectorRegistry(
                workspace_root=root / "workspace",
                ssh_client_factory=lambda _request, _secrets: fake_ssh,
                secret_store=SecretStore({"<SECRET_REF:ssh_password_001>": "Root"}),
            )
            item = registry.collect(
                step=_step(1, "collect_os_cpu_snapshot", "metrics.cpu"),
                request=request,
                raw_root=root / "raw",
                started_at=_now(),
                completed_at=_now(),
            )[0]

            self.assertEqual(item.collection["status"], "collected")
            self.assertEqual(fake_ssh.commands, ["uptime", "top -b -n 1 | head -50", "vmstat 1 3"])
            self.assertIn("vmstat output", Path(item.payload["content_ref"]).read_text(encoding="utf-8"))

    def test_guardrails_block_dangerous_sql_and_ssh_commands(self) -> None:
        self.assertTrue(is_mysql_sql_allowed("SHOW GLOBAL STATUS"))
        self.assertFalse(is_mysql_sql_allowed("DROP TABLE users"))
        self.assertFalse(is_mysql_sql_allowed("SET GLOBAL read_only=0"))
        self.assertTrue(is_ssh_command_allowed("tail -n 5000 -- /var/log/mysql/error.log"))
        self.assertFalse(is_ssh_command_allowed("rm -rf /var/log/mysql/error.log"))
        self.assertFalse(is_ssh_command_allowed("systemctl restart mysqld"))

    def test_raw_evidence_index_includes_summary_and_pipeline_status_warnings(self) -> None:
        request = _live_request()
        evidence_request_json = _evidence_request(
            request,
            [
                ("collect_mysql_runtime_status", "mysql.runtime_status"),
                ("collect_mysql_slow_log", "mysql.slow_log"),
            ],
        )
        fake_mysql = FakeMySQLClient(
            {
                "SHOW GLOBAL STATUS": [{"Variable_name": "Threads_running", "Value": "3"}],
                **_log_variables(slow_enabled=False),
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=_telemetry(),
                collectors=CollectorRegistry(
                    workspace_root=root / "workspace",
                    mysql_client_factory=lambda _request, _secrets: fake_mysql,
                    secret_store=SecretStore({"<SECRET_REF:mysql_password_001>": "Root"}),
                ),
                time_provider=_time_provider(),
            ).run(request, evidence_request_json=evidence_request_json)

            index = [a for a in result.artifacts if a.kind == "RawEvidenceIndex"][0]
            payload = json.loads(index.path.read_text(encoding="utf-8"))

        self.assertEqual(result.phase, "phase-02.1")
        self.assertEqual(result.status, "collection_completed_with_warnings")
        self.assertEqual(payload["collected_count"], 1)
        self.assertEqual(payload["not_available_count"], 1)
        self.assertEqual(payload["not_implemented_count"], 0)

    def test_collection_failed_when_all_collectors_fail(self) -> None:
        request = _live_request()
        evidence_request_json = _evidence_request(
            request,
            [("collect_mysql_runtime_status", "mysql.runtime_status")],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=_telemetry(),
                collectors=CollectorRegistry(
                    workspace_root=root / "workspace",
                    mysql_client_factory=lambda _request, _secrets: FailingMySQLClient(),
                    secret_store=SecretStore({"<SECRET_REF:mysql_password_001>": "Root"}),
                ),
                time_provider=_time_provider(),
            ).run(request, evidence_request_json=evidence_request_json)

        self.assertEqual(result.status, "collection_failed")
        self.assertEqual(result.raw_evidence[0].collection["status"], "failed")


class FakeMySQLClient:
    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results
        self.queries: list[str] = []

    def execute(self, sql: str) -> list[dict]:
        self.queries.append(sql)
        return self.results.get(sql, [])


class FailingMySQLClient:
    def execute(self, sql: str) -> list[dict]:
        raise RuntimeError("connection refused")


class FakeSSHClient:
    def __init__(self, results: dict[tuple, str]) -> None:
        self.results = results
        self.commands: list[str] = []

    def exec(self, command: str) -> str:
        self.commands.append(command)
        return self.results.get(("exec", command), "")

    def tail(self, path: str, lines: int) -> str:
        return self.results.get(("tail", lines, path), "")


def _live_request(*, with_ssh: bool = False):
    return normalize_request(
        "连接 MySQL 分析 CPU 告警",
        llm_json={
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.91,
            "input_mode": "live_collection",
            "target": {
                "type": "mysql",
                "host": "192.168.1.10",
                "port": 3306,
                "username": "root",
                "password_ref": "<SECRET_REF:mysql_password_001>",
            },
            "ssh_target": {
                "host": "192.168.1.10",
                "port": 22,
                "username": "root",
                "password_ref": "<SECRET_REF:ssh_password_001>",
            }
            if with_ssh
            else None,
            "collection_policy": {
                "allow_live_collection": True,
                "allow_mysql_login": True,
                "allow_ssh": with_ssh,
                "allow_metrics_query": False,
            },
            "event": {"event_time": "2026-05-08T17:00:00+08:00"},
            "missing_fields": [],
        },
        phase="phase-02.1",
    )


def _step(index: int, tool_name: str, evidence_type: str) -> CollectionStep:
    return CollectionStep(
        step_id=f"step_{index:03d}",
        evidence_type=evidence_type,
        tool_name=tool_name,
        target_ref="ssh_target" if tool_name.startswith("collect_os_") else "target",
        requires_secret_refs=("<SECRET_REF:mysql_password_001>",),
        requires_approval=False,
        timeout_seconds=30,
        purpose="collect evidence",
    )


def _evidence_request(request, tools: list[tuple[str, str]]) -> dict:
    return {
        "request_id": request.request_id,
        "phase": "phase-02",
        "target_agent": "mysql_analyzer",
        "target_domain": "mysql",
        "task_type": "alert_analysis",
        "input_mode": request.input_mode,
        "reasoning_mode": "evidence_planning",
        "evidence_request": {
            "goal": "collect real MySQL evidence",
            "required_evidence": [
                {
                    "evidence_type": evidence_type,
                    "priority": "required",
                    "purpose": "collect evidence",
                    "source": "mysql",
                    "tool_hint": tool_name,
                }
                for tool_name, evidence_type in tools
            ],
            "optional_evidence": [],
            "not_required_evidence": [],
            "missing_inputs": [],
            "approval_requirements": [],
        },
        "metadata": {"mode": "evidence_planning"},
    }


def _log_variables(
    *,
    error_log: str = "/var/log/mysql/error.log",
    slow_log: str = "/var/log/mysql/slow.log",
    slow_enabled: bool = True,
) -> dict[str, list[dict]]:
    return {
        "SHOW GLOBAL VARIABLES LIKE 'log_error'": [
            {"Variable_name": "log_error", "Value": error_log}
        ],
        "SHOW GLOBAL VARIABLES LIKE 'slow_query_log_file'": [
            {"Variable_name": "slow_query_log_file", "Value": slow_log}
        ],
        "SHOW GLOBAL VARIABLES LIKE 'slow_query_log'": [
            {"Variable_name": "slow_query_log", "Value": "ON" if slow_enabled else "OFF"}
        ],
        "SHOW GLOBAL VARIABLES LIKE 'log_output'": [
            {"Variable_name": "log_output", "Value": "FILE"}
        ],
        "SHOW GLOBAL VARIABLES LIKE 'datadir'": [
            {"Variable_name": "datadir", "Value": "/var/lib/mysql"}
        ],
    }


def _now() -> str:
    return "2026-05-08T10:00:00+08:00"


def _time_provider() -> FixedTimeProvider:
    return FixedTimeProvider(
        current_datetime=datetime.fromisoformat(_now()),
        timezone="Asia/Shanghai",
        locale="zh-CN",
    )


def _telemetry():
    from dbkit.runtime.observability import TelemetryRecorder

    return TelemetryRecorder()


if __name__ == "__main__":
    unittest.main()
