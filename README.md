# Proposal Reviewer

Web service that receives a proposal over an OpenAPI-described REST API and has it
evaluated by an AI agent. Two things can be configured:

- **the AI endpoint**: the Anthropic Messages API (Claude) or any OpenAI-compatible
  `/chat/completions` endpoint (Ollama, vLLM, LiteLLM, OpenAI, ...)
- **the evaluation skills**: markdown files that explain *how* to evaluate a proposal.
  Each skill is scored separately and weighted.

## Installation (Docker)

Requires Docker with the Compose plugin. The install script pulls
`mstarman/knmfi-proposalreviewer:0.0.2` from Docker Hub, asks for the AI endpoint (KIT
KI-Toolbox, Anthropic or another OpenAI-compatible endpoint), and starts the service:

```bash
curl -H 'Cache-Control: no-cache' -O https://raw.githubusercontent.com/StarmanMartin/Proposalreviewer/main/install.sh
chmod +x install.sh
./install.sh [install-dir]     # default: ./proposal-reviewer
```

It creates `docker-compose.yml`, `.env` (AI settings and randomly generated client/admin
API keys), and copies the default `config.yaml`, `skills/` and `prompts/` out of the image
so they can be edited. Existing files are never overwritten. For a non-interactive install,
set the answers as environment variables:

```bash
AI_PROVIDER=openai AI_BASE_URL=https://ki-toolbox.scc.kit.edu/api AI_API_KEY=... AI_MODEL=... \
  PORT=8000 ./install.sh /opt/proposal-reviewer
```

Publishing a new image (maintainers): `docker compose build && docker compose push`
(the tag is set in `docker-compose.yml` and as `IMAGE` in `install.sh`).

## Quick start (development)

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
| POST | `/api/v1/proposals/evaluate[?area=…]` | Evaluate a proposal sent as JSON |
| POST | `/api/v1/proposals/evaluate/file[?area=…]` | Evaluate an uploaded PDF / .txt / .md (multipart) |
| GET | `/api/v1/areas` | List subject areas that have their own skills |
| GET | `/api/v1/skills[?area=…]` | List skills (for a subject area: general + area skills) |
| GET | `/api/v1/skills/{name}[?area=…]` | Show one skill |
| PUT | `/api/v1/skills/{name}[?area=…]` | Create/replace a skill, in an area if given (admin key) |
| DELETE | `/api/v1/skills/{name}[?area=…]` | Delete a skill, from an area if given (admin key) |
| GET | `/api/v1/context` | Show the general context used for all proposals |
| POST | `/api/v1/context/generate` | Let the AI write the general context from a prompt (admin key) |
| PUT | `/api/v1/context` | Set the general context by hand (admin key) |
| DELETE | `/api/v1/context` | Remove the general context (admin key) |
| GET | `/api/v1/info` | Active provider, model, default skills, subject areas |
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

curl -X POST 'localhost:8000/api/v1/proposals/evaluate/file?area=chemistry' -F file=@proposal.pdf
```

Or with the bundled command-line client (uploads a PDF / .txt / .md):

```bash
export PROPOSAL_REVIEWER_API_KEY=...          # only if server.api_keys is set
uv run proposal-reviewer-client proposal.pdf --skills scientific-merit,methodology \
    --metadata '{"requested_hours": 48}' --area chemistry   # --url, --json for raw output
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
  "area": null,
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

### Subject areas

Proposals can be evaluated for a subject area with the optional `area` query parameter
(`?area=chemistry`, CLI client: `--area chemistry`). Each area has its own skill directory:

```
skills/
  scientific-merit/SKILL.md      general skills, used for every proposal
  methodology/SKILL.md
  areas/
    chemistry/
      safety/SKILL.md            added for chemistry proposals
      methodology/SKILL.md       replaces the general methodology skill for chemistry
```

- With an area, its skills are added to the general skills; an area skill with the same name
  replaces the general one (also its weight). To drop a general skill for an area, add an area
  skill with that name and `enabled: false`.
- The skill selection rules above then apply to this merged set.
- Without `area`, only the general skills are used. An unknown area is rejected (400).
- The area name is also passed to the AI (`Subject area: …`) and returned as `area` in the response.
- Areas are created by adding a directory, or with `PUT /api/v1/skills/{name}?area=…` (admin key).
  `areas` is reserved and cannot be used as a skill name.

### General context

Background information that the reviewer gets with every proposal, in all subject areas (for
example what each technology of the facility can and cannot do). It is stored in
`skills/CONTEXT.md` and placed in the system prompt before the skills.

An admin can have the AI write it from a prompt:

```bash
curl -X POST localhost:8000/api/v1/context/generate \
  -H "X-API-Key: $PROPOSAL_REVIEWER_ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"prompt": "Find all areas in https://www.knmf.kit.edu/technologies.php and summarize for each technology the important information"}'
```

- The result replaces the current context and is returned together with the token usage.
  `"save": false` only returns it (preview). The request can take several minutes.
- `provider: anthropic`: Claude reads the pages itself with the web search / web fetch server
  tools (`ai.web_tools`) and can follow links as needed.
- `provider: openai`: the service downloads the URLs named in the prompt plus up to
  `ai.web_max_linked_pages` pages they link to on the same site (linked pages over 50 000
  characters are skipped) and hands the text to the model. The model needs a context window
  large enough for that; lower the setting otherwise.
- `GET /api/v1/context` shows the context and the prompt it was generated from. It can also be
  written with `PUT /api/v1/context` (`{"content": "…"}`) or by editing `skills/CONTEXT.md`;
  the file is re-read on every request.
- Generation makes the server fetch the URLs in the prompt, so it is admin-only. Review the
  result: it becomes part of the reviewer's system prompt.

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
