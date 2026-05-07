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
    repo_root = Path.cwd()
    artifact_root = config.runtime.artifact_dir
    model = build_agent_model(config.model, config.agent)
    orchestrator = Orchestrator(
        repo_root=repo_root,
        artifact_store=ArtifactStore(artifact_root),
        telemetry=TelemetryRecorder(),
        deepagents_runtime_factory=DeepAgentsRuntimeFactory(
            model=model,
            tools_enabled=config.agent.tool_calling,
        ),
        invoke_llm=config.runtime.invoke_llm,
    )
    result = orchestrator.run(user_input)

    print(f"DBKit {__version__}")
    print(f"target_agent={result.route_decision.target_agent_name}")
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
