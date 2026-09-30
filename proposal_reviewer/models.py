"""Request and response models (these define the OpenAPI schema)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .context import Context
from .skills import NAME_PATTERN


class InlineSkill(BaseModel):
    """An ad-hoc skill sent with a single request (not stored)."""

    name: str = Field(pattern=NAME_PATTERN, examples=["budget-check"])
    description: str = ""
    weight: float = Field(default=1.0, ge=0)
    instructions: str = Field(min_length=1, examples=["Check that the requested budget is itemised and justified."])


class ProposalRequest(BaseModel):
    title: str | None = Field(default=None, examples=["In-situ TEM study of Li dendrite growth"])
    text: str = Field(min_length=1, description="Full proposal text (plain text or markdown).")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured context (applicant, facility, requested hours, ...), passed to the agent.",
    )
    skills: list[str] | None = Field(
        default=None,
        description="Names of stored skills to apply. Omit to use the configured defaults.",
        examples=[["scientific-merit", "feasibility"]],
    )
    extra_skills: list[InlineSkill] = Field(
        default_factory=list, description="Additional one-off skills for this request only."
    )


class SkillEvaluation(BaseModel):
    skill: str
    score: float
    weight: float = 1.0
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    comments: str = ""


class EvaluationResult(BaseModel):
    summary: str
    overall_score: float = Field(description="Overall score given by the agent.")
    weighted_score: float | None = Field(
        default=None, description="Weighted mean of the per-skill scores, computed by the service."
    )
    recommendation: str
    skill_evaluations: list[SkillEvaluation]
    questions_for_applicant: list[str] = Field(default_factory=list)


class EvaluationResponse(BaseModel):
    result: EvaluationResult
    skills_applied: list[str]
    area: str | None = Field(default=None, description="Subject area whose skill set was used.")
    provider: str
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)


class SkillOut(BaseModel):
    name: str
    description: str
    weight: float
    enabled: bool
    instructions: str
    area: str | None = Field(default=None, description="Subject area the skill belongs to (null = general).")


class SkillIn(BaseModel):
    description: str = ""
    weight: float = Field(default=1.0, ge=0)
    enabled: bool = True
    instructions: str = Field(min_length=1)


class ContextIn(BaseModel):
    content: str = Field(min_length=1, description="General context as markdown.")


class ContextGenerateRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        description="What the AI should collect. URLs in the prompt are read by the AI.",
        examples=["""Research the KIT KNMF technologies listed on https://www.knmf.kit.edu/technologies.php.

Identify all technologies/technology areas presented on the website and summarize the relevant information for each one.

For each technology, provide:

Technology name
Short description of what the technology is and what it is used for
Available capabilities / services
Important technical specifications or characteristics
Typical applications / use cases
Materials, samples, or objects that can be analyzed or processed, if stated
Relevant equipment, methods, or techniques, if mentioned
Key limitations or requirements, if stated
Contact information or responsible KNMF facility/group, if available
Source URL(s)

Follow links from the main technologies page where necessary to obtain the detailed information. Do not omit technologies simply because their information is provided on a subpage.

Present the results in a clear, consistent table, followed by a more detailed description for technologies where a table would not be sufficient.

Focus on factual information provided by KNMF. Do not add assumptions or information from unrelated external sources. If information is not available, explicitly state “Not specified on the website.”

At the end, provide a complete list of all technologies found and indicate the number of technologies reviewed."""
        ],
    )
    save: bool = Field(default=True, description="Store the result as the general context. false = preview only.")


class ContextGenerateResponse(BaseModel):
    context: Context
    saved: bool
    provider: str
    usage: dict[str, Any] = Field(default_factory=dict)
