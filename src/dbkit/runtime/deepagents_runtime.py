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
                system_prompt=self._system_prompt("intake", skill_text),
                name="dbkit-intake",
            )

    def create_mysql_analyzer_runtime(self, skill_text: str) -> Any:
        create_deep_agent = self._create_deep_agent or self._load_create_deep_agent()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return create_deep_agent(
                model=self.model,
                tools=[],
                skills=["/skills/mysql-analyzer/"],
                backend=self._filesystem_backend(),
                system_prompt=self._system_prompt("mysql-analyzer", skill_text),
                name="dbkit-mysql-analyzer",
            )

    def _load_create_deep_agent(self) -> Callable[..., Any]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from deepagents import create_deep_agent

        return create_deep_agent

    def _system_prompt(self, agent_name: str, skill_text: str) -> str:
        system_md = self.agents_dir / agent_name / "system.md"
        if not system_md.exists():
            raise FileNotFoundError(f"Agent system prompt not found: {system_md}")
        agent_prompt = system_md.read_text(encoding="utf-8")
        return (
            f"{agent_prompt}\n\n---\n\n"
            f"{self._filesystem_context_prompt()}\n\n---\n\n"
            f"{skill_text}"
        )

    def _filesystem_context_prompt(self) -> str:
        return "\n".join(
            [
                "## Runtime Filesystem Mapping",
                "",
                "DeepAgents file tools use DBKit virtual paths.",
                "",
                "- `/repo/` maps to configured `runtime.repo_dir`.",
                "- `/workspace/` maps to configured `runtime.workspace_dir`.",
                "- `/skills/` maps to configured `runtime.skills_dir`.",
                "- `/agents/` maps to configured `runtime.agents_dir`.",
                "",
                "When the user provides a host absolute path, convert it to the",
                "corresponding `/workspace/` virtual path before calling `ls`,",
                "`glob`, or `read_file`.",
                "",
                "If `runtime.workspace_dir=/`, host path `/tmp/a` maps to",
                "`/workspace/tmp/a`.",
                "",
                "If `runtime.workspace_dir=/tmp/mysql_conn_full_mock`, host path",
                "`/tmp/mysql_conn_full_mock/mysql-error.log` maps to",
                "`/workspace/mysql-error.log`.",
            ]
        )

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

    def host_path_to_workspace_virtual_path(self, host_path: str | Path) -> str:
        path = Path(host_path)
        if not path.is_absolute():
            return f"/workspace/{path.as_posix().lstrip('/')}"

        workspace = self.workspace_dir.absolute()
        try:
            relative = path.relative_to(workspace)
        except (OSError, ValueError):
            relative = Path(path.as_posix().lstrip("/"))

        suffix = relative.as_posix().strip("/")
        virtual_path = "/workspace/" + suffix if suffix else "/workspace/"
        if str(host_path).endswith("/") and not virtual_path.endswith("/"):
            virtual_path += "/"
        return virtual_path
