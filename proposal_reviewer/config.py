"""Configuration loading (YAML file with ${ENV} interpolation)."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(value):
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


def _split_keys(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [k.strip() for k in value.split(",") if k.strip()]
    return [str(k).strip() for k in value if str(k).strip()]


class ServerConfig(BaseModel):
    api_keys: list[str] = Field(default_factory=list)
    admin_api_key: str = ""

    _split = field_validator("api_keys", mode="before")(_split_keys)


class AIConfig(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    base_url: str = ""
    api_key: str = ""
    model: str = "claude-opus-5-5"
    max_tokens: int = 32000
    timeout_seconds: float = 600
    effort: str = "high"
    refusal_fallback: bool = True
    json_mode: bool = True


class SkillsConfig(BaseModel):
    directory: str = "skills"
    default: list[str] = Field(default_factory=list)

    _split = field_validator("default", mode="before")(_split_keys)


class EvaluationConfig(BaseModel):
    score_min: float = 0
    score_max: float = 10
    recommendations: list[str] = Field(
        default_factory=lambda: ["accept", "accept_with_revisions", "reject", "needs_more_information"]
    )
    extra_instructions: str = ""
    # Markdown file replacing the built-in base system prompt; missing or empty file = built-in default
    system_prompt_file: str = ""


class Settings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    config_path: Path | None = None

    def _resolve(self, value: str) -> Path:
        """Relative paths are relative to the config file's directory."""
        path = Path(value)
        if not path.is_absolute() and self.config_path is not None:
            path = self.config_path.parent / path
        return path

    @property
    def skills_dir(self) -> Path:
        return self._resolve(self.skills.directory)

    @property
    def system_prompt_path(self) -> Path | None:
        file = self.evaluation.system_prompt_file.strip()
        return self._resolve(file) if file else None


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or os.environ.get("PROPOSAL_REVIEWER_CONFIG", "config.yaml"))
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = Settings.model_validate(_interpolate(raw))
    settings.config_path = path.resolve()
    return settings


@lru_cache
def get_settings() -> Settings:
    return load_settings()
