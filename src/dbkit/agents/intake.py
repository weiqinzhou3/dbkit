from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntakeAgent:
    name: str
    skill_text: str

    @classmethod
    def from_repo_root(cls, repo_root: Path) -> "IntakeAgent":
        return cls.from_skills_dir(repo_root / "skills")

    @classmethod
    def from_skills_dir(cls, skills_dir: Path) -> "IntakeAgent":
        skill_path = skills_dir / "intake" / "SKILL.md"
        return cls(name="intake", skill_text=skill_path.read_text(encoding="utf-8"))
