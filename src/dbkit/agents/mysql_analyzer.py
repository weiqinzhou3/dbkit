from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dbkit.agents.evidence_structuring import EvidenceStructuringSubagentRegistration


@dataclass(frozen=True)
class MySQLAnalyzerAgent:
    name: str
    skill_text: str
    subagents: dict[str, EvidenceStructuringSubagentRegistration]

    @classmethod
    def from_skills_dir(
        cls,
        skills_dir: Path,
        agents_dir: Path | None = None,
    ) -> "MySQLAnalyzerAgent":
        skill_path = skills_dir / "mysql-analyzer" / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"MySQL analyzer skill not found: {skill_path}")
        agents_dir = agents_dir or skills_dir.parent / "agents"
        evidence_structuring = EvidenceStructuringSubagentRegistration.from_dirs(
            skills_dir=skills_dir,
            agents_dir=agents_dir,
        )
        evidence_structuring.validate()
        return cls(
            name="mysql_analyzer",
            skill_text=skill_path.read_text(encoding="utf-8"),
            subagents={"evidence_structuring": evidence_structuring},
        )
