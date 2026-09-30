from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proposal_reviewer.client import evaluate_file, format_result
from proposal_reviewer.config import Settings, load_settings
from proposal_reviewer.main import create_app
from proposal_reviewer.prompt import BASE_SYSTEM_PROMPT, build_system_prompt, load_base_prompt
from proposal_reviewer.providers import AIAnswer, AIText, html_to_text, parse_json_answer
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

    async def generate(self, system, user):
        self.calls.append((system, user, None))
        return AIText(text="# TEM\nResolution 0.1 nm.", model="fake-model", usage={"output_tokens": 5})


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


@pytest.fixture
def chemistry(settings):
    area = SkillRegistry(settings.skills_dir).for_area("chemistry", must_exist=False)
    area.save(Skill(name="beta", weight=2, instructions="Check beta for chemistry."))
    area.save(Skill(name="safety", instructions="Check lab safety."))
    return area


def test_evaluate_with_area_merges_and_overrides_skills(client, provider, chemistry):
    r = client.post("/api/v1/proposals/evaluate?area=chemistry", json={"text": "p"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["area"] == "chemistry"
    assert body["skills_applied"] == ["alpha", "beta", "safety"]
    system, user, _ = provider.calls[0]
    assert "Check beta for chemistry." in system and "Check beta." not in system
    assert "Check lab safety." in system and "Subject area: chemistry" in user
    # alpha 8*3, beta (area weight 2) 4*2, safety 4*1 -> 36 / 6
    assert body["result"]["weighted_score"] == 6.0


def test_evaluate_without_area_ignores_area_skills(client, chemistry):
    body = client.post("/api/v1/proposals/evaluate", json={"text": "p"}).json()
    assert body["skills_applied"] == ["alpha", "beta"] and body["area"] is None


def test_unknown_or_invalid_area_is_400(client):
    assert client.post("/api/v1/proposals/evaluate?area=nope", json={"text": "p"}).status_code == 400
    assert client.post("/api/v1/proposals/evaluate?area=..%2Fetc", json={"text": "p"}).status_code == 400


def test_area_skill_endpoints(client, chemistry):
    assert client.get("/api/v1/areas").json() == ["chemistry"]
    assert client.get("/api/v1/info").json()["areas"] == ["chemistry"]
    skills = {s["name"]: s["area"] for s in client.get("/api/v1/skills?area=chemistry").json()}
    assert skills == {"alpha": None, "beta": "chemistry", "safety": "chemistry"}
    assert client.get("/api/v1/skills/safety").status_code == 404

    admin = {"X-API-Key": "admin"}
    new = {"instructions": "Check beamtime."}
    assert client.put("/api/v1/skills/beamtime?area=physics", json=new, headers=admin).json()["area"] == "physics"
    assert client.get("/api/v1/areas").json() == ["chemistry", "physics"]
    assert client.put("/api/v1/skills/areas", json=new, headers=admin).status_code == 400
    # deleting the area override makes the general skill visible again
    assert client.delete("/api/v1/skills/beta?area=chemistry", headers=admin).status_code == 204
    assert client.get("/api/v1/skills/beta?area=chemistry").json()["area"] is None


def test_client_evaluate_file_with_area(client, chemistry, tmp_path):
    proposal = tmp_path / "proposal.txt"
    proposal.write_text("Text")
    body = evaluate_file(client, proposal, area="chemistry")
    assert body["skills_applied"] == ["alpha", "beta", "safety"]
    assert "Subject area:   chemistry" in format_result(body)


def test_generate_context_is_used_for_all_areas(client, provider, chemistry):
    body = {"prompt": "Summarise https://example.org/technologies"}
    assert client.post("/api/v1/context/generate", json=body).status_code == 401
    assert client.get("/api/v1/context").status_code == 404
    admin = {"X-API-Key": "admin"}

    preview = client.post("/api/v1/context/generate", json=body | {"save": False}, headers=admin)
    assert preview.status_code == 200 and preview.json()["saved"] is False
    assert client.get("/api/v1/context").status_code == 404

    r = client.post("/api/v1/context/generate", json=body, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["context"]["content"] == "# TEM\nResolution 0.1 nm."
    assert provider.calls[-1][1] == body["prompt"]
    stored = client.get("/api/v1/context").json()
    assert stored["prompt"] == body["prompt"] and stored["model"] == "fake-model" and stored["generated_at"]

    for url in ("/api/v1/proposals/evaluate", "/api/v1/proposals/evaluate?area=chemistry"):
        assert client.post(url, json={"text": "p"}).status_code == 200
        system = provider.calls[-1][0]
        assert "<context>\n# TEM\nResolution 0.1 nm.\n</context>" in system
        assert system.index("<context>") < system.index("# Evaluation skills")
    # the context file in the skills directory is not mistaken for a skill
    assert [s["name"] for s in client.get("/api/v1/skills").json()] == ["alpha", "beta"]


def test_context_put_and_delete(client, provider):
    admin = {"X-API-Key": "admin"}
    assert client.put("/api/v1/context", json={"content": "By hand."}).status_code == 401
    assert client.put("/api/v1/context", json={"content": "By hand."}, headers=admin).status_code == 200
    assert client.get("/api/v1/context").json() == {
        "content": "By hand.", "prompt": None, "model": None, "generated_at": None
    }
    assert client.delete("/api/v1/context", headers=admin).status_code == 204
    assert client.delete("/api/v1/context", headers=admin).status_code == 404
    client.post("/api/v1/proposals/evaluate", json={"text": "p"})
    assert "General context" not in provider.calls[-1][0]


def test_html_to_text_drops_scripts():
    html = "<html><head><title>x</title><script>var a;</script></head><body><h1>TEM</h1><p>0.1 nm</p></body></html>"
    assert html_to_text(html) == "TEM\n0.1 nm"
