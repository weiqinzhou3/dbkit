from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


EVIDENCE_STRUCTURING_ALLOWED_TOOLS: tuple[str, ...] = (
    "build_evidence_bundle",
)

EVIDENCE_STRUCTURING_FORBIDDEN_TOOLS: tuple[str, ...] = (
    "collect_mysql_processlist",
    "collect_processlist",
    "collect_mysql_runtime_status",
    "collect_mysql_innodb_status",
    "collect_innodb_status",
    "collect_mysql_variables",
    "collect_mysql_service_metadata",
    "discover_mysql_log_paths",
    "collect_mysql_error_log",
    "collect_mysql_slow_log",
    "collect_os_service_status",
    "collect_os_cpu_snapshot",
    "collect_os_memory_snapshot",
    "collect_os_disk_snapshot",
    "read_remote_file",
    "kill_mysql_query",
    "change_mysql_config",
    "generate_findings",
    "generate_verdict",
)


@dataclass(frozen=True)
class EvidenceStructuringSubagentRegistration:
    name: str
    parent_agent: str
    skill_path: Path
    system_prompt_path: Path
    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]

    @classmethod
    def from_dirs(
        cls,
        *,
        skills_dir: Path,
        agents_dir: Path,
    ) -> "EvidenceStructuringSubagentRegistration":
        return cls(
            name="evidence_structuring",
            parent_agent="mysql_analyzer",
            skill_path=skills_dir / "evidence" / "SKILL.md",
            system_prompt_path=agents_dir / "evidence-structuring" / "system.md",
            allowed_tools=EVIDENCE_STRUCTURING_ALLOWED_TOOLS,
            forbidden_tools=EVIDENCE_STRUCTURING_FORBIDDEN_TOOLS,
        )

    def validate(self) -> None:
        if not self.skill_path.exists():
            raise FileNotFoundError(f"Evidence skill not found: {self.skill_path}")
        if not self.system_prompt_path.exists():
            raise FileNotFoundError(
                f"Evidence structuring system prompt not found: {self.system_prompt_path}"
            )
        overlap = set(self.allowed_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError(
                "Evidence structuring allowed tools include forbidden tools: "
                + ", ".join(sorted(overlap))
            )

    def is_tool_allowed(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools and tool_name not in self.forbidden_tools

    def to_dict(self) -> dict[str, object]:
        return {
            "agent": self.name,
            "parent_agent": self.parent_agent,
            "skill": self.skill_path.as_posix(),
            "system_prompt": self.system_prompt_path.as_posix(),
            "allowed_tools": list(self.allowed_tools),
        }
