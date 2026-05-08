import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dbkit.config import load_app_config
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.guardrails import Guardrails
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.orchestrator import Orchestrator
from dbkit.runtime.time_context import FixedTimeProvider
from dbkit.runtime.user_message import render_user_message, validate_user_message


class Phase012IntakeUxTest(unittest.TestCase):
    def test_config_reads_phase012_runtime_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "model:",
                        "  provider_kind: openai_compatible",
                        "  model_name: qwen3.5-flash",
                        "  base_url: https://example.test/v1",
                        "  api_key: sk-test",
                        "runtime:",
                        "  artifact_dir: .dbkit/test-artifacts",
                        "  timezone: Asia/Shanghai",
                        "  locale: zh-CN",
                        "  interactive: true",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_app_config(config_path)

            self.assertEqual(config.runtime.timezone, "Asia/Shanghai")
            self.assertEqual(config.runtime.locale, "zh-CN")
            self.assertTrue(config.runtime.interactive)

    def test_fixed_time_provider_returns_runtime_context(self) -> None:
        provider = FixedTimeProvider(
            current_datetime=datetime.fromisoformat("2026-05-08T10:00:00+08:00"),
            timezone="Asia/Shanghai",
            locale="zh-CN",
        )

        context = provider.runtime_context()

        self.assertEqual(context["current_datetime"], "2026-05-08T10:00:00+08:00")
        self.assertEqual(context["timezone"], "Asia/Shanghai")
        self.assertEqual(context["locale"], "zh-CN")

    def test_orchestrator_injects_runtime_context_into_intake_agent(self) -> None:
        class FakeIntakeRuntime:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, payload):
                self.calls.append(payload)
                runtime_context = payload["runtime_context"]
                return _assistant_json(
                    {
                        "target_agent": "mysql_analyzer",
                        "target_domain": "mysql",
                        "task_type": "alert_analysis",
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
                        "event": {
                            "event_time": runtime_context["current_datetime"].replace(
                                "10:00:00", "17:00:00"
                            ),
                            "time_window": {
                                "before": "6h",
                                "after": "1h",
                                "source": "skill_default_from_event_time",
                            },
                            "symptoms": ["high_cpu"],
                        },
                        "missing_fields": [],
                    }
                )

        runtime = FakeIntakeRuntime()
        result = _orchestrator_with_runtime(runtime).run(
            "今天17:00触发 mysql cpu usage > 85%"
        )

        self.assertFalse(result.blocked)
        self.assertEqual(
            runtime.calls[0]["runtime_context"]["current_datetime"],
            "2026-05-08T10:00:00+08:00",
        )
        initial_content = runtime.calls[0]["messages"][0]["content"]
        self.assertIn('"runtime_context"', initial_content)
        self.assertIn('"current_datetime": "2026-05-08T10:00:00+08:00"', initial_content)
        self.assertIn('"timezone": "Asia/Shanghai"', initial_content)
        self.assertIn('"locale": "zh-CN"', initial_content)
        self.assertIn("今天17:00触发 mysql cpu usage > 85%", initial_content)
        self.assertEqual(
            result.normalized_request.event["event_time"],
            "2026-05-08T17:00:00+08:00",
        )
        self.assertIn(
            "runtime_context_injected",
            [event.event_type for event in result.telemetry],
        )
        self.assertIn(
            "relative_time_resolved",
            [event.event_type for event in result.telemetry],
        )

    def test_valid_user_message_is_rendered_and_persisted_for_blocked_request(self) -> None:
        class FakeIntakeRuntime:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, payload):
                self.calls.append(payload)
                if payload.get("mode") == "blocked_message":
                    return _assistant_json(
                        {
                            "user_message": {
                                "summary": "当前请求需要补充 MySQL 主机地址后才能继续。",
                                "missing_items": [
                                    {
                                        "field": "target.host",
                                        "label": "MySQL 主机地址",
                                        "reason": "你希望 DBKit 直接连接 MySQL 进行 live collection，但没有提供连接目标。",
                                        "example": "192.168.1.10",
                                    }
                                ],
                                "retry_example": "请帮我分析 192.168.1.10 的 MySQL，账号 root。",
                            }
                        }
                    )
                return _assistant_json(
                    {
                        "target_agent": "mysql_analyzer",
                        "target_domain": "mysql",
                        "task_type": "general_question",
                        "routing_confidence": 0.91,
                        "input_mode": "live_collection",
                        "target": None,
                        "missing_fields": ["target.host"],
                    }
                )

        runtime = FakeIntakeRuntime()
        result = _orchestrator_with_runtime(runtime).run("请连接这个 MySQL 看一下")

        self.assertTrue(result.blocked)
        self.assertIn("MySQL 主机地址", result.rendered_user_message)
        self.assertEqual(result.user_message["missing_items"][0]["field"], "target.host")
        blocked_artifact = [a for a in result.artifacts if a.kind == "BlockedRequest"][0]
        payload = json.loads(blocked_artifact.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["user_message"]["summary"], "当前请求需要补充 MySQL 主机地址后才能继续。")
        self.assertTrue(payload["supplement_required"])
        self.assertIn("request_blocked_message_rendered", [e.event_type for e in result.telemetry])
        blocked_content = runtime.calls[1]["messages"][0]["content"]
        self.assertIn('"sanitized_user_prompt"', blocked_content)
        self.assertIn('"normalized_request"', blocked_content)
        self.assertIn('"blocking_issues"', blocked_content)
        self.assertIn('"missing_fields"', blocked_content)
        self.assertIn('"input_mode"', blocked_content)
        self.assertIn('"collection_policy"', blocked_content)
        self.assertIn('"runtime_context"', blocked_content)

    def test_today_alert_blocked_request_only_exposes_target_host_missing(self) -> None:
        case = self

        class FakeIntakeRuntime:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, payload):
                self.calls.append(payload)
                content = payload["messages"][0]["content"]
                if payload.get("mode") == "blocked_message":
                    case.assertIn('"sanitized_user_prompt"', content)
                    case.assertIn('"blocking_issues"', content)
                    case.assertIn('"missing_fields"', content)
                    case.assertIn("target.host", content)
                    return _assistant_json(
                        {
                            "user_message": {
                                "summary": "当前请求需要补充 MySQL 主机地址后才能继续。",
                                "missing_items": [
                                    {
                                        "field": "target.host",
                                        "label": "MySQL 主机地址",
                                        "reason": "你提供了账号、密码和告警时间，但没有提供要连接的 MySQL 主机地址。",
                                        "example": "192.168.1.10",
                                    }
                                ],
                                "retry_example": "请补充：MySQL 主机是 192.168.1.10",
                            }
                        }
                    )
                case.assertIn('"current_datetime": "2026-05-08T10:00:00+08:00"', content)
                case.assertNotIn("密码是root", content)
                return _assistant_json(
                    {
                        "target_agent": "mysql_analyzer",
                        "target_domain": "mysql",
                        "task_type": "alert_analysis",
                        "routing_confidence": 0.93,
                        "input_mode": "live_collection",
                        "target": {
                            "type": "mysql",
                            "host": None,
                            "port": 3306,
                            "username": "root",
                            "password_ref": "<SECRET_REF:chinese_password_001>",
                        },
                        "ssh_target": None,
                        "collection_policy": {
                            "allow_live_collection": True,
                            "allow_mysql_login": True,
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
                        "missing_fields": [
                            "runtime_context.current_datetime",
                            "event.event_time",
                            "target.host",
                        ],
                    }
                )

        runtime = FakeIntakeRuntime()
        result = _orchestrator_with_runtime(runtime).run(
            "请帮我分析这个 MySQL，今天17:00触发 mysql cpu usage > 85%，MySQL的账号是root，密码是root。"
        )

        self.assertTrue(result.blocked)
        self.assertEqual(result.normalized_request.event["event_time"], "2026-05-08T17:00:00+08:00")
        self.assertIn("time_window", result.normalized_request.event)
        self.assertEqual(result.normalized_request.missing_fields, ("target.host",))
        self.assertNotIn("runtime_context.current_datetime", result.blocking_issues)
        self.assertNotIn("event.event_time", result.normalized_request.missing_fields)
        self.assertIn("MySQL 主机地址", result.rendered_user_message)
        artifact_text = "\n".join(
            artifact.path.read_text(encoding="utf-8") for artifact in result.artifacts
        )
        self.assertNotIn("密码是root", artifact_text)
        event_types = [event.event_type for event in result.telemetry]
        self.assertIn("runtime_context_injected", event_types)
        self.assertIn("request_blocked_message_rendered", event_types)

    def test_runtime_context_missing_fails_as_runtime_error(self) -> None:
        class BrokenTimeProvider:
            def runtime_context(self):
                return {"timezone": "Asia/Shanghai", "locale": "zh-CN"}

        with self.assertRaisesRegex(RuntimeError, "runtime_context.current_datetime"):
            _orchestrator_with_runtime(
                runtime=object(),
                time_provider=BrokenTimeProvider(),
            ).run("今天17:00触发告警")

    def test_invalid_user_message_uses_deterministic_fallback(self) -> None:
        class FakeIntakeRuntime:
            def invoke(self, payload):
                if payload.get("mode") == "blocked_message":
                    return _assistant_json({"user_message": {"summary": "```bad```"}})
                return _assistant_json(
                    {
                        "target_agent": "mysql_analyzer",
                        "target_domain": "mysql",
                        "task_type": "general_question",
                        "routing_confidence": 0.91,
                        "input_mode": "live_collection",
                        "target": None,
                        "missing_fields": ["target.host"],
                    }
                )

        result = _orchestrator_with_runtime(FakeIntakeRuntime()).run("请连接这个 MySQL")

        self.assertTrue(result.blocked)
        self.assertIn("target.host", result.rendered_user_message)
        self.assertIn(
            "request_blocked_message_fallback_used",
            [event.event_type for event in result.telemetry],
        )

    def test_interactive_supplement_patch_merges_and_reruns_guardrails(self) -> None:
        class FakeIntakeRuntime:
            def invoke(self, payload):
                if payload.get("mode") == "blocked_message":
                    return _assistant_json(
                        {
                            "user_message": {
                                "summary": "当前请求需要补充 MySQL 主机地址。",
                                "missing_items": [
                                    {
                                        "field": "target.host",
                                        "label": "MySQL 主机地址",
                                        "reason": "live collection 需要连接目标。",
                                        "example": "192.168.1.10",
                                    }
                                ],
                                "retry_example": "MySQL 是 192.168.1.10",
                            }
                        }
                    )
                if payload.get("mode") == "supplement_patch":
                    return _assistant_json(
                        {"supplement_patch": {"target": {"host": "192.168.1.10"}}}
                    )
                return _assistant_json(
                    {
                        "target_agent": "mysql_analyzer",
                        "target_domain": "mysql",
                        "task_type": "general_question",
                        "routing_confidence": 0.91,
                        "input_mode": "live_collection",
                        "target": {
                            "type": "mysql",
                            "host": None,
                            "port": 3306,
                            "username": "root",
                            "password_ref": "<SECRET_REF:password_001>",
                        },
                        "collection_policy": {
                            "allow_live_collection": True,
                            "allow_mysql_login": True,
                            "allow_ssh": False,
                            "allow_metrics_query": False,
                        },
                        "missing_fields": ["target.host"],
                    }
                )

        result = _orchestrator_with_runtime(FakeIntakeRuntime()).run(
            "请连接这个 MySQL，用户 root，密码是Root@123",
            interactive=True,
            supplement_reader=lambda: "MySQL 是 192.168.1.10",
        )

        self.assertFalse(result.blocked)
        self.assertEqual(result.normalized_request.target["host"], "192.168.1.10")
        self.assertNotIn("target.host", result.normalized_request.missing_fields)
        self.assertIn(
            "interactive_supplement_patch_merged",
            [event.event_type for event in result.telemetry],
        )

    def test_secret_supplement_is_redacted_before_patch_agent_call(self) -> None:
        case = self

        class FakeIntakeRuntime:
            def __init__(self) -> None:
                self.calls = []

            def invoke(self, payload):
                self.calls.append(payload)
                if payload.get("mode") == "blocked_message":
                    return _assistant_json(
                        {
                            "user_message": {
                                "summary": "当前请求需要补充密码。",
                                "missing_items": [
                                    {
                                        "field": "target.password_ref",
                                        "label": "MySQL 密码",
                                        "reason": "live collection 需要凭据引用。",
                                        "example": "密码是 <SECRET_REF:password_001>",
                                    }
                                ],
                                "retry_example": "密码是 <SECRET_REF:password_001>",
                            }
                        }
                    )
                if payload.get("mode") == "supplement_patch":
                    supplement_text = payload["messages"][0]["content"]
                    case.assertNotIn("Root@123", supplement_text)
                    return _assistant_json(
                        {
                            "supplement_patch": {
                                "target": {"password_ref": "<SECRET_REF:chinese_password_001>"}
                            }
                        }
                    )
                return _assistant_json(
                    {
                        "target_agent": "mysql_analyzer",
                        "target_domain": "mysql",
                        "task_type": "general_question",
                        "routing_confidence": 0.91,
                        "input_mode": "live_collection",
                        "target": {
                            "type": "mysql",
                            "host": "192.168.1.10",
                            "port": 3306,
                            "username": "root",
                        },
                        "collection_policy": {
                            "allow_live_collection": True,
                            "allow_mysql_login": True,
                            "allow_ssh": False,
                            "allow_metrics_query": False,
                        },
                        "missing_fields": ["target.password_ref"],
                    }
                )

        runtime = FakeIntakeRuntime()
        result = _orchestrator_with_runtime(runtime).run(
            "请连接 MySQL 192.168.1.10，用户 root",
            interactive=True,
            supplement_reader=lambda: "密码是 Root@123",
        )

        text = "\n".join(
            json.dumps(event.to_dict(), ensure_ascii=False) for event in result.telemetry
        )
        artifacts = "\n".join(a.path.read_text(encoding="utf-8") for a in result.artifacts)
        self.assertFalse(result.blocked)
        self.assertNotIn("Root@123", text)
        self.assertNotIn("Root@123", artifacts)
        self.assertIn("<SECRET_REF:chinese_password_", result.normalized_request.target["password_ref"])

    def test_patch_guardrails_reject_system_agent_target(self) -> None:
        result = Guardrails().validate_supplement_patch(
            {"target_agent": "validation_agent"},
            base_request=_complete_live_request(),
        )

        self.assertFalse(result.passed)
        self.assertTrue(any("target_agent" in issue for issue in result.blocking_issues))

    def test_user_message_validator_rejects_fields_not_blocked(self) -> None:
        result = validate_user_message(
            {
                "summary": "需要补充信息。",
                "missing_items": [
                    {
                        "field": "ssh_target.host",
                        "label": "SSH 主机",
                        "reason": "需要 SSH。",
                        "example": "192.168.1.10",
                    }
                ],
                "retry_example": "补充 SSH 主机",
            },
            blocking_issues=("missing required field: target.host",),
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "target.host",
            render_user_message(None, ("missing required field: target.host",)),
        )


def _assistant_json(payload: dict) -> dict:
    return {"messages": [SimpleNamespace(type="ai", content=json.dumps(payload, ensure_ascii=False))]}


def _orchestrator_with_runtime(
    runtime: object,
    time_provider: object | None = None,
) -> Orchestrator:
    class FakeFactory:
        def create_intake_runtime(self, skill_text: str):
            return runtime

    tmpdir = tempfile.TemporaryDirectory()
    _TEMP_DIRS.append(tmpdir)
    return Orchestrator(
        repo_root=Path.cwd(),
        artifact_store=ArtifactStore(Path(tmpdir.name) / "artifacts"),
        telemetry=TelemetryRecorder(),
        deepagents_runtime_factory=FakeFactory(),
        time_provider=time_provider or FixedTimeProvider(
            current_datetime=datetime.fromisoformat("2026-05-08T10:00:00+08:00"),
            timezone="Asia/Shanghai",
            locale="zh-CN",
        ),
    )


def _complete_live_request():
    from dbkit.tools.normalize_request import normalize_request

    return normalize_request(
        "MySQL",
        llm_json={
            "target_agent": "mysql_analyzer",
            "target_domain": "mysql",
            "task_type": "general_question",
            "routing_confidence": 0.91,
            "input_mode": "live_collection",
            "target": {
                "type": "mysql",
                "host": "192.168.1.10",
                "port": 3306,
                "username": "root",
                "password_ref": "<SECRET_REF:password_001>",
            },
            "collection_policy": {
                "allow_live_collection": True,
                "allow_mysql_login": True,
                "allow_ssh": False,
                "allow_metrics_query": False,
            },
            "missing_fields": [],
        },
    )


_TEMP_DIRS: list[tempfile.TemporaryDirectory[str]] = []


if __name__ == "__main__":
    unittest.main()
