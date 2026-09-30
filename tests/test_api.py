from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proposal_reviewer.client import evaluate_file, format_result
from proposal_reviewer.config import Settings, load_settings
from proposal_reviewer.main import create_app
from proposal_reviewer.prompt import BASE_SYSTEM_PROMPT, build_system_prompt, load_base_prompt
from proposal_reviewer.providers import AIAnswer, parse_json_answer
from proposal_reviewer.skills import Skill, SkillRegistry, parse_skill, render_skill

ROOT = Path(__file__).resolve().parent.parent


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def evaluate(self, system, user, schema):
        self.calls.append((system, user, schema))
        skills = schema["properties"]["skill_evaluations"]["items"]["properties"]["skill"]["enum"]
        return AIAnswer(
            model="fake-model",
            data={
                "summary": "ok",
                "overall_score": 7,
                "recommendation": "accept",
                "skill_evaluations": [
                    {"skill": s, "score": 8 if i == 0 else 4, "strengths": ["a"], "weaknesses": [], "comments": ""}
                    for i, s in enumerate(skills)
                ],
                "questions_for_applicant": [],
            },
        )


@pytest.fixture
def settings(tmp_path):
    s = Settings.model_validate(
        {
            "server": {"api_keys": "k1", "admin_api_key": "admin"},
            "skills": {"directory": str(tmp_path / "skills")},
        }
    )
    registry = SkillRegistry(s.skills_dir)
    registry.save(Skill(name="alpha", description="A", weight=3, instructions="Check alpha."))
    registry.save(Skill(name="beta", description="B", weight=1, instructions="Check beta."))
    return s


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def client(settings, provider):
    with TestClient(create_app(settings, provider), headers={"X-API-Key": "k1"}) as c:
        yield c


def test_openapi_available(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/proposals/evaluate" in paths
    assert "/api/v1/skills/{name}" in paths


def test_requires_api_key(settings, provider):
    with TestClient(create_app(settings, provider)) as c:
        assert c.post("/api/v1/proposals/evaluate", json={"text": "x"}).status_code == 401


def test_evaluate_uses_all_enabled_skills_and_weights(client, provider):
    r = client.post("/api/v1/proposals/evaluate", json={"title": "T", "text": "My proposal"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skills_applied"] == ["alpha", "beta"]
    # (8*3 + 4*1) / 4 = 7
    assert body["result"]["weighted_score"] == 7.0
    system, user, _ = provider.calls[0]
    assert "Check alpha." in system and "Check beta." in system
    assert "<proposal>" in user and "My proposal" in user


def test_evaluate_selected_and_inline_skills(client, provider):
    r = client.post(
        "/api/v1/proposals/evaluate",
        json={"text": "p", "skills": ["beta"], "extra_skills": [{"name": "budget", "instructions": "Check money."}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skills_applied"] == ["beta", "budget"]
    assert "Check money." in provider.calls[0][0]


def test_unknown_skill_is_400(client):
    r = client.post("/api/v1/proposals/evaluate", json={"text": "p", "skills": ["nope"]})
    assert r.status_code == 400


def test_evaluate_file(client):
    r = client.post(
        "/api/v1/proposals/evaluate/file",
        files={"file": ("proposal.md", b"# My proposal\nText", "text/markdown")},
        data={"skills": "alpha"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skills_applied"] == ["alpha"]


def test_skill_crud_requires_admin(client):
    new = {"description": "C", "weight": 2, "instructions": "Check gamma."}
    assert client.put("/api/v1/skills/gamma", json=new).status_code == 401
    admin = {"X-API-Key": "admin"}
    assert client.put("/api/v1/skills/gamma", json=new, headers=admin).status_code == 200
    assert client.get("/api/v1/skills/gamma").json()["instructions"] == "Check gamma."
    assert client.put("/api/v1/skills/..%2Fetc", json=new, headers=admin).status_code in (400, 404)
    assert client.delete("/api/v1/skills/gamma", headers=admin).status_code == 204
    assert client.get("/api/v1/skills/gamma").status_code == 404


def test_skill_roundtrip():
    skill = Skill(name="x", description="d: with colon", weight=1.5, enabled=False, instructions="# Hi\nbody")
    assert parse_skill(render_skill(skill), "x") == skill


def test_parse_json_answer_tolerates_fences():
    assert parse_json_answer('Here:\n```json\n{"a": 1}\n```') == {"a": 1}


def test_shipped_config_and_skills_load():
    s = load_settings(ROOT / "config.yaml")
    skills = SkillRegistry(s.skills_dir).list()
    assert {k.name for k in skills} >= {"scientific-merit", "methodology", "feasibility", "impact"}
    assert "scientific-merit" in build_system_prompt(s.evaluation, skills)


def test_client_evaluate_file(client, tmp_path):
    proposal = tmp_path / "proposal.md"
    proposal.write_text("# My proposal\nText")
    body = evaluate_file(client, proposal, skills=["alpha"], metadata={"requested_hours": 8})
    assert body["skills_applied"] == ["alpha"]
    assert "Recommendation: accept" in format_result(body)
    with pytest.raises(RuntimeError, match="400"):
        evaluate_file(client, proposal, skills=["nope"])


def test_custom_system_prompt_file(settings, provider, tmp_path):
    prompt_file = tmp_path / "system_prompt.md"
    settings.evaluation.system_prompt_file = str(prompt_file)
    with TestClient(create_app(settings, provider), headers={"X-API-Key": "k1"}) as c:
        c.post("/api/v1/proposals/evaluate", json={"text": "p"})
        prompt_file.write_text("# Reviewer\nScale {score_min}-{score_max}, one of {recommendations}. JSON: {}")
        c.post("/api/v1/proposals/evaluate", json={"text": "p"})
    default_system, custom_system = provider.calls[0][0], provider.calls[1][0]
    assert default_system.startswith("You are an expert reviewer")
    assert custom_system.startswith("# Reviewer\nScale 0-10, one of accept, accept_with_revisions")
    assert "JSON: {}" in custom_system and "Check alpha." in custom_system


def test_system_prompt_example_matches_default():
    assert load_base_prompt(ROOT / "prompts" / "system_prompt.example.md") == BASE_SYSTEM_PROMPT
