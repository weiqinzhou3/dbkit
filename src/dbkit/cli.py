from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dbkit import __version__
from dbkit.agents.mysql_analyzer import MySQLAnalyzerAgent
from dbkit.config import DEFAULT_CONFIG_PATH, load_app_config
from dbkit.model_provider import build_agent_model
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.deepagents_runtime import DeepAgentsRuntimeFactory
from dbkit.runtime.evidence_pipeline import EvidencePipeline
from dbkit.runtime.guardrails import Guardrails
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.orchestrator import Orchestrator
from dbkit.runtime.time_context import TimeProvider
from dbkit.tools.collectors import CollectorRegistry


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    config_path, interactive_flag, prompt_args = _parse_args(args)
    config = load_app_config(config_path)
    user_input = (
        " ".join(prompt_args) if prompt_args else "MySQL runtime intake smoke test"
    )
    repo_root = config.runtime.repo_dir
    artifact_root = config.runtime.artifact_dir
    model = build_agent_model(config.model, config.agent)
    runtime_factory = DeepAgentsRuntimeFactory(
        model=model,
        tools_enabled=False,
        repo_dir=config.runtime.repo_dir,
        workspace_dir=config.runtime.workspace_dir,
        skills_dir=config.runtime.skills_dir,
        agents_dir=config.runtime.agents_dir,
    )
    orchestrator = Orchestrator(
        repo_root=repo_root,
        skills_dir=config.runtime.skills_dir,
        artifact_store=ArtifactStore(artifact_root),
        telemetry=TelemetryRecorder(),
        guardrails=Guardrails(
            allowed_workspace_root=config.runtime.allowed_workspace_root,
            max_discovered_files=config.runtime.max_discovered_files,
            max_evidence_file_size_bytes=config.runtime.max_evidence_file_size_bytes,
            blocked_paths=config.runtime.blocked_paths,
        ),
        deepagents_runtime_factory=runtime_factory,
        invoke_llm=config.runtime.invoke_llm,
        time_provider=TimeProvider(
            timezone=config.runtime.timezone,
            locale=config.runtime.locale,
        ),
    )
    interactive = interactive_flag or config.runtime.interactive
    result = orchestrator.run(
        user_input,
        interactive=interactive,
        supplement_reader=_read_supplement if interactive else None,
    )

    print(f"DBKit {__version__}")

    if result.blocked:
        print("status=blocked")
        print(
            "reason=missing_required_fields"
            if any(i.startswith("missing required field: ") for i in result.blocking_issues)
            else "reason=guardrails_failed"
        )
        missing = [
            i.removeprefix("missing required field: ")
            for i in result.blocking_issues
            if i.startswith("missing required field: ")
        ]
        if missing:
            print(f"missing_fields={','.join(missing)}")
        if result.rendered_user_message:
            print()
            print(result.rendered_user_message)
        print()
        print("提示：使用 --interactive 可以交互式补充缺失信息。")
        return 1

    mysql_analyzer = MySQLAnalyzerAgent.from_skills_dir(config.runtime.skills_dir)
    analyzer_runtime = runtime_factory.create_mysql_analyzer_runtime(
        mysql_analyzer.skill_text
    )
    evidence_result = EvidencePipeline(
        artifact_store=ArtifactStore(artifact_root),
        telemetry=TelemetryRecorder(),
        collectors=CollectorRegistry(workspace_root=config.runtime.workspace_dir),
        time_provider=TimeProvider(
            timezone=config.runtime.timezone,
            locale=config.runtime.locale,
        ),
        mysql_analyzer_runtime=analyzer_runtime,
    ).run(result.normalized_request)

    print("phase=phase-02")
    print(f"status={evidence_result.status}")
    print(f"input_mode={result.normalized_request.input_mode}")
    print(f"raw_evidence_count={len(evidence_result.raw_evidence)}")
    index_artifacts = [
        artifact for artifact in evidence_result.artifacts
        if artifact.kind == "RawEvidenceIndex"
    ]
    if index_artifacts:
        print(f"artifact={index_artifacts[0].path}")
    elif evidence_result.artifacts:
        print(f"artifact={evidence_result.artifacts[-1].path}")
    if evidence_result.status == "collection_blocked":
        return 1
    return 0


def _parse_args(args: list[str]) -> tuple[Path, bool, list[str]]:
    config_path = DEFAULT_CONFIG_PATH
    interactive = False
    prompt_args: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--config":
            if index + 1 >= len(args):
                raise ValueError("--config requires a path")
            config_path = Path(args[index + 1])
            index += 2
        elif arg == "--interactive":
            interactive = True
            index += 1
        else:
            prompt_args.extend(args[index:])
            break
    return config_path, interactive, prompt_args


def _read_supplement(rendered_user_message: str) -> str:
    print()
    print(rendered_user_message)
    print()
    print("请补充缺失信息：")
    return input("> ")
