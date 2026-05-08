import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.collection_guardrails import CollectionGuardrails
from dbkit.runtime.deepagents_runtime import DeepAgentsRuntimeFactory
from dbkit.runtime.evidence_pipeline import EvidencePipeline
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.time_context import FixedTimeProvider
from dbkit.schemas.evidence import validate_evidence_request
from dbkit.tools.collectors import CollectorRegistry
from dbkit.tools.normalize_request import normalize_request


class Phase02EvidencePlanningCollectionTest(unittest.TestCase):
    def test_json_extractor_supports_pure_fenced_prose_and_block_content(self) -> None:
        from dbkit.runtime.json_extraction import extract_json_from_invoke_result

        normalized = _provided_evidence_request(files=["/workspace/mysql-error.log"])
        payload = _evidence_request_payload(normalized)
        cases = [
            {"messages": [{"role": "assistant", "content": json.dumps(payload)}]},
            {
                "messages": [
                    {"role": "assistant", "content": "```json\n" + json.dumps(payload) + "\n```"}
                ]
            },
            {
                "messages": [
                    {"role": "assistant", "content": "Here is the JSON:\n" + json.dumps(payload)}
                ]
            },
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "prefix"},
                            {"type": "text", "text": json.dumps(payload)},
                        ],
                    }
                ]
            },
            {"messages": [SimpleNamespace(type="ai", content=json.dumps(payload))]},
        ]

        try:
            from langchain_core.messages import AIMessage
        except ImportError:
            AIMessage = None
        if AIMessage is not None:
            cases.append({"messages": [AIMessage(content=json.dumps(payload))]})

        for case in cases:
            parsed = extract_json_from_invoke_result(case)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["phase"], "phase-02")

    def test_pipeline_parses_fenced_evidence_request_from_analyzer(self) -> None:
        normalized = _provided_evidence_request(files=["/workspace/mysql-error.log"])
        payload = _evidence_request_payload(normalized)

        class FakeAnalyzerRuntime:
            def invoke(self, payload_in):
                return {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```",
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "mysql-error.log").write_text("error\n", encoding="utf-8")
            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=TelemetryRecorder(),
                collectors=CollectorRegistry(workspace_root=workspace),
                time_provider=_fixed_time_provider(),
                mysql_analyzer_runtime=FakeAnalyzerRuntime(),
            ).run(normalized)

            self.assertEqual(result.status, "raw_evidence_collected")
            self.assertEqual(len(result.raw_evidence), 1)

    def test_evidence_request_parse_failure_returns_blocked_artifact(self) -> None:
        normalized = _provided_evidence_request(files=["/workspace/mysql-error.log"])

        class FakeAnalyzerRuntime:
            def invoke(self, payload):
                return {"messages": [{"role": "assistant", "content": "not json"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=TelemetryRecorder(),
                collectors=CollectorRegistry(workspace_root=root / "workspace"),
                time_provider=_fixed_time_provider(),
                mysql_analyzer_runtime=FakeAnalyzerRuntime(),
            ).run(normalized)

            self.assertEqual(result.status, "evidence_request_parse_failed")
            self.assertEqual(result.blocking_issues, ("evidence_request_parse_failed",))
            artifact = [item for item in result.artifacts if item.kind == "EvidenceRequestFailed"][0]
            payload = json.loads(artifact.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["reason"], "evidence_request_parse_failed")
            self.assertIn(
                "evidence_request_parse_failed",
                [event.event_type for event in result.telemetry],
            )

    def test_evidence_type_alias_and_source_are_normalized(self) -> None:
        normalized = _provided_evidence_request(files=["/workspace/mysql-error.log"])
        payload = _evidence_request_payload(
            normalized,
            tool_hint="collect_processlist",
            evidence_type="mysql_processlist",
            source="live_collection",
        )

        evidence_request = validate_evidence_request(payload)
        item = evidence_request.evidence_request["required_evidence"][0]

        self.assertEqual(item["evidence_type"], "mysql.processlist")
        self.assertEqual(item["source"], "mysql")

        runtime_status_payload = _evidence_request_payload(
            normalized,
            tool_hint="collect_mysql_runtime_status",
            evidence_type="mysql.status",
            source="mysql",
        )
        runtime_status = validate_evidence_request(runtime_status_payload)
        runtime_item = runtime_status.evidence_request["required_evidence"][0]
        self.assertEqual(runtime_item["evidence_type"], "mysql.runtime_status")

    def test_mysql_analyzer_skill_defines_evidence_planning_mode(self) -> None:
        skill_path = Path("skills/mysql-analyzer/SKILL.md")

        self.assertTrue(skill_path.exists())
        skill_text = skill_path.read_text(encoding="utf-8")
        self.assertIn("evidence_planning", skill_text)
        self.assertIn("EvidenceRequest", skill_text)
        self.assertIn("Do not output root_cause", skill_text)

    def test_deepagents_factory_creates_mysql_analyzer_runtime(self) -> None:
        calls = []

        def fake_create_deep_agent(**kwargs):
            calls.append(kwargs)
            return object()

        runtime = DeepAgentsRuntimeFactory(
            create_deep_agent=fake_create_deep_agent,
            model=object(),
        ).create_mysql_analyzer_runtime("MYSQL_SKILL")

        self.assertIsNotNone(runtime)
        self.assertEqual(calls[0]["name"], "dbkit-mysql-analyzer")
        self.assertEqual(calls[0]["skills"], ["/skills/mysql-analyzer/"])
        self.assertIn("MYSQL_SKILL", calls[0]["system_prompt"])

    def test_pipeline_invokes_mysql_analyzer_in_evidence_planning_mode(self) -> None:
        case = self

        class FakeAnalyzerRuntime:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, payload):
                self.calls.append(payload)
                content = payload["messages"][0]["content"]
                case.assertIn('"mode": "evidence_planning"', content)
                case.assertIn('"normalized_request"', content)
                case.assertNotIn("root_cause", content)
                normalized = payload["normalized_request"]
                return {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                _evidence_request_payload(normalized),
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "mysql-error.log").write_text("error\n", encoding="utf-8")
            normalized = _provided_evidence_request(files=["/workspace/mysql-error.log"])
            runtime = FakeAnalyzerRuntime()

            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=TelemetryRecorder(),
                collectors=CollectorRegistry(workspace_root=workspace),
                time_provider=_fixed_time_provider(),
                mysql_analyzer_runtime=runtime,
            ).run(normalized)

            self.assertEqual(result.status, "raw_evidence_collected")
            self.assertEqual(len(runtime.calls), 1)
            self.assertEqual(len(result.raw_evidence), 1)

    def test_cpu_alert_request_produces_evidence_request_without_findings(self) -> None:
        normalized = _provided_evidence_request(files=["/workspace/tmp/mysql-error.log"])
        payload = {
            "request_id": normalized.request_id,
            "phase": "phase-02",
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "input_mode": "provided_evidence",
            "reasoning_mode": "evidence_planning",
            "evidence_request": {
                "goal": "collect evidence for MySQL CPU alert analysis",
                "required_evidence": [
                    {
                        "evidence_type": "mysql.error_log",
                        "priority": "required",
                        "purpose": "inspect MySQL error log around alert window",
                        "source": "provided_evidence",
                        "tool_hint": "read_provided_evidence_file",
                    }
                ],
                "optional_evidence": [],
                "not_required_evidence": [],
                "missing_inputs": [],
                "approval_requirements": [],
            },
            "metadata": {
                "skill": "skills/mysql-analyzer/SKILL.md",
                "mode": "evidence_planning",
            },
        }

        evidence_request = validate_evidence_request(payload)

        self.assertEqual(evidence_request.phase, "phase-02")
        self.assertEqual(evidence_request.reasoning_mode, "evidence_planning")
        serialized = json.dumps(evidence_request.to_dict(), ensure_ascii=False)
        for forbidden in ("root_cause", "findings", "verdict", "summary"):
            self.assertNotIn(forbidden, serialized)

    def test_evidence_request_rejects_findings_root_cause_or_verdict(self) -> None:
        normalized = _provided_evidence_request(files=["/workspace/tmp/mysql-error.log"])
        payload = _evidence_request_payload(normalized)
        payload["root_cause"] = "CPU pressure"

        with self.assertRaisesRegex(ValueError, "root_cause"):
            validate_evidence_request(payload)

    def test_provided_evidence_file_becomes_raw_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            workspace.mkdir()
            evidence_file = workspace / "mysql-error.log"
            evidence_file.write_text("错误日志\nline2\n", encoding="utf-8")
            normalized = _provided_evidence_request(
                files=["/workspace/mysql-error.log"]
            )

            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=TelemetryRecorder(),
                collectors=CollectorRegistry(workspace_root=workspace),
                time_provider=_fixed_time_provider(),
            ).run(
                normalized,
                evidence_request_json=_evidence_request_payload(normalized),
            )

            self.assertEqual(result.status, "raw_evidence_collected")
            self.assertEqual(len(result.raw_evidence), 1)
            raw = result.raw_evidence[0]
            self.assertEqual(raw.evidence_type, "mysql.error_log")
            self.assertEqual(raw.collection["status"], "collected")
            self.assertEqual(raw.payload["line_count"], 2)
            self.assertTrue(Path(raw.payload["content_ref"]).exists())
            index_artifact = [a for a in result.artifacts if a.kind == "RawEvidenceIndex"][0]
            index_text = index_artifact.path.read_text(encoding="utf-8")
            self.assertIn("错误日志", Path(raw.payload["content_ref"]).read_text(encoding="utf-8"))
            self.assertNotIn("\\u9519", index_text)

    def test_provided_evidence_directory_becomes_raw_evidence_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            evidence_dir = workspace / "mysql"
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "mysql-error.log").write_text("error\n", encoding="utf-8")
            (evidence_dir / "processlist.txt").write_text("process\n", encoding="utf-8")
            normalized = _provided_evidence_request(files=["/workspace/mysql/"])

            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=TelemetryRecorder(),
                collectors=CollectorRegistry(workspace_root=workspace),
                time_provider=_fixed_time_provider(),
            ).run(
                normalized,
                evidence_request_json=_evidence_request_payload(
                    normalized,
                    tool_hint="read_provided_evidence_directory",
                    evidence_type="provided_evidence.directory",
                ),
            )

            self.assertEqual(result.status, "raw_evidence_collected")
            self.assertEqual(len(result.raw_evidence), 2)
            self.assertEqual(
                sorted(item.source["path"] for item in result.raw_evidence),
                ["/workspace/mysql/mysql-error.log", "/workspace/mysql/processlist.txt"],
            )

    def test_live_collection_missing_target_is_blocked(self) -> None:
        normalized = normalize_request(
            "连接 MySQL 分析 CPU 告警",
            llm_json={
                "target_agent": "mysql_analyzer",
                "target_domain": "mysql",
                "task_type": "alert_analysis",
                "routing_confidence": 0.91,
                "input_mode": "live_collection",
                "target": None,
                "collection_policy": {
                    "allow_live_collection": True,
                    "allow_mysql_login": True,
                    "allow_ssh": False,
                    "allow_metrics_query": False,
                },
                "event": {"event_time": "2026-05-08T17:00:00+08:00"},
                "missing_fields": [],
            },
            phase="phase-02",
        )
        evidence_request = validate_evidence_request(
            _evidence_request_payload(
                normalized,
                tool_hint="collect_mysql_runtime_status",
                evidence_type="mysql.runtime_status",
                source="mysql",
            )
        )
        plan = EvidencePipeline.create_collection_plan(evidence_request, normalized)

        result = CollectionGuardrails().validate(plan, normalized)

        self.assertFalse(result.passed)
        self.assertTrue(any("target.host" in issue for issue in result.blocking_issues))

    def test_live_collector_failure_does_not_fake_success(self) -> None:
        normalized = normalize_request(
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
                "collection_policy": {
                    "allow_live_collection": True,
                    "allow_mysql_login": True,
                    "allow_ssh": False,
                    "allow_metrics_query": False,
                },
                "event": {"event_time": "2026-05-08T17:00:00+08:00"},
                "missing_fields": [],
            },
            phase="phase-02",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=TelemetryRecorder(),
                collectors=CollectorRegistry(
                    workspace_root=root / "workspace",
                    mysql_client_factory=lambda _request, _secrets: FailingMySQLClient(),
                ),
                time_provider=_fixed_time_provider(),
            ).run(
                normalized,
                evidence_request_json=_mysql_baseline_evidence_request_payload(normalized),
            )

            self.assertEqual(result.status, "collection_failed")
            self.assertEqual(result.raw_evidence[0].collection["status"], "failed")

    def test_raw_secrets_absent_from_artifacts_and_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "mysql-error.log").write_text("password redacted\n", encoding="utf-8")
            normalized = _provided_evidence_request(
                files=["/workspace/mysql-error.log"],
                redaction_summary={
                    "redacted": True,
                    "secret_refs": ["<SECRET_REF:chinese_password_001>"],
                    "redacted_patterns": ["chinese_password_assignment"],
                },
            )

            result = EvidencePipeline(
                artifact_store=ArtifactStore(root / "artifacts"),
                telemetry=TelemetryRecorder(),
                collectors=CollectorRegistry(workspace_root=workspace),
                time_provider=_fixed_time_provider(),
            ).run(
                normalized,
                evidence_request_json=_evidence_request_payload(normalized),
            )

            all_text = "\n".join(
                artifact.path.read_text(encoding="utf-8")
                for artifact in result.artifacts
            )
            telemetry_text = "\n".join(
                json.dumps(event.to_dict(), ensure_ascii=False)
                for event in result.telemetry
            )
            self.assertNotIn("Root", all_text)
            self.assertNotIn("Root", telemetry_text)


def _provided_evidence_request(
    *,
    files: list[str],
    redaction_summary: dict | None = None,
):
    return normalize_request(
        "请帮我分析这个 MySQL，今天17:00触发 mysql cpu usage > 85%，只需要分析本地文件。",
        llm_json={
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.93,
            "input_mode": "provided_evidence",
            "target": None,
            "ssh_target": None,
            "provided_evidence": {
                "mode": "local_files",
                "files": files,
                "pasted_text": False,
                "description": "只需要分析本地文件",
                "discovery": {
                    "attempted_paths": files,
                    "discovered_files": files,
                    "discovery_status": "files_found",
                    "errors": [],
                },
            },
            "collection_policy": {
                "allow_live_collection": False,
                "allow_mysql_login": False,
                "allow_ssh": False,
                "allow_metrics_query": False,
            },
            "event": {
                "event_time": "2026-05-08T17:00:00+08:00",
                "time_window": {
                    "before": "6h",
                    "after": "1h",
                    "source": "skill_default_from_event_time",
                },
                "alerts": [{"raw": "mysql cpu usage > 85%"}],
                "symptoms": ["high_cpu"],
            },
            "missing_fields": [],
        },
        redaction_summary=redaction_summary,
        phase="phase-02",
    )


def _evidence_request_payload(
    normalized,
    *,
    tool_hint: str = "read_provided_evidence_file",
    evidence_type: str = "mysql.error_log",
    source: str = "provided_evidence",
) -> dict:
    return {
        "request_id": normalized.request_id,
        "phase": "phase-02",
        "target_agent": "mysql_analyzer",
        "target_domain": "mysql",
        "task_type": normalized.task_type,
        "input_mode": normalized.input_mode,
        "reasoning_mode": "evidence_planning",
        "evidence_request": {
            "goal": "collect evidence for MySQL CPU alert analysis",
            "required_evidence": [
                {
                    "evidence_type": evidence_type,
                    "priority": "required",
                    "purpose": "collect raw operational evidence",
                    "source": source,
                    "tool_hint": tool_hint,
                }
            ],
            "optional_evidence": [],
            "not_required_evidence": [],
            "missing_inputs": [],
            "approval_requirements": [],
        },
        "metadata": {
            "skill": "skills/mysql-analyzer/SKILL.md",
            "mode": "evidence_planning",
        },
    }


def _mysql_baseline_evidence_request_payload(normalized) -> dict:
    payload = _evidence_request_payload(
        normalized,
        tool_hint="collect_mysql_processlist",
        evidence_type="mysql.processlist",
        source="mysql",
    )
    payload["evidence_request"]["required_evidence"] = [
        {
            "evidence_type": evidence_type,
            "priority": "required",
            "purpose": "collect raw operational evidence",
            "source": "mysql",
            "tool_hint": tool_hint,
        }
        for tool_hint, evidence_type in (
            ("collect_mysql_processlist", "mysql.processlist"),
            ("collect_mysql_runtime_status", "mysql.runtime_status"),
            ("collect_mysql_innodb_status", "mysql.innodb_status"),
            ("collect_mysql_variables", "mysql.variables"),
            ("collect_mysql_service_metadata", "mysql.service_metadata"),
            ("discover_mysql_log_paths", "mysql.log_paths"),
        )
    ]
    return payload


class FailingMySQLClient:
    def execute(self, sql: str) -> list[dict]:
        raise RuntimeError("connection refused")


def _fixed_time_provider() -> FixedTimeProvider:
    return FixedTimeProvider(
        current_datetime=datetime.fromisoformat("2026-05-08T10:00:00+08:00"),
        timezone="Asia/Shanghai",
        locale="zh-CN",
    )


if __name__ == "__main__":
    unittest.main()
