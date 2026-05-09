import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class MainEntrypointTest(unittest.TestCase):
    def test_main_entrypoint_blocks_when_target_info_missing(self) -> None:
        """Without LLM and without target info, CLI must block and exit non-zero."""
        import main

        output = io.StringIO()
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
                        "runtime:",
                        f"  artifact_dir: {tmpdir}/artifacts",
                        "  invoke_llm: false",
                    ]
                ),
                encoding="utf-8",
            )

            with redirect_stdout(output):
                exit_code = main.main(
                    ["--config", str(config_path), "MySQL connection spike"]
                )

        # Phase-01.1: missing target info → blocked → exit 1
        self.assertEqual(exit_code, 1)
        self.assertIn("DBKit", output.getvalue())
        self.assertIn("status=blocked", output.getvalue())
        self.assertIn("missing_fields=", output.getvalue())
        self.assertIn("artifact=", output.getvalue())

    def test_phase02_parse_failure_prints_blocked_reason(self) -> None:
        import main
        from dbkit.config import AgentConfig, AppConfig, ModelConfig, ProviderKind, RuntimeConfig
        from dbkit.schemas.evidence import EvidencePipelineResult
        from dbkit.schemas.runtime import ArtifactRecord, NormalizedRequest, RouteDecision, RuntimeResult

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = root / "failed.json"
            artifact_path.write_text("{}", encoding="utf-8")
            normalized = NormalizedRequest(
                request_id="req_cli_parse_failed",
                original_input="connect mysql",
                redacted_input="connect mysql",
                target_domain="mysql",
                requested_capability="alert_analysis",
                missing_fields=(),
                phase="phase-02",
                target_agent="mysql_analyzer",
                task_type="alert_analysis",
                input_mode="live_collection",
            )

            class FakeOrchestrator:
                def __init__(self, **_kwargs):
                    pass

                def run(self, *_args, **_kwargs):
                    return RuntimeResult(
                        normalized_request=normalized,
                        route_decision=RouteDecision(
                            target_agent_name="mysql_analyzer",
                            target_domain="mysql",
                            phase="phase-02",
                            reason="test",
                        ),
                        artifacts=(),
                        telemetry=(),
                        deepagents_runtime_ready=True,
                        blocked=False,
                    )

            class FakeAnalyzerAgent:
                skill_text = "MYSQL_SKILL"

                @classmethod
                def from_skills_dir(cls, _skills_dir):
                    return cls()

            class FakeRuntimeFactory:
                def __init__(self, **_kwargs):
                    pass

                def create_mysql_analyzer_runtime(self, _skill_text, **_kwargs):
                    return object()

            class FakeEvidencePipeline:
                def __init__(self, **_kwargs):
                    pass

                def run(self, _request):
                    return EvidencePipelineResult(
                        request_id=normalized.request_id,
                        phase="phase-02",
                        status="evidence_request_parse_failed",
                        evidence_request=None,
                        collection_plan=None,
                        raw_evidence=(),
                        artifacts=(ArtifactRecord(kind="EvidenceRequestFailed", path=artifact_path),),
                        telemetry=(),
                        blocking_issues=("evidence_request_parse_failed",),
                    )

            config = AppConfig(
                model=ModelConfig(
                    provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                    model_name="test-model",
                    base_url="https://example.invalid/v1",
                    api_key="sk-test",
                ),
                agent=AgentConfig(),
                runtime=RuntimeConfig(
                    artifact_dir=root / "artifacts",
                    repo_dir=Path("."),
                    workspace_dir=root / "workspace",
                    skills_dir=Path("skills"),
                    agents_dir=Path("agents"),
                ),
            )

            with (
                patch("dbkit.cli.load_app_config", return_value=config),
                patch("dbkit.cli.build_agent_model", return_value=object()),
                patch("dbkit.cli.DeepAgentsRuntimeFactory", FakeRuntimeFactory),
                patch("dbkit.cli.Orchestrator", FakeOrchestrator),
                patch("dbkit.cli.MySQLAnalyzerAgent", FakeAnalyzerAgent),
                patch("dbkit.cli.EvidencePipeline", FakeEvidencePipeline),
                redirect_stdout(output),
            ):
                exit_code = main.main(["--config", str(root / "config.yaml"), "connect mysql"])

        self.assertEqual(exit_code, 1)
        self.assertIn("phase=phase-02", output.getvalue())
        self.assertIn("status=blocked", output.getvalue())
        self.assertIn("reason=evidence_request_parse_failed", output.getvalue())
        self.assertIn("artifact=", output.getvalue())

    def test_phase021_collection_failed_exits_nonzero(self) -> None:
        import main
        from dbkit.config import AgentConfig, AppConfig, ModelConfig, ProviderKind, RuntimeConfig
        from dbkit.schemas.evidence import EvidencePipelineResult
        from dbkit.schemas.runtime import NormalizedRequest, RouteDecision, RuntimeResult

        output = io.StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            normalized = NormalizedRequest(
                request_id="req_cli_collection_failed",
                original_input="connect mysql",
                redacted_input="connect mysql",
                target_domain="mysql",
                requested_capability="alert_analysis",
                missing_fields=(),
                phase="phase-02.1",
                target_agent="mysql_analyzer",
                task_type="alert_analysis",
                input_mode="live_collection",
            )

            class FakeOrchestrator:
                def __init__(self, **_kwargs):
                    pass

                def run(self, *_args, **_kwargs):
                    return RuntimeResult(
                        normalized_request=normalized,
                        route_decision=RouteDecision(
                            target_agent_name="mysql_analyzer",
                            target_domain="mysql",
                            phase="phase-02.1",
                            reason="test",
                        ),
                        artifacts=(),
                        telemetry=(),
                        deepagents_runtime_ready=True,
                        blocked=False,
                    )

            class FakeAnalyzerAgent:
                skill_text = "MYSQL_SKILL"

                @classmethod
                def from_skills_dir(cls, _skills_dir):
                    return cls()

            class FakeRuntimeFactory:
                def __init__(self, **_kwargs):
                    pass

                def create_mysql_analyzer_runtime(self, _skill_text, **_kwargs):
                    return object()

            class FakeEvidencePipeline:
                def __init__(self, **_kwargs):
                    pass

                def run(self, _request):
                    return EvidencePipelineResult(
                        request_id=normalized.request_id,
                        phase="phase-02.1",
                        status="collection_failed",
                        evidence_request=None,
                        collection_plan=None,
                        raw_evidence=(),
                        artifacts=(),
                        telemetry=(),
                    )

            config = AppConfig(
                model=ModelConfig(
                    provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                    model_name="test-model",
                    base_url="https://example.invalid/v1",
                    api_key="sk-test",
                ),
                agent=AgentConfig(),
                runtime=RuntimeConfig(
                    artifact_dir=root / "artifacts",
                    repo_dir=Path("."),
                    workspace_dir=root / "workspace",
                    skills_dir=Path("skills"),
                    agents_dir=Path("agents"),
                ),
            )

            with (
                patch("dbkit.cli.load_app_config", return_value=config),
                patch("dbkit.cli.build_agent_model", return_value=object()),
                patch("dbkit.cli.DeepAgentsRuntimeFactory", FakeRuntimeFactory),
                patch("dbkit.cli.Orchestrator", FakeOrchestrator),
                patch("dbkit.cli.MySQLAnalyzerAgent", FakeAnalyzerAgent),
                patch("dbkit.cli.EvidencePipeline", FakeEvidencePipeline),
                redirect_stdout(output),
            ):
                exit_code = main.main(["--config", str(root / "config.yaml"), "connect mysql"])

        self.assertEqual(exit_code, 1)
        self.assertIn("phase=phase-02.1", output.getvalue())
        self.assertIn("status=collection_failed", output.getvalue())

    def test_phase021_missing_dependencies_prints_install_hint(self) -> None:
        import main
        from dbkit.config import AgentConfig, AppConfig, ModelConfig, ProviderKind, RuntimeConfig
        from dbkit.schemas.evidence import EvidencePipelineResult
        from dbkit.schemas.runtime import ArtifactRecord, NormalizedRequest, RouteDecision, RuntimeResult

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = root / "collection-blocked.json"
            artifact_path.write_text("{}", encoding="utf-8")
            normalized = NormalizedRequest(
                request_id="req_cli_missing_deps",
                original_input="connect mysql",
                redacted_input="connect mysql",
                target_domain="mysql",
                requested_capability="alert_analysis",
                missing_fields=(),
                phase="phase-02.1",
                target_agent="mysql_analyzer",
                task_type="alert_analysis",
                input_mode="live_collection",
            )

            class FakeOrchestrator:
                def __init__(self, **_kwargs):
                    pass

                def run(self, *_args, **_kwargs):
                    return RuntimeResult(
                        normalized_request=normalized,
                        route_decision=RouteDecision(
                            target_agent_name="mysql_analyzer",
                            target_domain="mysql",
                            phase="phase-02.1",
                            reason="test",
                        ),
                        artifacts=(),
                        telemetry=(),
                        deepagents_runtime_ready=True,
                        blocked=False,
                    )

            class FakeAnalyzerAgent:
                skill_text = "MYSQL_SKILL"

                @classmethod
                def from_skills_dir(cls, _skills_dir):
                    return cls()

            class FakeRuntimeFactory:
                def __init__(self, **_kwargs):
                    pass

                def create_mysql_analyzer_runtime(self, _skill_text, **_kwargs):
                    return object()

            class FakeEvidencePipeline:
                def __init__(self, **_kwargs):
                    pass

                def run(self, _request):
                    return EvidencePipelineResult(
                        request_id=normalized.request_id,
                        phase="phase-02.1",
                        status="missing_collection_dependencies",
                        evidence_request=None,
                        collection_plan=None,
                        raw_evidence=(),
                        artifacts=(ArtifactRecord(kind="CollectionBlocked", path=artifact_path),),
                        telemetry=(),
                        blocking_issues=("missing_collection_dependencies",),
                        metadata={
                            "missing_dependencies": ["pymysql", "paramiko"],
                            "install_hint": 'pip install -e ".[collection]"',
                        },
                    )

            config = AppConfig(
                model=ModelConfig(
                    provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                    model_name="test-model",
                    base_url="https://example.invalid/v1",
                    api_key="sk-test",
                ),
                agent=AgentConfig(),
                runtime=RuntimeConfig(
                    artifact_dir=root / "artifacts",
                    repo_dir=Path("."),
                    workspace_dir=root / "workspace",
                    skills_dir=Path("skills"),
                    agents_dir=Path("agents"),
                ),
            )

            with (
                patch("dbkit.cli.load_app_config", return_value=config),
                patch("dbkit.cli.build_agent_model", return_value=object()),
                patch("dbkit.cli.DeepAgentsRuntimeFactory", FakeRuntimeFactory),
                patch("dbkit.cli.Orchestrator", FakeOrchestrator),
                patch("dbkit.cli.MySQLAnalyzerAgent", FakeAnalyzerAgent),
                patch("dbkit.cli.EvidencePipeline", FakeEvidencePipeline),
                redirect_stdout(output),
            ):
                exit_code = main.main(["--config", str(root / "config.yaml"), "connect mysql"])

        self.assertEqual(exit_code, 1)
        self.assertIn("status=blocked", output.getvalue())
        self.assertIn("reason=missing_collection_dependencies", output.getvalue())
        self.assertIn("missing_dependencies=pymysql,paramiko", output.getvalue())
        self.assertIn('install_hint=pip install -e ".[collection]"', output.getvalue())

    def test_phase03_normal_workflow_auto_delegates_to_evidence_structuring(self) -> None:
        import main
        from dbkit.config import AgentConfig, AppConfig, ModelConfig, ProviderKind, RuntimeConfig
        from dbkit.schemas.evidence import EvidencePipelineResult
        from dbkit.schemas.runtime import ArtifactRecord, NormalizedRequest, RouteDecision, RuntimeResult

        output = io.StringIO()
        case = self

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_root = root / ".dbkit" / "artifacts"
            index_path = _write_minimal_raw_evidence_index(artifact_root)
            normalized = NormalizedRequest(
                request_id="req_cli_phase03",
                original_input="connect mysql",
                redacted_input="connect mysql",
                target_domain="mysql",
                requested_capability="alert_analysis",
                missing_fields=(),
                phase="phase-02.1",
                target_agent="mysql_analyzer",
                task_type="alert_analysis",
                input_mode="live_collection",
            )

            class FakeOrchestrator:
                def __init__(self, **_kwargs):
                    pass

                def run(self, *_args, **_kwargs):
                    return RuntimeResult(
                        normalized_request=normalized,
                        route_decision=RouteDecision(
                            target_agent_name="mysql_analyzer",
                            target_domain="mysql",
                            phase="phase-02.1",
                            reason="test",
                        ),
                        artifacts=(),
                        telemetry=(),
                        deepagents_runtime_ready=True,
                        blocked=False,
                    )

            class FakeAnalyzerAgent:
                skill_text = "MYSQL_SKILL"

                @classmethod
                def from_skills_dir(cls, _skills_dir, agents_dir=None):
                    return cls()

            class FakeRuntimeFactory:
                def __init__(self, **_kwargs):
                    self.runtime = None

                def create_mysql_analyzer_runtime(
                    self,
                    _skill_text,
                    *,
                    evidence_structuring_subagent=None,
                    evidence_structuring_tools=(),
                ):
                    self.runtime = FakeAnalyzerRuntime(
                        evidence_structuring_subagent=evidence_structuring_subagent,
                        evidence_structuring_tools=evidence_structuring_tools,
                    )
                    return self.runtime

                def create_validation_runtime(self, _skill_text):
                    return FakeValidationRuntime()

            class FakeAnalyzerRuntime:
                def __init__(
                    self,
                    *,
                    evidence_structuring_subagent,
                    evidence_structuring_tools,
                ):
                    self.calls = []
                    self.evidence_structuring_subagent = evidence_structuring_subagent
                    self.evidence_structuring_tools = tuple(evidence_structuring_tools)

                def invoke(self, payload):
                    self.calls.append(payload)
                    if payload.get("mode") == "findings_generation":
                        return {
                            "messages": [
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        {
                                            "request_id": normalized.request_id,
                                            "phase": "phase-04",
                                            "mode": "findings_generation",
                                            "target_agent": "mysql_analyzer",
                                            "input_evidence_bundle": ".dbkit/artifacts/req_cli_phase03.evidence-bundle.json",
                                            "findings": [],
                                            "insufficient_evidence": [],
                                            "metadata": {
                                                "skill": "skills/mysql-analyzer/SKILL.md",
                                                "runtime_foundation": "DeepAgents SDK",
                                            },
                                        }
                                    ),
                                }
                            ]
                        }
                    if payload.get("mode") != "evidence_structuring_delegation":
                        return {"messages": [{"role": "assistant", "content": "{}"}]}
                    case.assertEqual(
                        payload["raw_evidence_index"],
                        "/repo/.dbkit/artifacts/req_cli_phase03.raw-evidence-index.json",
                    )
                    case.assertFalse(payload["raw_evidence_index"].startswith("/.dbkit/"))
                    case.assertIn(
                        "/repo/.dbkit/artifacts/req_cli_phase03.raw-evidence-index.json",
                        payload["messages"][0]["content"],
                    )
                    self.evidence_structuring_tools[0].invoke(
                        {"raw_evidence_index": payload["raw_evidence_index"]}
                    )
                    return {
                        "messages": [
                            {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "status": "evidence_bundle_created",
                                        "subagent": "evidence_structuring",
                                    }
                                ),
                            }
                        ]
                    }

            class FakeValidationRuntime:
                def invoke(self, payload):
                    return {
                        "messages": [
                            {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "request_id": normalized.request_id,
                                        "phase": "phase-04",
                                        "input_findings_artifact": ".dbkit/artifacts/req_cli_phase03.findings-draft.json",
                                        "input_evidence_bundle": ".dbkit/artifacts/req_cli_phase03.evidence-bundle.json",
                                        "validated_findings": [],
                                        "blocked_findings": [],
                                        "downgraded_findings": [],
                                        "requires_human_review": False,
                                        "validation_summary": {
                                            "passed": 0,
                                            "blocked": 0,
                                            "downgraded": 0,
                                        },
                                    }
                                ),
                            }
                        ]
                    }

            class FakeEvidencePipeline:
                def __init__(self, **kwargs):
                    self.telemetry = kwargs["telemetry"]

                def run(self, _request):
                    self.telemetry.emit(
                        event_type="raw_evidence_collection_completed",
                        stage="collection",
                        message="Raw evidence collection completed",
                        attributes={
                            "request_id": normalized.request_id,
                            "parent_agent": "mysql_analyzer",
                            "subagent": "evidence_structuring",
                            "raw_evidence_index": str(index_path),
                        },
                    )
                    return EvidencePipelineResult(
                        request_id=normalized.request_id,
                        phase="phase-02.1",
                        status="raw_evidence_collected",
                        evidence_request=None,
                        collection_plan=None,
                        raw_evidence=(),
                        artifacts=(
                            ArtifactRecord(kind="RawEvidenceIndex", path=index_path),
                        ),
                        telemetry=tuple(self.telemetry.events),
                    )

            config = AppConfig(
                model=ModelConfig(
                    provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                    model_name="test-model",
                    base_url="https://example.invalid/v1",
                    api_key="sk-test",
                ),
                agent=AgentConfig(),
                runtime=RuntimeConfig(
                    artifact_dir=artifact_root,
                    repo_dir=root,
                    workspace_dir=root / "workspace",
                    skills_dir=Path("skills"),
                    agents_dir=Path("agents"),
                ),
            )

            with (
                patch("dbkit.cli.load_app_config", return_value=config),
                patch("dbkit.cli.build_agent_model", return_value=object()),
                patch("dbkit.cli.DeepAgentsRuntimeFactory", FakeRuntimeFactory),
                patch("dbkit.cli.Orchestrator", FakeOrchestrator),
                patch("dbkit.cli.MySQLAnalyzerAgent", FakeAnalyzerAgent),
                patch("dbkit.cli.EvidencePipeline", FakeEvidencePipeline),
                redirect_stdout(output),
            ):
                exit_code = main.main(["--config", str(root / "config.yaml"), "connect mysql"])

            telemetry_path = artifact_root / f"{normalized.request_id}.evidence-processing-telemetry.jsonl"
            events = [
                json.loads(line)
                for line in telemetry_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(exit_code, 0)
        self.assertIn("phase=phase-03", output.getvalue())
        self.assertIn("status=evidence_bundle_created", output.getvalue())
        self.assertIn("parent_agent=mysql_analyzer", output.getvalue())
        self.assertIn("subagent=evidence_structuring", output.getvalue())
        self.assertIn("raw_evidence_artifact=", output.getvalue())
        self.assertIn(".evidence-bundle.json", output.getvalue())
        event_types = {event["event_type"] for event in events}
        self.assertIn("evidence_subagent_delegation_started", event_types)
        self.assertIn("evidence_subagent_invoked", event_types)
        self.assertIn("evidence_structuring_model_call_started", event_types)
        self.assertIn("evidence_subagent_invocation_completed", event_types)
        self.assertIn("evidence_bundle_created", event_types)
        delegation_event = [
            event for event in events
            if event["event_type"] == "evidence_subagent_delegation_started"
        ][0]
        delegation_attrs = delegation_event["attributes"]
        self.assertEqual(
            delegation_attrs["raw_evidence_index_repo_relative"],
            ".dbkit/artifacts/req_cli_phase03.raw-evidence-index.json",
        )
        self.assertEqual(
            delegation_attrs["raw_evidence_index_virtual_path"],
            "/repo/.dbkit/artifacts/req_cli_phase03.raw-evidence-index.json",
        )
        self.assertEqual(delegation_attrs["filesystem_root"], "/repo")
        for event in events:
            if event["event_type"].startswith("evidence_"):
                attrs = event.get("attributes") or {}
                self.assertEqual(attrs.get("parent_agent"), "mysql_analyzer")
                self.assertEqual(attrs.get("subagent"), "evidence_structuring")


def _write_minimal_raw_evidence_index(artifact_root: Path) -> Path:
    raw_dir = artifact_root / "raw"
    raw_dir.mkdir(parents=True)
    request_id = "req_cli_phase03"
    raw_path = raw_dir / "rawev_processlist.json"
    raw_path.write_text(
        json.dumps(
            {
                "sql": "SHOW FULL PROCESSLIST",
                "rows": [
                    {
                        "Id": 1,
                        "User": "app",
                        "Host": "10.0.0.1:52000",
                        "Command": "Query",
                        "Time": 12,
                        "State": "executing",
                        "Info": "select 1",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    raw_entry = {
        "raw_evidence_id": "rawev_processlist",
        "request_id": request_id,
        "evidence_type": "mysql.processlist",
        "source": {"kind": "mysql", "tool_name": "collect_mysql_processlist"},
        "collection": {"status": "collected", "errors": []},
        "payload": {
            "content_ref": str(raw_path),
            "bytes": raw_path.stat().st_size,
            "line_count": 1,
        },
        "metadata": {
            "time_window": {
                "start": "2026-05-09T11:00:00+08:00",
                "end": "2026-05-09T18:00:00+08:00",
                "source": "skill_default_from_event_time",
            }
        },
    }
    index_path = artifact_root / f"{request_id}.raw-evidence-index.json"
    index_path.write_text(
        json.dumps(
            {
                "request_id": request_id,
                "phase": "phase-02.1",
                "raw_evidence_count": 1,
                "raw_evidence": [raw_entry],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return index_path


if __name__ == "__main__":
    unittest.main()
