"""Evaluation skills stored as <skills_dir>/<name>/SKILL.md files.

A SKILL.md starts with YAML front matter followed by markdown instructions:

    ---
    name: feasibility
    description: Is the proposed work realistic with the requested resources?
    weight: 1.5
    enabled: true
    ---
    # How to evaluate feasibility
    ...
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

SKILL_FILE = "SKILL.md"
NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_NAME_RE = re.compile(NAME_PATTERN)
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class Skill(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    weight: float = Field(default=1.0, ge=0)
    enabled: bool = True
    instructions: str


class SkillError(ValueError):
    pass


def validate_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise SkillError(f"Invalid skill name '{name}': use lowercase letters, digits, '-' or '_'")
    return name


def parse_skill(text: str, fallback_name: str) -> Skill:
    match = _FRONT_MATTER_RE.match(text)
    meta, body = ({}, text)
    if match:
        meta = yaml.safe_load(match.group(1)) or {}
        body = match.group(2)
    return Skill(
        name=meta.get("name", fallback_name),
        description=meta.get("description", ""),
        weight=meta.get("weight", 1.0),
        enabled=meta.get("enabled", True),
        instructions=body.strip(),
    )


def render_skill(skill: Skill) -> str:
    meta = {
        "name": skill.name,
        "description": skill.description,
        "weight": skill.weight,
        "enabled": skill.enabled,
    }
    return f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n\n{skill.instructions.strip()}\n"


class SkillRegistry:
    """Reads skills from disk on every call, so edits to SKILL.md files apply without a restart."""

    def __init__(self, directory: Path):
        self.directory = directory

    def list(self) -> list[Skill]:
        if not self.directory.is_dir():
            return []
        skills = []
        for skill_file in sorted(self.directory.glob(f"*/{SKILL_FILE}")):
            skills.append(parse_skill(skill_file.read_text(encoding="utf-8"), skill_file.parent.name))
        return skills

    def get(self, name: str) -> Skill | None:
        validate_name(name)
        path = self.directory / name / SKILL_FILE
        if not path.is_file():
            return None
        return parse_skill(path.read_text(encoding="utf-8"), name)

    def save(self, skill: Skill) -> Skill:
        validate_name(skill.name)
        target = self.directory / skill.name
        target.mkdir(parents=True, exist_ok=True)
        (target / SKILL_FILE).write_text(render_skill(skill), encoding="utf-8")
        return skill

    def delete(self, name: str) -> bool:
        validate_name(name)
        target = self.directory / name
        if not (target / SKILL_FILE).is_file():
            return False
        shutil.rmtree(target)
        return True

    def resolve(self, names: list[str] | None, defaults: list[str]) -> list[Skill]:
        """Pick the skills for one evaluation: explicit names > configured defaults > all enabled."""
        requested = names or defaults
        if not requested:
            return [s for s in self.list() if s.enabled]
        skills, missing = [], []
        for name in requested:
            skill = self.get(name)
            if skill is None:
                missing.append(name)
            else:
                skills.append(skill)
        if missing:
            raise SkillError(f"Unknown skill(s): {', '.join(missing)}")
        return skills
