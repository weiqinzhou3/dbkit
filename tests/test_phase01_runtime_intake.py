import json
import tempfile
import unittest
from dataclasses import replace
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

    def test_redactor_removes_english_chinese_headers_and_database_uri_secrets(self) -> None:
        raw = (
            "password=abc123 passwd: def456 pwd：中文密码 token=tok-1 "
            "secret: sec-value api_key=api-value Authorization: Bearer auth-token "
            "mysql://root:rootpass@db:3306/app redis://:redispass@cache:6379 "
            "mongodb://u:mongopass@mongo/db postgres://pg:pgpass@pg/db"
        )

        result = Redactor().redact(raw)

        for secret in (
            "abc123",
            "def456",
            "中文密码",
            "tok-1",
            "sec-value",
            "api-value",
            "auth-token",
            "rootpass",
            "redispass",
            "mongopass",
            "pgpass",
        ):
            self.assertNotIn(secret, result.redacted_text)
        self.assertIn("<REDACTED>", result.redacted_text)
        self.assertGreater(result.raw_bytes, result.filtered_bytes)
        self.assertLess(result.compression_ratio, 1.0)

    def test_normalize_request_creates_phase01_request(self) -> None:
        request = normalize_request("MySQL connection spike on prod-db-1")

        self.assertTrue(request.request_id.startswith("req_"))
        self.assertEqual(request.original_input, "MySQL connection spike on prod-db-1")
        self.assertEqual(request.redacted_input, "MySQL connection spike on prod-db-1")
        self.assertEqual(request.target_domain, "mysql")
        self.assertEqual(request.requested_capability, "runtime_intake")
        self.assertEqual(request.phase, "phase-01")
        self.assertIn("time_window", request.missing_fields)

    def test_intake_agent_loads_skill_from_repo(self) -> None:
        agent = IntakeAgent.from_repo_root(Path.cwd())

        self.assertEqual(agent.name, "intake")
        self.assertIn("Intake Skill", agent.skill_text)
        self.assertIn("normalized request generation", agent.skill_text)

    def test_guardrails_validate_normalized_request(self) -> None:
        request = normalize_request("MySQL latency alert")
        guardrails = Guardrails()

        validated = guardrails.validate_normalized_request(request)

        self.assertIs(validated, request)

    def test_router_selects_mysql_analyzer_agent_name(self) -> None:
        request = normalize_request("MySQL replication lag")

        route = Router().route(request)

        self.assertEqual(route.target_agent_name, "mysql_analyzer")
        self.assertEqual(route.target_domain, "mysql")
        self.assertEqual(route.phase, "phase-01")

    def test_artifact_store_persists_normalized_request_as_business_output(self) -> None:
        request = normalize_request("MySQL CPU alert")

        with tempfile.TemporaryDirectory() as tmpdir:
            record = ArtifactStore(Path(tmpdir)).persist_request(request)

            self.assertEqual(record.kind, "NormalizedRequest")
            self.assertTrue(record.path.exists())
            payload = json.loads(record.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["request_id"], request.request_id)
            self.assertEqual(payload["target_domain"], "mysql")

    def test_telemetry_recorder_emits_runtime_events_not_artifacts(self) -> None:
        recorder = TelemetryRecorder()

        event = recorder.emit(
            event_type="stage_started",
            stage="intake",
            message="Intake stage started",
            attributes={"phase": "phase-01"},
        )

        self.assertEqual(event.event_type, "stage_started")
        self.assertEqual(event.stage, "intake")
        self.assertEqual(event.attributes["phase"], "phase-01")
        self.assertEqual(recorder.events, [event])

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

        runtime = factory.create_intake_runtime(skill_text="Intake Skill")

        self.assertIsNotNone(runtime)
        self.assertIs(calls[0]["model"], model)
        self.assertEqual(len(calls[0]["tools"]), 1)
        self.assertEqual(calls[0]["tools"][0].__name__, "normalize_request_tool")
        self.assertIn("Intake Skill", calls[0]["system_prompt"])
        self.assertEqual(calls[0]["name"], "dbkit-intake")

    def test_orchestrator_runs_phase01_flow_without_dba_analysis(self) -> None:
        class FakeIntakeRuntime:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, payload):
                self.calls.append(payload)
                return {"messages": []}

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
                "MySQL connection spike password=abc token=secret-token"
            )

            self.assertTrue(result.deepagents_runtime_ready)
            self.assertEqual(result.route_decision.target_agent_name, "mysql_analyzer")
            self.assertEqual(len(result.artifacts), 1)
            self.assertTrue(result.artifacts[0].path.exists())
            self.assertIn("Intake Skill", factory.skill_text)
            self.assertEqual(len(factory.runtime.calls), 1)
            self.assertIn(
                "MySQL connection spike",
                factory.runtime.calls[0]["messages"][0]["content"],
            )
            self.assertNotIn("abc", factory.runtime.calls[0]["messages"][0]["content"])
            self.assertNotIn(
                "secret-token",
                factory.runtime.calls[0]["messages"][0]["content"],
            )
            cost_events = [
                event for event in result.telemetry if event.event_type == "runtime_cost"
            ]
            self.assertEqual(
                [event.attributes["stage"] for event in cost_events],
                ["redactor", "normalize_request"],
            )
            self.assertEqual(cost_events[0].attributes["stage"], "redactor")
            self.assertIn("raw_bytes", cost_events[0].attributes)
            self.assertIn("filtered_bytes", cost_events[0].attributes)
            self.assertIn("compression_ratio", cost_events[0].attributes)
            self.assertIn("estimated_tokens", cost_events[0].attributes)
            self.assertIn("tool_latency_ms", cost_events[0].attributes)
            self.assertEqual(
                [
                    event.event_type
                    for event in result.telemetry
                    if event.event_type != "runtime_cost"
                ],
                [
                    "stage_started",
                    "stage_completed",
                    "stage_started",
                    "stage_completed",
                    "stage_started",
                    "stage_completed",
                    "stage_started",
                    "stage_completed",
                ],
            )

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
