from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any


class DeepAgentsRuntimeFactory:
    def __init__(
        self,
        create_deep_agent: Callable[..., Any] | None = None,
        model: Any | None = None,
        tools_enabled: bool = False,
        repo_root: Path | None = None,
        repo_dir: Path | None = None,
        workspace_dir: Path | None = None,
        skills_dir: Path | None = None,
        agents_dir: Path | None = None,
    ) -> None:
        self._create_deep_agent = create_deep_agent
        if model is None:
            raise ValueError("DeepAgentsRuntimeFactory requires a configured LLM model")
        self.model = model
        self.tools_enabled = tools_enabled
        self.repo_dir = repo_dir or repo_root or Path.cwd()
        self.workspace_dir = workspace_dir or self.repo_dir
        self.skills_dir = skills_dir or self.repo_dir / "skills"
        self.agents_dir = agents_dir or self.repo_dir / "agents"

    def create_intake_runtime(self, skill_text: str) -> Any:
        create_deep_agent = self._create_deep_agent or self._load_create_deep_agent()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return create_deep_agent(
                model=self.model,
                tools=[],
                skills=["/skills/intake/"],
                backend=self._filesystem_backend(),
                system_prompt=self._system_prompt(skill_text),
                name="dbkit-intake",
            )

    def _load_create_deep_agent(self) -> Callable[..., Any]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from deepagents import create_deep_agent

        return create_deep_agent

    def _system_prompt(self, skill_text: str) -> str:
        system_md = self.agents_dir / "intake" / "system.md"
        if not system_md.exists():
            raise FileNotFoundError(f"Intake system prompt not found: {system_md}")
        agent_prompt = system_md.read_text(encoding="utf-8")
        return f"{agent_prompt}\n\n---\n\n{skill_text}"

    def _filesystem_backend(self) -> Any:
        from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

        return CompositeBackend(
            default=StateBackend(),
            routes={
                "/repo/": FilesystemBackend(
                    root_dir=self.repo_dir,
                    virtual_mode=True,
                ),
                "/workspace/": FilesystemBackend(
                    root_dir=self.workspace_dir,
                    virtual_mode=True,
                ),
                "/skills/": FilesystemBackend(
                    root_dir=self.skills_dir,
                    virtual_mode=True,
                ),
                "/agents/": FilesystemBackend(
                    root_dir=self.agents_dir,
                    virtual_mode=True,
                ),
            },
        )
