from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dbkit.agents.evidence_structuring import EvidenceStructuringSubagentRegistration
from dbkit.runtime.langchain_compat import configure_langchain_deserialization_allowlist


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
        return create_deep_agent(
            model=self.model,
            tools=[],
            skills=["/skills/intake/"],
            backend=self._filesystem_backend(),
            system_prompt=self._system_prompt("intake", skill_text),
            name="dbkit-intake",
        )

    def create_mysql_analyzer_runtime(
        self,
        skill_text: str,
        *,
        evidence_structuring_subagent: EvidenceStructuringSubagentRegistration | None = None,
        evidence_structuring_tools: Sequence[Any] = (),
    ) -> Any:
        create_deep_agent = self._create_deep_agent or self._load_create_deep_agent()
        subagents = []
        if evidence_structuring_subagent is not None:
            evidence_structuring_subagent.validate()
            evidence_skill_text = evidence_structuring_subagent.skill_path.read_text(
                encoding="utf-8"
            )
            subagents.append(
                {
                    "name": evidence_structuring_subagent.name,
                    "description": (
                        "Transform DBKit Phase-02.1 RawEvidence into a bounded, "
                        "deduplicated, LLM-safe EvidenceBundle. Use this after "
                        "RawEvidence collection is complete and before any "
                        "findings_generation work."
                    ),
                    "system_prompt": self._system_prompt(
                        "evidence-structuring",
                        evidence_skill_text,
                    ),
                    "skills": ["/skills/evidence/"],
                    "tools": evidence_structuring_tools,
                }
            )
        return create_deep_agent(
            model=self.model,
            tools=[],
            subagents=subagents or None,
            skills=["/skills/mysql-analyzer/"],
            backend=self._filesystem_backend(),
            system_prompt=self._system_prompt("mysql-analyzer", skill_text),
            name="dbkit-mysql-analyzer",
        )

    def create_evidence_structuring_runtime(self, skill_text: str) -> Any:
        create_deep_agent = self._create_deep_agent or self._load_create_deep_agent()
        return create_deep_agent(
            model=self.model,
            tools=[],
            skills=["/skills/evidence/"],
            backend=self._filesystem_backend(),
            system_prompt=self._system_prompt("evidence-structuring", skill_text),
            name="dbkit-evidence-structuring",
        )

    def create_validation_runtime(self, skill_text: str) -> Any:
        create_deep_agent = self._create_deep_agent or self._load_create_deep_agent()
        return create_deep_agent(
            model=self.model,
            tools=[],
            skills=["/skills/validation/"],
            backend=self._filesystem_backend(),
            system_prompt=self._system_prompt("validation", skill_text),
            name="dbkit-validation",
        )

    def _load_create_deep_agent(self) -> Callable[..., Any]:
        configure_langchain_deserialization_allowlist("messages")
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
                "Repository artifacts must be accessed through `/repo/`.",
                "For example, host/repo path `.dbkit/artifacts/x.json` maps to",
                "`/repo/.dbkit/artifacts/x.json`, never `/.dbkit/artifacts/x.json`.",
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
        configure_langchain_deserialization_allowlist("messages")
        from deepagents.backends.protocol import GlobResult, LsResult, ReadResult
        from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

        default_backend = _RawArtifactBlockingBackend(
            StateBackend(),
            read_result_cls=ReadResult,
            ls_result_cls=LsResult,
            glob_result_cls=GlobResult,
        )
        return CompositeBackend(
            default=default_backend,
            routes={
                "/repo/": _RawArtifactBlockingBackend(
                    FilesystemBackend(
                        root_dir=self.repo_dir,
                        virtual_mode=True,
                    ),
                    read_result_cls=ReadResult,
                    ls_result_cls=LsResult,
                    glob_result_cls=GlobResult,
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


class _RawArtifactBlockingBackend:
    def __init__(
        self,
        backend: Any,
        *,
        read_result_cls: Any,
        ls_result_cls: Any,
        glob_result_cls: Any,
    ) -> None:
        self._backend = backend
        self._read_result_cls = read_result_cls
        self._ls_result_cls = ls_result_cls
        self._glob_result_cls = glob_result_cls

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        if _is_blocked_raw_artifact_path(file_path):
            return self._read_result_cls(error=_blocked_raw_artifact_message(file_path))
        return self._backend.read(file_path, offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        if _is_blocked_raw_artifact_path(file_path):
            return self._read_result_cls(error=_blocked_raw_artifact_message(file_path))
        return await self._backend.aread(file_path, offset=offset, limit=limit)

    def ls(self, path: str) -> Any:
        if _is_blocked_raw_artifact_path(path):
            return self._ls_result_cls(error=_blocked_raw_artifact_message(path))
        return self._backend.ls(path)

    async def als(self, path: str) -> Any:
        if _is_blocked_raw_artifact_path(path):
            return self._ls_result_cls(error=_blocked_raw_artifact_message(path))
        return await self._backend.als(path)

    def glob(self, pattern: str, path: str = "/") -> Any:
        candidate = f"{path.rstrip('/')}/{pattern.lstrip('/')}"
        if _is_blocked_raw_artifact_path(path) or _is_blocked_raw_artifact_path(candidate):
            return self._glob_result_cls(error=_blocked_raw_artifact_message(candidate))
        return self._backend.glob(pattern, path)

    async def aglob(self, pattern: str, path: str = "/") -> Any:
        candidate = f"{path.rstrip('/')}/{pattern.lstrip('/')}"
        if _is_blocked_raw_artifact_path(path) or _is_blocked_raw_artifact_path(candidate):
            return self._glob_result_cls(error=_blocked_raw_artifact_message(candidate))
        return await self._backend.aglob(pattern, path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)


def _is_blocked_raw_artifact_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    if normalized.startswith("repo/"):
        normalized = normalized.removeprefix("repo/").lstrip("/")
    if normalized.startswith(".dbkit/artifacts/raw/"):
        return True
    if normalized.startswith(".dbkit/artifacts/") and normalized.endswith(
        ".raw-evidence-index.json"
    ):
        return True
    return False


def _blocked_raw_artifact_message(path: str) -> str:
    return (
        "blocked: evidence_structuring must not use filesystem read/list/glob "
        f"for raw evidence artifacts ({path}); call build_evidence_bundle instead"
    )
