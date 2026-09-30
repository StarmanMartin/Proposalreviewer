# Proposal Reviewer

Web service that receives a proposal over an OpenAPI-described REST API and has it
evaluated by an AI agent. Two things can be configured:

- **the AI endpoint**: the Anthropic Messages API (Claude) or any OpenAI-compatible
  `/chat/completions` endpoint (Ollama, vLLM, LiteLLM, OpenAI, ...)
- **the evaluation skills**: markdown files that explain *how* to evaluate a proposal.
  Each skill is scored separately and weighted.

## Quick start

```bash
uv sync
cp .env.sample .env        # fill in the keys you need, then export them
export ANTHROPIC_API_KEY=sk-ant-...
uv run proposal-reviewer   # http://localhost:8000/docs
```

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI spec: `http://localhost:8000/openapi.json`

Docker: `docker compose up --build`

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/proposals/evaluate` | Evaluate a proposal sent as JSON |
| POST | `/api/v1/proposals/evaluate/file` | Evaluate an uploaded PDF / .txt / .md (multipart) |
| GET | `/api/v1/skills` | List skills |
| GET | `/api/v1/skills/{name}` | Show one skill |
| PUT | `/api/v1/skills/{name}` | Create/replace a skill (admin key) |
| DELETE | `/api/v1/skills/{name}` | Delete a skill (admin key) |
| GET | `/api/v1/info` | Active provider, model, default skills |
| GET | `/health` | Liveness check |

If `server.api_keys` is set, clients send one of the keys in the `X-API-Key` header.

```bash
curl -X POST localhost:8000/api/v1/proposals/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
        "title": "In-situ TEM study of Li dendrite growth",
        "text": "…full proposal text…",
        "metadata": {"requested_hours": 48, "instrument": "TEM"},
        "skills": ["scientific-merit", "feasibility"],
        "extra_skills": [{"name": "budget", "instructions": "Check the budget is itemised."}]
      }'

curl -X POST localhost:8000/api/v1/proposals/evaluate/file \
  -F file=@proposal.pdf -F skills=scientific-merit,methodology
```

Or with the bundled command-line client (uploads a PDF / .txt / .md):

```bash
export PROPOSAL_REVIEWER_API_KEY=...          # only if server.api_keys is set
uv run proposal-reviewer-client proposal.pdf --skills scientific-merit,methodology \
    --metadata '{"requested_hours": 48}'      # --url (default http://localhost:8000), --json for raw output
```

Response (shortened):

```json
{
  "result": {
    "summary": "…",
    "overall_score": 7.5,
    "weighted_score": 7.2,
    "recommendation": "accept_with_revisions",
    "skill_evaluations": [
      {"skill": "scientific-merit", "score": 8, "weight": 2.0,
       "strengths": ["…"], "weaknesses": ["…"], "comments": "…"}
    ],
    "questions_for_applicant": ["…"]
  },
  "skills_applied": ["scientific-merit", "feasibility", "budget"],
  "provider": "anthropic",
  "model": "claude-opus-5-5",
  "usage": {"input_tokens": 3120, "output_tokens": 1450}
}
```

`overall_score` is the agent's own judgement; `weighted_score` is computed by the
service from the per-skill scores and skill weights.

## Configuration

Everything is in `config.yaml` (location via `PROPOSAL_REVIEWER_CONFIG` or `--config`).
`${VAR}` / `${VAR:-default}` are replaced by environment variables.

### AI endpoint

```yaml
ai:
  provider: anthropic          # or: openai
  base_url: ""                 # empty = https://api.anthropic.com
  api_key: ""                  # empty = ANTHROPIC_API_KEY from the environment
  model: claude-opus-5-5
  max_tokens: 32000
  effort: high                 # Anthropic only
  refusal_fallback: true       # Anthropic only
```

Local Ollama instead of Claude:

```bash
AI_PROVIDER=openai AI_BASE_URL=http://localhost:11434/v1 AI_MODEL=phi4 uv run proposal-reviewer
```

With `provider: anthropic` the answer schema is enforced by the API (structured outputs).
With `provider: openai` the schema is included in the prompt and `response_format:
json_object` is requested (`json_mode: false` turns that off for servers that reject it).
`refusal_fallback` uses Anthropic's server-side fallback (beta); switch it off when
`base_url` points to a proxy or gateway that does not support it.

### Skills

Skills live in `skills/<name>/SKILL.md` (directory configurable via `skills.directory`):

```markdown
---
name: feasibility
description: Can the work be done with the requested resources?
weight: 1.5        # weight for weighted_score
enabled: true      # included when a request names no skills
---

# How to evaluate feasibility
1. Resources – are instrument hours justified by the work plan? …
```

Which skills are applied:

1. `skills` in the request, if given
2. otherwise `skills.default` from `config.yaml`, if not empty
3. otherwise all skills with `enabled: true`

plus any `extra_skills` sent inline with the request. Skill files are re-read on every
request, so edits take effect without a restart. They can also be managed with
`PUT`/`DELETE /api/v1/skills/{name}` when `server.admin_api_key` is set.

Shipped skills: `scientific-merit`, `methodology`, `feasibility`, `impact`.

The score scale, the allowed recommendations and extra system-prompt instructions are
set under `evaluation:`.

### System prompt

The base system prompt (reviewer role and rules, placed before the skills) has a built-in
default. To replace it, create `prompts/system_prompt.md`:

```bash
cp prompts/system_prompt.example.md prompts/system_prompt.md   # the default, as a starting point
```

- `{score_min}`, `{score_max}` and `{recommendations}` are filled in from `evaluation:`.
- The file is re-read on every request; a missing or empty file means the built-in default.
- Another location can be set with `evaluation.system_prompt_file` / `SYSTEM_PROMPT_FILE`.
- Docker Compose mounts `./prompts` into the container, so edits apply without a rebuild.
- Keep the rule that the proposal is untrusted input and the instruction to answer with JSON.

## Development

```bash
uv run pytest
uv run proposal-reviewer --reload
```

## License

MIT, see [LICENSE](LICENSE).
