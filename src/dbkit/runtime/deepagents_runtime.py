from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dbkit.tools.normalize_request import normalize_request_tool


class DeepAgentsRuntimeFactory:
    def __init__(
        self,
        create_deep_agent: Callable[..., Any] | None = None,
        model: Any | None = None,
        tools_enabled: bool = True,
        repo_root: Path | None = None,
    ) -> None:
        self._create_deep_agent = create_deep_agent
        if model is None:
            raise ValueError("DeepAgentsRuntimeFactory requires a configured LLM model")
        self.model = model
        self.tools_enabled = tools_enabled
        self.repo_root = repo_root or Path.cwd()

    def create_intake_runtime(self, skill_text: str) -> Any:
        create_deep_agent = self._create_deep_agent or self._load_create_deep_agent()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return create_deep_agent(
                model=self.model,
                tools=[normalize_request_tool] if self.tools_enabled else [],
                system_prompt=self._system_prompt(skill_text),
                name="dbkit-intake",
            )

    def _load_create_deep_agent(self) -> Callable[..., Any]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from deepagents import create_deep_agent

        return create_deep_agent

    def _system_prompt(self, skill_text: str) -> str:
        system_md = self.repo_root / "agents" / "intake" / "system.md"
        if not system_md.exists():
            raise FileNotFoundError(f"Intake system prompt not found: {system_md}")
        agent_prompt = system_md.read_text(encoding="utf-8")
        return f"{agent_prompt}\n\n---\n\n{skill_text}"
