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

Subject areas have their own skills in <skills_dir>/areas/<area>/<name>/SKILL.md. For an area,
its skills are added to the general ones; an area skill with the same name replaces the general one.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

SKILL_FILE = "SKILL.md"
AREAS_DIR = "areas"
NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_NAME_RE = re.compile(NAME_PATTERN)
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class Skill(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    weight: float = Field(default=1.0, ge=0)
    enabled: bool = True
    instructions: str
    # Subject area the skill comes from (None = general); set by the registry, not stored in the file
    area: str | None = None


class SkillError(ValueError):
    pass


def validate_name(name: str, kind: str = "skill name") -> str:
    if not _NAME_RE.match(name):
        raise SkillError(f"Invalid {kind} '{name}': use lowercase letters, digits, '-' or '_'")
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
    """Reads skills from disk on every call, so edits to SKILL.md files apply without a restart.

    A registry without area sees the general skills; one returned by for_area() sees the general
    skills merged with that area's skills. save() and delete() only touch the registry's own level.
    """

    def __init__(self, directory: Path, area: str | None = None):
        self.directory = directory
        self.area = area

    @property
    def areas_dir(self) -> Path:
        return self.directory / AREAS_DIR

    @property
    def own_dir(self) -> Path:
        return self.areas_dir / self.area if self.area else self.directory

    def areas(self) -> list[str]:
        if not self.areas_dir.is_dir():
            return []
        return sorted(p.name for p in self.areas_dir.iterdir() if p.is_dir() and _NAME_RE.match(p.name))

    def for_area(self, area: str | None, must_exist: bool = True) -> SkillRegistry:
        if not area:
            return SkillRegistry(self.directory)
        validate_name(area, "subject area")
        if must_exist and not (self.areas_dir / area).is_dir():
            raise SkillError(f"Unknown subject area '{area}'")
        return SkillRegistry(self.directory, area)

    def _levels(self) -> list[tuple[str | None, Path]]:
        """Skill directories from lowest to highest priority."""
        levels = [(None, self.directory)]
        if self.area:
            levels.append((self.area, self.areas_dir / self.area))
        return levels

    def list(self) -> list[Skill]:
        merged: dict[str, Skill] = {}
        for area, directory in self._levels():
            if not directory.is_dir():
                continue
            for skill_file in sorted(directory.glob(f"*/{SKILL_FILE}")):
                skill = parse_skill(skill_file.read_text(encoding="utf-8"), skill_file.parent.name)
                skill.area = area
                merged[skill.name] = skill
        return sorted(merged.values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        validate_name(name)
        for area, directory in reversed(self._levels()):
            path = directory / name / SKILL_FILE
            if path.is_file():
                skill = parse_skill(path.read_text(encoding="utf-8"), name)
                skill.area = area
                return skill
        return None

    def save(self, skill: Skill) -> Skill:
        validate_name(skill.name)
        if self.area is None and skill.name == AREAS_DIR:
            raise SkillError(f"'{AREAS_DIR}' is reserved for subject areas")
        target = self.own_dir / skill.name
        target.mkdir(parents=True, exist_ok=True)
        (target / SKILL_FILE).write_text(render_skill(skill), encoding="utf-8")
        return skill.model_copy(update={"area": self.area})

    def delete(self, name: str) -> bool:
        validate_name(name)
        target = self.own_dir / name
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
