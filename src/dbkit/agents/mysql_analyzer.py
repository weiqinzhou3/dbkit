from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MySQLAnalyzerAgent:
    name: str
    skill_text: str

    @classmethod
    def from_skills_dir(cls, skills_dir: Path) -> "MySQLAnalyzerAgent":
        skill_path = skills_dir / "mysql-analyzer" / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"MySQL analyzer skill not found: {skill_path}")
        return cls(name="mysql-analyzer", skill_text=skill_path.read_text(encoding="utf-8"))
