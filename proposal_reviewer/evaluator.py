"""Combines proposal + skills, calls the AI agent and normalises the result."""

from __future__ import annotations

from .config import Settings
from .models import EvaluationResponse, EvaluationResult, ProposalRequest
from .prompt import build_system_prompt, build_user_message, evaluation_schema, load_base_prompt
from .providers import AIError, Provider
from .skills import Skill, SkillError, SkillRegistry


def select_skills(request: ProposalRequest, registry: SkillRegistry, settings: Settings) -> list[Skill]:
    skills = registry.resolve(request.skills, settings.skills.default)
    skills += [Skill(**inline.model_dump()) for inline in request.extra_skills]
    names = [s.name for s in skills]
    if not skills:
        raise SkillError("No skills available: add skills to the skills directory or send extra_skills")
    if len(names) != len(set(names)):
        raise SkillError("Duplicate skill names in request")
    return skills


async def evaluate_proposal(
    request: ProposalRequest, provider: Provider, registry: SkillRegistry, settings: Settings
) -> EvaluationResponse:
    skills = select_skills(request, registry, settings)
    names = [s.name for s in skills]
    schema = evaluation_schema(settings.evaluation, names)
    system = build_system_prompt(settings.evaluation, skills, load_base_prompt(settings.system_prompt_path))
    # Anthropic enforces the schema natively; other endpoints get it spelled out in the prompt.
    inline_schema = None if provider.name == "anthropic" else schema
    user = build_user_message(request.title, request.text, request.metadata, inline_schema, registry.area)

    answer = await provider.evaluate(system, user, schema)

    try:
        result = EvaluationResult.model_validate(answer.data)
    except ValueError as e:
        raise AIError(f"The AI answer does not match the expected format: {e}") from e

    weights = {s.name: s.weight for s in skills}
    for item in result.skill_evaluations:
        item.weight = weights.get(item.skill, 0.0)
    total = sum(e.weight for e in result.skill_evaluations)
    if total > 0:
        result.weighted_score = round(sum(e.score * e.weight for e in result.skill_evaluations) / total, 2)

    return EvaluationResponse(
        result=result, skills_applied=names, area=registry.area, provider=provider.name, model=answer.model, usage=answer.usage
    )
