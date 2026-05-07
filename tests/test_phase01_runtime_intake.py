import json
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from dbkit.agents.intake import IntakeAgent
from dbkit.config import ProviderKind, load_app_config
from dbkit.model_provider import build_agent_model, build_model
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.deepagents_runtime import DeepAgentsRuntimeFactory
from dbkit.runtime.guardrails import Guardrails
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.orchestrator import Orchestrator
from dbkit.runtime.redactor import Redactor
from dbkit.runtime.router import Router
from dbkit.tools.normalize_request import normalize_request


class Phase01RuntimeIntakeTest(unittest.TestCase):
    def test_load_app_config_reads_llm_settings_from_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "model:",
                        "  provider_kind: openai_compatible",
                        "  model_name: qwen3.5-flash",
                        "  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "  api_key: sk-test",
                        "  temperature: 0.1",
                        "  reasoning_effort: high",
                        "  extra_body:",
                        "    thinking:",
                        "      type: enabled",
                        "agent:",
                        "  tool_calling: true",
                        "  tool_calling_thinking_type: disabled",
                        "runtime:",
                        "  artifact_dir: .dbkit/test-artifacts",
                        "  invoke_llm: true",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_app_config(config_path)

            self.assertEqual(config.model.provider_kind, ProviderKind.OPENAI_COMPATIBLE)
            self.assertEqual(config.model.model_name, "qwen3.5-flash")
            self.assertEqual(
                config.model.base_url,
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
            self.assertEqual(config.model.api_key, "sk-test")
            self.assertEqual(config.model.temperature, 0.1)
            self.assertEqual(config.model.reasoning_effort, "high")
            self.assertEqual(config.model.extra_body, {"thinking": {"type": "enabled"}})
            self.assertTrue(config.agent.tool_calling)
            self.assertEqual(config.agent.tool_calling_thinking_type, "disabled")
            self.assertEqual(config.runtime.artifact_dir, Path(".dbkit/test-artifacts"))
            self.assertTrue(config.runtime.invoke_llm)

    def test_build_model_uses_openai_compatible_config(self) -> None:
        config = load_app_config(_write_config_file())
        calls = []

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        with patch("dbkit.model_provider.ChatOpenAI", FakeChatOpenAI):
            model = build_model(config.model)

        self.assertIsInstance(model, FakeChatOpenAI)
        self.assertEqual(
            calls[0],
            {
                "model": "qwen3.5-flash",
                "api_key": "sk-test",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "temperature": 0.0,
                "stream_usage": False,
                "max_retries": 5,
                "reasoning_effort": "high",
                "extra_body": {"thinking": {"type": "enabled"}},
            },
        )

    def test_build_agent_model_disables_configured_thinking_for_tool_calling_runtime(self) -> None:
        config = load_app_config(_write_config_file())
        calls = []

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        with patch("dbkit.model_provider.ChatOpenAI", FakeChatOpenAI):
            model = build_agent_model(config.model, config.agent)

        self.assertIsInstance(model, FakeChatOpenAI)
        self.assertEqual(calls[0]["model"], "qwen3.5-flash")
        self.assertEqual(calls[0]["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertNotIn("reasoning_effort", calls[0])

    def test_build_agent_model_disables_thinking_by_default_for_tool_calling_runtime(self) -> None:
        config = load_app_config(_write_config_file_without_agent_section())
        calls = []

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        with patch("dbkit.model_provider.ChatOpenAI", FakeChatOpenAI):
            build_agent_model(config.model, config.agent)

        self.assertEqual(config.agent.tool_calling_thinking_type, "disabled")
        self.assertEqual(calls[0]["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertNotIn("reasoning_effort", calls[0])

    def test_build_agent_model_preserves_default_connection_when_tool_calling_disabled(
        self,
    ) -> None:
        config = load_app_config(_write_config_file())
        agent = replace(config.agent, tool_calling=False)
        calls = []

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        with patch("dbkit.model_provider.ChatOpenAI", FakeChatOpenAI):
            build_agent_model(config.model, agent)

        self.assertEqual(calls[0]["reasoning_effort"], "high")
        self.assertEqual(calls[0]["extra_body"], {"thinking": {"type": "enabled"}})

    # --- Redactor ---

    def test_redactor_removes_english_secrets_with_secret_refs(self) -> None:
        raw = (
            "password=abc123 passwd: def456 token=tok-1 "
            "secret: sec-value api_key=api-value Authorization: Bearer auth-token "
            "password is EnglishSecret"
        )
        result = Redactor().redact(raw)

        for secret in (
            "abc123",
            "def456",
            "tok-1",
            "sec-value",
            "api-value",
            "auth-token",
            "EnglishSecret",
        ):
            self.assertNotIn(secret, result.redacted_text)
        self.assertIn("<SECRET_REF:", result.redacted_text)
        self.assertGreater(len(result.secret_refs), 0)
        self.assertTrue(result.redaction_summary["redacted"])

    def test_redactor_removes_database_uri_passwords(self) -> None:
        raw = (
            "mysql://root:rootpass@db:3306/app redis://:redispass@cache:6379 "
            "mongodb://u:mongopass@mongo/db postgres://pg:pgpass@pg/db"
        )
        result = Redactor().redact(raw)

        for secret in ("rootpass", "redispass", "mongopass", "pgpass"):
            self.assertNotIn(secret, result.redacted_text)
        self.assertIn("<SECRET_REF:", result.redacted_text)
        # Usernames and hosts are preserved
        self.assertIn("root:", result.redacted_text)
        self.assertIn("@db:3306", result.redacted_text)

    def test_redactor_removes_password_is_assignment(self) -> None:
        result = Redactor().redact("password is EnglishSecret")

        self.assertNotIn("EnglishSecret", result.redacted_text)
        self.assertIn("<SECRET_REF:password_", result.redacted_text)

    def test_redactor_removes_chinese_password_patterns(self) -> None:
        for raw in [
            "MySQL密码是Root123",
            "密码为SuperSecret",
            "口令是Pass@word",
            "口令为Hidden99",
        ]:
            result = Redactor().redact(raw)
            self.assertIn("<SECRET_REF:chinese_password_", result.redacted_text, msg=raw)
            # Verify the actual secret value is gone — extract it from the raw string
            import re
            m = re.search(r"[是为]\s*(\S+)", raw)
            if m:
                self.assertNotIn(m.group(1), result.redacted_text, msg=raw)

    def test_redactor_returns_secret_refs_and_summary(self) -> None:
        raw = "密码是Secret123 password=AnotherSecret"
        result = Redactor().redact(raw)

        self.assertGreaterEqual(len(result.secret_refs), 2)
        self.assertIn("chinese_password_assignment", result.redaction_summary["redacted_patterns"])
        self.assertIn("english_assignment", result.redaction_summary["redacted_patterns"])

    def test_redactor_produces_no_secret_refs_for_clean_input(self) -> None:
        result = Redactor().redact("MySQL CPU usage is high")
        self.assertEqual(result.secret_refs, ())
        self.assertFalse(result.redaction_summary["redacted"])

    # --- normalize_request ---

    def test_normalize_request_deterministic_fallback_creates_phase011_request(self) -> None:
        request = normalize_request("MySQL connection spike on prod-db-1")

        self.assertTrue(request.request_id.startswith("req_"))
        self.assertEqual(request.phase, "phase-01.1")
        self.assertEqual(request.target_domain, "mysql")
        self.assertEqual(request.requested_capability, "runtime_intake")
        self.assertIn("target.host", request.missing_fields)
        self.assertIn("target.username", request.missing_fields)
        self.assertNotIn("time_window", request.missing_fields)

    def test_normalize_request_with_llm_json_infers_time_window_from_event_time(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.92,
            "target": {"host": "192.168.1.1", "port": 3306, "username": "root"},
            "ssh_target": None,
            "event": {
                "event_time": "2026-05-07T17:00:00+08:00",
                "time_window": {
                    "before": "6h",
                    "after": "1h",
                    "source": "skill_default_from_event_time",
                },
                "alerts": [{"raw": "mysql cpu > 85%", "semantic_hint": "high_cpu"}],
                "symptoms": ["high_cpu"],
            },
            "missing_fields": [],
        }

        request = normalize_request(
            "请帮我分析这个MySQL，今天17:00触发 mysql cpu usage > 85%",
            llm_json=llm_json,
        )

        self.assertIsNotNone(request.event)
        tw = request.event["time_window"]
        self.assertEqual(tw["source"], "skill_default_from_event_time")
        self.assertIn("2026-05-07T11:00:00", tw["start"])
        self.assertIn("2026-05-07T18:00:00", tw["end"])
        self.assertEqual(tw["before"], "6h")
        self.assertEqual(tw["after"], "1h")
        self.assertNotIn("time_window", request.missing_fields)
        self.assertEqual(request.missing_fields, ())

    def test_normalize_request_with_explicit_time_window_preserves_user_range(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.9,
            "target": {"host": "db-1", "port": 3306, "username": "admin"},
            "event": {
                "event_time": "2026-05-07T17:00:00+08:00",
                "time_window": {
                    "start": "2026-05-07T16:00:00+08:00",
                    "end": "2026-05-07T17:00:00+08:00",
                    "source": "user_explicit",
                    "before": "1h",
                    "after": "0h",
                },
                "symptoms": [],
            },
            "missing_fields": [],
        }

        request = normalize_request("分析今天17:00告警，只看前1小时", llm_json=llm_json)

        tw = request.event["time_window"]
        self.assertEqual(tw["source"], "user_explicit")
        self.assertEqual(tw["before"], "1h")

    def test_normalize_request_marks_missing_target_host_and_username(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.7,
            "target": None,
            "event": {"event_time": "2026-05-07T17:00:00+08:00", "symptoms": []},
            "missing_fields": ["target.host", "target.username"],
        }

        request = normalize_request("请分析数据库故障", llm_json=llm_json)

        self.assertIn("target.host", request.missing_fields)
        self.assertIn("target.username", request.missing_fields)

    def test_normalize_request_provided_evidence_does_not_require_live_target(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.91,
            "input_mode": "provided_evidence",
            "target": None,
            "ssh_target": None,
            "provided_evidence": {
                "mode": "local_files",
                "files": [],
                "pasted_text": False,
                "description": "只需要分析本地文件",
            },
            "collection_policy": {
                "allow_live_collection": False,
                "allow_mysql_login": False,
                "allow_ssh": False,
                "allow_metrics_query": False,
            },
            "event": {
                "event_time": "2026-05-07T17:00:00+08:00",
                "time_window": {
                    "before": "6h",
                    "after": "1h",
                    "source": "skill_default_from_event_time",
                },
                "alerts": [{"raw": "mysql cpu usage > 85%"}],
                "symptoms": ["high_cpu"],
            },
            "evidence_plan": {
                "required_evidence": ["mysql.processlist", "metrics.cpu"],
                "provided_evidence": [],
                "missing_evidence": ["provided_evidence.files"],
            },
            "missing_fields": ["provided_evidence.files"],
        }

        request = normalize_request(
            "请帮我分析这个 MySQL，今天17:00触发 mysql cpu usage > 85%，只需要分析本地文件。",
            llm_json=llm_json,
        )

        self.assertEqual(request.input_mode, "provided_evidence")
        self.assertIsNone(request.target)
        self.assertIsNone(request.ssh_target)
        self.assertFalse(request.collection_policy["allow_live_collection"])
        self.assertFalse(request.collection_policy["allow_mysql_login"])
        self.assertFalse(request.collection_policy["allow_ssh"])
        self.assertNotIn("target.host", request.missing_fields)
        self.assertNotIn("target.username", request.missing_fields)
        self.assertIn("provided_evidence.files", request.missing_fields)

    def test_normalize_request_live_collection_requires_live_target_fields(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.93,
            "input_mode": "live_collection",
            "target": {"type": "mysql", "host": "192.168.1.1", "port": 3306, "username": "root"},
            "collection_policy": {
                "allow_live_collection": True,
                "allow_mysql_login": True,
                "allow_ssh": False,
                "allow_metrics_query": False,
            },
            "event": {"event_time": "2026-05-07T17:00:00+08:00", "symptoms": ["high_cpu"]},
            "missing_fields": ["target.password_ref"],
        }

        request = normalize_request(
            "请连接 192.168.1.1 的 MySQL 分析今天17:00的 CPU 告警，用户 root。",
            llm_json=llm_json,
        )

        self.assertEqual(request.input_mode, "live_collection")
        self.assertEqual(request.target["host"], "192.168.1.1")
        self.assertEqual(request.target["username"], "root")
        self.assertIn("target.password_ref", request.missing_fields)

    def test_normalize_request_hybrid_only_requires_allowed_collection_fields(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "incident_analysis",
            "routing_confidence": 0.88,
            "input_mode": "hybrid",
            "target": None,
            "provided_evidence": {
                "mode": "local_files",
                "files": ["/tmp/mysql-slow.log"],
                "pasted_text": False,
                "description": "我有慢日志文件",
            },
            "collection_policy": {
                "allow_live_collection": True,
                "allow_mysql_login": True,
                "allow_ssh": False,
                "allow_metrics_query": False,
            },
            "event": {"event_time": "2026-05-07T17:00:00+08:00", "symptoms": ["slow_query"]},
            "missing_fields": ["target.host", "target.username"],
        }

        request = normalize_request(
            "我有慢日志文件 /tmp/mysql-slow.log，也可以连数据库补充看 processlist。",
            llm_json=llm_json,
        )

        self.assertEqual(request.input_mode, "hybrid")
        self.assertTrue(request.collection_policy["allow_mysql_login"])
        self.assertEqual(request.provided_evidence["files"], ["/tmp/mysql-slow.log"])
        self.assertIn("target.host", request.missing_fields)
        self.assertNotIn("ssh_target.host", request.missing_fields)

    def test_normalize_request_extracts_redacted_mysql_uri_target(self) -> None:
        redacted = "mysql://root:<SECRET_REF:uri_password_001>@192.168.1.1:3306"
        request = normalize_request(
            redacted,
            redaction_summary={
                "redacted": True,
                "secret_refs": ["<SECRET_REF:uri_password_001>"],
                "redacted_patterns": ["database_uri"],
            },
        )

        self.assertEqual(request.target["host"], "192.168.1.1")
        self.assertEqual(request.target["port"], 3306)
        self.assertEqual(request.target["username"], "root")
        self.assertEqual(request.target["password_ref"], "<SECRET_REF:uri_password_001>")
        self.assertNotIn("Root", json.dumps(request.to_dict(), ensure_ascii=False))

    # --- Intake Agent ---

    def test_intake_agent_loads_skill_from_repo(self) -> None:
        agent = IntakeAgent.from_repo_root(Path.cwd())

        self.assertEqual(agent.name, "intake")
        self.assertIn("DBKit Intake Skill", agent.skill_text)
        self.assertIn("Output Contract", agent.skill_text)
        self.assertIn("Missing Field Rules", agent.skill_text)

    # --- Guardrails ---

    def test_guardrails_passes_complete_request(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.92,
            "target": {"host": "192.168.1.1", "port": 3306, "username": "root"},
            "event": {
                "event_time": "2026-05-07T17:00:00+08:00",
                "symptoms": ["high_cpu"],
            },
            "missing_fields": [],
        }
        request = normalize_request("MySQL CPU alert", llm_json=llm_json)
        result = Guardrails().validate(request)

        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_issues, ())

    def test_guardrails_blocks_when_required_fields_missing(self) -> None:
        request = normalize_request("请分析数据库故障")
        result = Guardrails().validate(request)

        self.assertFalse(result.passed)
        self.assertTrue(
            any("target.host" in i for i in result.blocking_issues)
        )

    def test_guardrails_provided_evidence_blocks_on_evidence_not_live_target(self) -> None:
        request = normalize_request(
            "只需要分析本地文件",
            llm_json={
                "target_agent": "mysql_analyzer",
                "target_domain": "mysql",
                "task_type": "alert_analysis",
                "routing_confidence": 0.86,
                "input_mode": "provided_evidence",
                "target": None,
                "ssh_target": None,
                "provided_evidence": {
                    "mode": "local_files",
                    "files": [],
                    "pasted_text": False,
                    "description": "只需要分析本地文件",
                },
                "collection_policy": {
                    "allow_live_collection": False,
                    "allow_mysql_login": False,
                    "allow_ssh": False,
                    "allow_metrics_query": False,
                },
                "event": {"event_time": "2026-05-07T17:00:00+08:00", "symptoms": []},
                "missing_fields": ["provided_evidence.files"],
            },
        )

        result = Guardrails().validate(request)

        self.assertFalse(result.passed)
        self.assertTrue(any("provided_evidence.files" in i for i in result.blocking_issues))
        self.assertFalse(any("target.host" in i for i in result.blocking_issues))
        self.assertFalse(any("target.username" in i for i in result.blocking_issues))

    def test_guardrails_blocks_invalid_target_agent(self) -> None:
        llm_json = {
            "target_agent": "intake_agent",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.9,
            "target": {"host": "db", "port": 3306, "username": "root"},
            "event": {"event_time": "2026-05-07T17:00:00+08:00", "symptoms": []},
            "missing_fields": [],
        }
        request = normalize_request("MySQL issue", llm_json=llm_json)
        result = Guardrails().validate(request)

        self.assertFalse(result.passed)
        self.assertTrue(
            any("target_agent" in i for i in result.blocking_issues)
        )

    def test_guardrails_blocks_system_agent_targets(self) -> None:
        for bad_agent in ("evidence_agent", "validation_agent"):
            llm_json = {
                "target_agent": bad_agent,
                "target_domain": "mysql",
                "task_type": "alert_analysis",
                "routing_confidence": 0.9,
                "target": {"host": "db", "port": 3306, "username": "root"},
                "event": {"event_time": "2026-05-07T17:00:00+08:00", "symptoms": []},
                "missing_fields": [],
            }
            request = normalize_request("MySQL issue", llm_json=llm_json)
            result = Guardrails().validate(request)
            self.assertFalse(result.passed, msg=f"Expected block for {bad_agent}")

    def test_guardrails_detects_secret_leakage_in_redacted_input(self) -> None:
        from dbkit.schemas.runtime import NormalizedRequest
        request = NormalizedRequest(
            request_id="req_test",
            original_input="MySQL密码是RawSecret",
            redacted_input="MySQL密码是RawSecret",  # not redacted — simulates leakage
            target_domain="mysql",
            requested_capability="runtime_intake",
            missing_fields=("target.host", "target.username"),
        )
        result = Guardrails().validate(request)

        self.assertFalse(result.passed)
        self.assertTrue(any("secret leakage" in i for i in result.blocking_issues))

    # --- Router ---

    def test_router_selects_mysql_analyzer_agent_name(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.9,
            "target": {"host": "db-1", "port": 3306, "username": "root"},
            "event": {"event_time": "2026-05-07T17:00:00+08:00", "symptoms": []},
            "missing_fields": [],
        }
        request = normalize_request("MySQL replication lag", llm_json=llm_json)

        route = Router().route(request)

        self.assertEqual(route.target_agent_name, "mysql_analyzer")
        self.assertEqual(route.target_domain, "mysql")
        self.assertEqual(route.phase, "phase-01.1")

    # --- ArtifactStore ---

    def test_artifact_store_persists_normalized_request_with_chinese_readability(self) -> None:
        llm_json = {
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "alert_analysis",
            "routing_confidence": 0.9,
            "target": {"host": "db-1", "port": 3306, "username": "root"},
            "event": {"event_time": "2026-05-07T17:00:00+08:00", "symptoms": []},
            "missing_fields": [],
        }
        request = normalize_request("MySQL CPU 告警", llm_json=llm_json)

        with tempfile.TemporaryDirectory() as tmpdir:
            record = ArtifactStore(Path(tmpdir)).persist_request(request)

            raw = record.path.read_text(encoding="utf-8")
            # ensure_ascii=False: Chinese must appear as readable characters, not as \uXXXX
            self.assertIn("告", raw)
            self.assertNotIn("\\u544a", raw)  # 告 is the escaped form of 告
            payload = json.loads(raw)
            self.assertEqual(payload["request_id"], request.request_id)
            self.assertEqual(payload["target_domain"], "mysql")

    def test_artifact_store_persists_telemetry_as_jsonl(self) -> None:
        recorder = TelemetryRecorder()
        recorder.emit(
            event_type="redaction_completed",
            stage="redactor",
            message="中文测试",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            record = ArtifactStore(Path(tmpdir)).persist_telemetry("req_test", recorder.events)

            self.assertTrue(record.path.exists())
            raw = record.path.read_text(encoding="utf-8")
            # ensure_ascii=False: Chinese must appear as readable characters, not as \uXXXX
            self.assertIn("中", raw)
            self.assertNotIn("\\u4e2d", raw)  # 中 is the escaped form of 中
            line = json.loads(raw.strip().split("\n")[0])
            self.assertEqual(line["event_type"], "redaction_completed")

    def test_artifact_store_persists_blocked_request(self) -> None:
        request = normalize_request("请分析数据库故障")

        with tempfile.TemporaryDirectory() as tmpdir:
            record = ArtifactStore(Path(tmpdir)).persist_blocked_request(
                request, ("missing required field: target.host",)
            )

            payload = json.loads(record.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked")
            self.assertIn("missing required field: target.host", payload["blocking_issues"])

    # --- TelemetryRecorder ---

    def test_telemetry_recorder_emits_phase011_specific_events(self) -> None:
        recorder = TelemetryRecorder()

        recorder.emit_redaction_completed(
            request_id="req_test", secret_count=2, patterns=["english_assignment"],
            raw_bytes=100, filtered_bytes=80,
        )
        recorder.emit_intake_agent_started(request_id="req_test")
        recorder.emit_intake_agent_completed(request_id="req_test")
        recorder.emit_normalize_request_started(request_id="req_test")
        recorder.emit_normalize_request_completed(
            request_id="req_test", missing_fields=[]
        )
        recorder.emit_guardrails_started(request_id="req_test")
        recorder.emit_guardrails_passed(request_id="req_test")
        recorder.emit_route_selected(
            request_id="req_test", target_agent="mysql_analyzer", target_domain="mysql"
        )
        recorder.emit_artifact_written(
            request_id="req_test", kind="NormalizedRequest", path="/tmp/test.json"
        )

        event_types = [e.event_type for e in recorder.events]
        for expected in [
            "redaction_completed",
            "intake_agent_started",
            "intake_agent_completed",
            "normalize_request_started",
            "normalize_request_completed",
            "request_guardrails_started",
            "request_guardrails_passed",
            "route_selected",
            "artifact_written",
        ]:
            self.assertIn(expected, event_types)

    def test_telemetry_recorder_emits_blocked_event(self) -> None:
        recorder = TelemetryRecorder()
        recorder.emit_guardrails_blocked(
            request_id="req_test",
            blocking_issues=["missing required field: target.host"],
        )

        self.assertEqual(recorder.events[0].event_type, "request_guardrails_blocked")
        self.assertIn(
            "missing required field: target.host",
            recorder.events[0].attributes["blocking_issues"],
        )

    def test_telemetry_recorder_emits_intake_json_parse_failed(self) -> None:
        recorder = TelemetryRecorder()
        recorder.emit_intake_json_parse_failed(
            request_id="req_test",
            reason="no parseable JSON found",
        )

        self.assertEqual(recorder.events[0].event_type, "intake_json_parse_failed")
        self.assertTrue(recorder.events[0].attributes["llm_intake_failed"])

    # --- DeepAgentsRuntimeFactory ---

    def test_deepagents_runtime_factory_calls_sdk_constructor(self) -> None:
        calls = []

        def fake_create_deep_agent(**kwargs):
            calls.append(kwargs)
            return object()

        model = object()
        factory = DeepAgentsRuntimeFactory(
            create_deep_agent=fake_create_deep_agent,
            model=model,
        )

        runtime = factory.create_intake_runtime(skill_text="DBKit Intake Skill")

        self.assertIsNotNone(runtime)
        self.assertIs(calls[0]["model"], model)
        self.assertEqual(len(calls[0]["tools"]), 1)
        self.assertEqual(calls[0]["tools"][0].__name__, "normalize_request_tool")
        self.assertIn("DBKit Intake Skill", calls[0]["system_prompt"])
        self.assertEqual(calls[0]["name"], "dbkit-intake")

    def test_deepagents_runtime_factory_loads_system_prompt_from_agents_dir(self) -> None:
        calls = []

        def fake_create_deep_agent(**kwargs):
            calls.append(kwargs)
            return object()

        model = object()
        factory = DeepAgentsRuntimeFactory(
            create_deep_agent=fake_create_deep_agent,
            model=model,
            repo_root=Path.cwd(),
        )

        factory.create_intake_runtime(skill_text="SKILL_CONTENT")

        # agents/intake/system.md must be reflected in the system prompt
        self.assertIn("DBKit Intake Agent", calls[0]["system_prompt"])
        self.assertIn("SKILL_CONTENT", calls[0]["system_prompt"])

    # --- Orchestrator ---

    def test_orchestrator_consumes_llm_json_and_routes_successfully(self) -> None:
        class FakeIntakeRuntime:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, payload):
                self.calls.append(payload)
                return {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": json.dumps({
                                "target_agent": "mysql_analyzer",
                                "target_domain": "mysql",
                                "task_type": "alert_analysis",
                                "routing_confidence": 0.92,
                                "target": {
                                    "host": "prod-db-1",
                                    "port": 3306,
                                    "username": "monitor",
                                },
                                "ssh_target": None,
                                "event": {
                                    "event_time": "2026-05-07T17:00:00+08:00",
                                    "time_window": {
                                        "before": "6h",
                                        "after": "1h",
                                        "source": "skill_default_from_event_time",
                                    },
                                    "alerts": [],
                                    "symptoms": ["high_cpu"],
                                },
                                "missing_fields": [],
                            }),
                        }
                    ]
                }

        class FakeDeepAgentsRuntimeFactory:
            def create_intake_runtime(self, skill_text: str):
                self.skill_text = skill_text
                self.runtime = FakeIntakeRuntime()
                return self.runtime

        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts_root = Path(tmpdir) / "artifacts"
            factory = FakeDeepAgentsRuntimeFactory()
            orchestrator = Orchestrator(
                repo_root=Path.cwd(),
                artifact_store=ArtifactStore(artifacts_root),
                telemetry=TelemetryRecorder(),
                deepagents_runtime_factory=factory,
            )

            result = orchestrator.run(
                "MySQL CPU spike password=abc token=secret-token"
            )

            self.assertFalse(result.blocked)
            self.assertTrue(result.deepagents_runtime_ready)
            self.assertEqual(result.route_decision.target_agent_name, "mysql_analyzer")
            self.assertEqual(result.normalized_request.metadata["normalizer"], "llm_intake_plus_normalize_request")
            self.assertEqual(result.normalized_request.task_type, "alert_analysis")
            self.assertIsNotNone(result.normalized_request.event)
            self.assertIn("time_window", result.normalized_request.event)
            self.assertEqual(len(result.artifacts), 2)
            self.assertTrue(result.artifacts[0].path.exists())

            # Secrets must not reach the LLM
            self.assertNotIn("abc", factory.runtime.calls[0]["messages"][0]["content"])
            self.assertNotIn(
                "secret-token", factory.runtime.calls[0]["messages"][0]["content"]
            )

            # Key phase-01.1 telemetry events must be present
            event_types = [e.event_type for e in result.telemetry]
            for expected in [
                "redaction_completed",
                "intake_agent_started",
                "intake_agent_completed",
                "normalize_request_completed",
                "request_guardrails_passed",
                "route_selected",
                "artifact_written",
            ]:
                self.assertIn(expected, event_types, msg=f"Missing event: {expected}")

    def test_orchestrator_consumes_langchain_message_object_json(self) -> None:
        class FakeIntakeRuntime:
            def invoke(self, payload):
                return {
                    "messages": [
                        SimpleNamespace(
                            type="ai",
                            content=json.dumps({
                                "target_agent": "mysql_analyzer",
                                "target_domain": "mysql",
                                "task_type": "alert_analysis",
                                "routing_confidence": 0.92,
                                "input_mode": "provided_evidence",
                                "target": None,
                                "ssh_target": None,
                                "provided_evidence": {
                                    "mode": "local_files",
                                    "files": ["/tmp/mysql-error.log"],
                                    "pasted_text": False,
                                    "description": "只分析本地文件",
                                },
                                "collection_policy": {
                                    "allow_live_collection": False,
                                    "allow_mysql_login": False,
                                    "allow_ssh": False,
                                    "allow_metrics_query": False,
                                },
                                "event": {
                                    "event_time": "2026-05-07T17:00:00+08:00",
                                    "time_window": {
                                        "before": "6h",
                                        "after": "1h",
                                        "source": "skill_default_from_event_time",
                                    },
                                    "alerts": [{"raw": "mysql cpu usage > 85%"}],
                                    "symptoms": ["high_cpu"],
                                },
                                "missing_fields": [],
                            }),
                        )
                    ]
                }

        class FakeFactory:
            def create_intake_runtime(self, skill_text: str):
                return FakeIntakeRuntime()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = Orchestrator(
                repo_root=Path.cwd(),
                artifact_store=ArtifactStore(Path(tmpdir) / "artifacts"),
                telemetry=TelemetryRecorder(),
                deepagents_runtime_factory=FakeFactory(),
            ).run(
                "请帮我分析这个 MySQL，今天17:00触发 mysql cpu usage > 85%，只需要分析本地文件。"
            )

            self.assertFalse(result.blocked)
            self.assertEqual(result.normalized_request.input_mode, "provided_evidence")
            self.assertEqual(
                result.normalized_request.metadata["normalizer"],
                "llm_intake_plus_normalize_request",
            )
            self.assertEqual(result.normalized_request.task_type, "alert_analysis")
            self.assertIn("time_window", result.normalized_request.event)

    def test_orchestrator_extracts_json_from_fenced_assistant_content(self) -> None:
        class FakeIntakeRuntime:
            def invoke(self, payload):
                return {
                    "messages": [
                        SimpleNamespace(
                            type="ai",
                            content=(
                                "Here is the JSON.\n```json\n"
                                + json.dumps({
                                    "target_agent": "mysql_analyzer",
                                    "target_domain": "mysql",
                                    "task_type": "alert_analysis",
                                    "routing_confidence": 0.9,
                                    "input_mode": "provided_evidence",
                                    "target": None,
                                    "ssh_target": None,
                                    "provided_evidence": {
                                        "mode": "local_files",
                                        "files": [],
                                        "pasted_text": False,
                                        "description": "只需要分析本地文件",
                                    },
                                    "collection_policy": {
                                        "allow_live_collection": False,
                                        "allow_mysql_login": False,
                                        "allow_ssh": False,
                                        "allow_metrics_query": False,
                                    },
                                    "event": {
                                        "event_time": "2026-05-07T17:00:00+08:00",
                                        "time_window": {
                                            "before": "6h",
                                            "after": "1h",
                                            "source": "skill_default_from_event_time",
                                        },
                                    },
                                    "missing_fields": ["provided_evidence.files"],
                                })
                                + "\n```"
                            ),
                        )
                    ]
                }

        class FakeFactory:
            def create_intake_runtime(self, skill_text: str):
                return FakeIntakeRuntime()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = Orchestrator(
                repo_root=Path.cwd(),
                artifact_store=ArtifactStore(Path(tmpdir) / "artifacts"),
                telemetry=TelemetryRecorder(),
                deepagents_runtime_factory=FakeFactory(),
            ).run("只需要分析本地文件")

            self.assertTrue(result.blocked)
            self.assertEqual(result.normalized_request.input_mode, "provided_evidence")
            self.assertEqual(
                result.normalized_request.metadata["normalizer"],
                "llm_intake_plus_normalize_request",
            )
            self.assertEqual(result.normalized_request.missing_fields, ("provided_evidence.files",))

    def test_orchestrator_records_visible_fallback_metadata_when_llm_json_invalid(self) -> None:
        class FakeIntakeRuntime:
            def invoke(self, payload):
                return {"messages": [SimpleNamespace(type="ai", content="not json")]}

        class FakeFactory:
            def create_intake_runtime(self, skill_text: str):
                return FakeIntakeRuntime()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = Orchestrator(
                repo_root=Path.cwd(),
                artifact_store=ArtifactStore(Path(tmpdir) / "artifacts"),
                telemetry=TelemetryRecorder(),
                deepagents_runtime_factory=FakeFactory(),
            ).run("MySQL latency")

            self.assertTrue(result.blocked)
            self.assertTrue(result.normalized_request.metadata["llm_intake_failed"])
            self.assertIn("fallback_reason", result.normalized_request.metadata)

    def test_orchestrator_keeps_chinese_secret_out_of_llm_artifact_and_telemetry(self) -> None:
        class FakeIntakeRuntime:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, payload):
                self.calls.append(payload)
                return {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": json.dumps({
                                "target_agent": "mysql_analyzer",
                                "target_domain": "mysql",
                                "task_type": "general_question",
                                "routing_confidence": 0.91,
                                "input_mode": "provided_evidence",
                                "target": None,
                                "ssh_target": None,
                                "provided_evidence": {
                                    "mode": "pasted_text",
                                    "files": [],
                                    "pasted_text": True,
                                    "description": "用户提供了文本",
                                },
                                "collection_policy": {
                                    "allow_live_collection": False,
                                    "allow_mysql_login": False,
                                    "allow_ssh": False,
                                    "allow_metrics_query": False,
                                },
                                "event": None,
                                "missing_fields": [],
                            }),
                        }
                    ]
                }

        class FakeFactory:
            def __init__(self) -> None:
                self.runtime = FakeIntakeRuntime()

            def create_intake_runtime(self, skill_text: str):
                return self.runtime

        with tempfile.TemporaryDirectory() as tmpdir:
            factory = FakeFactory()
            result = Orchestrator(
                repo_root=Path.cwd(),
                artifact_store=ArtifactStore(Path(tmpdir) / "artifacts"),
                telemetry=TelemetryRecorder(),
                deepagents_runtime_factory=factory,
            ).run("MySQL密码是Root")

            llm_payload = json.dumps(factory.runtime.calls, ensure_ascii=False)
            artifact_text = "\n".join(
                artifact.path.read_text(encoding="utf-8") for artifact in result.artifacts
            )
            telemetry_text = "\n".join(
                json.dumps(event.to_dict(), ensure_ascii=False) for event in result.telemetry
            )
            self.assertNotIn("Root", llm_payload)
            self.assertNotIn("Root", artifact_text)
            self.assertNotIn("Root", telemetry_text)
            self.assertIn("<SECRET_REF:chinese_password_", artifact_text)

    def test_orchestrator_blocks_and_writes_blocked_artifact_for_missing_fields(self) -> None:
        class FakeIntakeRuntime:
            def invoke(self, payload):
                return {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": json.dumps({
                                "target_agent": "mysql_analyzer",
                                "target_domain": "mysql",
                                "task_type": "incident_analysis",
                                "routing_confidence": 0.7,
                                "target": None,
                                "event": None,
                                "missing_fields": ["target.host", "target.username"],
                            }),
                        }
                    ]
                }

        class FakeFactory:
            def create_intake_runtime(self, skill_text: str):
                return FakeIntakeRuntime()

        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = Orchestrator(
                repo_root=Path.cwd(),
                artifact_store=ArtifactStore(Path(tmpdir) / "artifacts"),
                telemetry=TelemetryRecorder(),
                deepagents_runtime_factory=FakeFactory(),
            )

            result = orchestrator.run("请分析数据库故障")

            self.assertTrue(result.blocked)
            self.assertIsNone(result.route_decision)
            self.assertTrue(
                any("target.host" in i for i in result.blocking_issues)
            )
            # blocked-request artifact must exist
            blocked_artifacts = [a for a in result.artifacts if a.kind == "BlockedRequest"]
            self.assertEqual(len(blocked_artifacts), 1)
            self.assertTrue(blocked_artifacts[0].path.exists())

    def test_orchestrator_falls_back_to_deterministic_when_llm_returns_no_json(self) -> None:
        class FakeIntakeRuntime:
            def invoke(self, payload):
                return {"messages": [{"role": "assistant", "content": "I cannot help."}]}

        class FakeFactory:
            def create_intake_runtime(self, skill_text: str):
                return FakeIntakeRuntime()

        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = Orchestrator(
                repo_root=Path.cwd(),
                artifact_store=ArtifactStore(Path(tmpdir) / "artifacts"),
                telemetry=TelemetryRecorder(),
                deepagents_runtime_factory=FakeFactory(),
            )

            result = orchestrator.run("MySQL latency")

            # No LLM JSON → deterministic fallback → missing fields → blocked
            self.assertTrue(result.blocked)
            event_types = [e.event_type for e in result.telemetry]
            self.assertIn("intake_json_parse_failed", event_types)

    def test_orchestrator_can_skip_llm_invocation_for_local_smoke_tests(self) -> None:
        class FakeIntakeRuntime:
            def invoke(self, payload):
                raise AssertionError("LLM runtime should not be invoked")

        class FakeDeepAgentsRuntimeFactory:
            def create_intake_runtime(self, skill_text: str):
                return FakeIntakeRuntime()

        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = Orchestrator(
                repo_root=Path.cwd(),
                artifact_store=ArtifactStore(Path(tmpdir) / "artifacts"),
                telemetry=TelemetryRecorder(),
                deepagents_runtime_factory=FakeDeepAgentsRuntimeFactory(),
                invoke_llm=False,
            )

            result = orchestrator.run("MySQL connection spike on prod-db-1")

            self.assertTrue(result.deepagents_runtime_ready)
            # Without LLM, deterministic fallback has missing fields → blocked
            self.assertTrue(result.blocked)

    def test_connection_uri_redaction_preserves_host_and_user(self) -> None:
        raw = "mysql://root:Root@192.168.1.1:3306"
        result = Redactor().redact(raw)

        self.assertNotIn("Root", result.redacted_text)
        self.assertIn("<SECRET_REF:uri_password_", result.redacted_text)
        self.assertIn("root:", result.redacted_text)
        self.assertIn("@192.168.1.1:3306", result.redacted_text)


