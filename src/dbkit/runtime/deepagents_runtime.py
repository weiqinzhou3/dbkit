from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any

from dbkit.tools.normalize_request import normalize_request_tool


class DeepAgentsRuntimeFactory:
    def __init__(
        self,
        create_deep_agent: Callable[..., Any] | None = None,
        model: Any | None = None,
    ) -> None:
        self._create_deep_agent = create_deep_agent
        if model is None:
            raise ValueError("DeepAgentsRuntimeFactory requires a configured LLM model")
        self.model = model

    def create_intake_runtime(self, skill_text: str) -> Any:
        create_deep_agent = self._create_deep_agent or self._load_create_deep_agent()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return create_deep_agent(
                model=self.model,
                tools=[normalize_request_tool],
                system_prompt=self._system_prompt(skill_text),
                name="dbkit-intake",
            )

    def _load_create_deep_agent(self) -> Callable[..., Any]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from deepagents import create_deep_agent

        return create_deep_agent

    def _system_prompt(self, skill_text: str) -> str:
        return "\n\n".join(
            [
                "DBKit Phase 01 Intake Agent.",
                "Normalize requests only. Do not perform DBA reasoning.",
                skill_text,
            ]
        )
