import io
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

                def create_mysql_analyzer_runtime(self, _skill_text):
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

                def create_mysql_analyzer_runtime(self, _skill_text):
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


if __name__ == "__main__":
    unittest.main()
