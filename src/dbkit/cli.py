from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dbkit import __version__
from dbkit.config import DEFAULT_CONFIG_PATH, load_app_config
from dbkit.model_provider import build_agent_model
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.deepagents_runtime import DeepAgentsRuntimeFactory
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.orchestrator import Orchestrator


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    config_path, prompt_args = _parse_args(args)
    config = load_app_config(config_path)
    user_input = (
        " ".join(prompt_args) if prompt_args else "MySQL runtime intake smoke test"
    )
    repo_root = config.runtime.repo_dir
    artifact_root = config.runtime.artifact_dir
    model = build_agent_model(config.model, config.agent)
    orchestrator = Orchestrator(
        repo_root=repo_root,
        skills_dir=config.runtime.skills_dir,
        artifact_store=ArtifactStore(artifact_root),
        telemetry=TelemetryRecorder(),
        deepagents_runtime_factory=DeepAgentsRuntimeFactory(
            model=model,
            tools_enabled=False,
            repo_dir=config.runtime.repo_dir,
            workspace_dir=config.runtime.workspace_dir,
            skills_dir=config.runtime.skills_dir,
            agents_dir=config.runtime.agents_dir,
        ),
        invoke_llm=config.runtime.invoke_llm,
    )
    result = orchestrator.run(user_input)

    print(f"DBKit {__version__}")

    if result.blocked:
        missing = [
            i.removeprefix("missing required field: ")
            for i in result.blocking_issues
            if i.startswith("missing required field: ")
        ]
        other_issues = [
            i for i in result.blocking_issues
            if not i.startswith("missing required field: ")
        ]

        print("status=blocked")
        print("reason=missing_required_fields" if missing else "reason=guardrails_failed")
        if missing:
            print(f"missing_fields={','.join(missing)}")
        for issue in other_issues:
            print(f"issue={issue}")
        if result.artifacts:
            print(f"artifact={result.artifacts[0].path}")
        return 1

    print(f"phase={result.normalized_request.phase}")
    print(f"target_agent={result.route_decision.target_agent_name}")
    print(f"target_domain={result.normalized_request.target_domain}")
    print(f"task_type={result.normalized_request.task_type}")
    print(f"artifact={result.artifacts[0].path}")
    return 0


def _parse_args(args: list[str]) -> tuple[Path, list[str]]:
    if not args:
        return DEFAULT_CONFIG_PATH, []

    if args[0] == "--config":
        if len(args) < 2:
            raise ValueError("--config requires a path")
        return Path(args[1]), args[2:]

    return DEFAULT_CONFIG_PATH, args