def _write_config_file() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    path = Path(tmpdir.name) / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "model:",
                "  provider_kind: openai_compatible",
                "  model_name: qwen3.5-flash",
                "  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1",
                "  api_key: sk-test",
                "  reasoning_effort: high",
                "  extra_body:",
                "    thinking:",
                "      type: enabled",
                "agent:",
                "  tool_calling: true",
                "  tool_calling_thinking_type: disabled",
                "runtime:",
                "  artifact_dir: .dbkit/test-artifacts",
                "  invoke_llm: true",
            ]
        ),
        encoding="utf-8",
    )
    _TEMP_CONFIGS.append(tmpdir)
    return path


def _write_config_file_without_agent_section() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    path = Path(tmpdir.name) / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "model:",
                "  provider_kind: openai_compatible",
                "  model_name: deepseek-v4-pro",
                "  base_url: https://api.deepseek.com",
                "  api_key: sk-test",
                "  reasoning_effort: high",
                "  extra_body:",
                "    thinking:",
                "      type: enabled",
                "runtime:",
                "  artifact_dir: .dbkit/test-artifacts",
                "  invoke_llm: true",
            ]
        ),
        encoding="utf-8",
    )
    _TEMP_CONFIGS.append(tmpdir)
    return path


_TEMP_CONFIGS: list[tempfile.TemporaryDirectory[str]] = []


if __name__ == "__main__":
    unittest.main()
