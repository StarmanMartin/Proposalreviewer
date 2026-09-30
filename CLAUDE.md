# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI web service that evaluates research/facility-access proposals with an LLM. Two things are configurable: the AI endpoint (Anthropic Messages API or any OpenAI-compatible `/chat/completions` server) and the evaluation "skills" (markdown `SKILL.md` files, each scored separately and weighted).

## Commands

Uses `uv` (Python ≥3.11; the local venv is 3.13).

```bash
uv sync                                   # install incl. dev deps
uv run proposal-reviewer                  # serve on :8000 (Swagger at /docs, spec at /openapi.json)
uv run proposal-reviewer --reload         # dev auto-reload; --config PATH, --host, --port also accepted
uv run pytest                             # all tests
uv run pytest tests/test_api.py::test_skill_roundtrip   # single test
docker compose up --build                 # container; mounts ./skills and ./config.yaml
docker compose build && docker compose push   # publish mstarman/knmfi-proposalreviewer:<tag>
./install.sh [dir]                        # end-user install from Docker Hub (copies config/skills/prompts out of the image)
uv run proposal-reviewer-context "PROMPT" [--dry-run]  # generate skills/CONTEXT.md from the CLI (context.py:main; install.sh runs it via docker compose exec)
uv run proposal-reviewer-client FILE [--area X]  # CLI client (proposal_reviewer/client.py) for the file-upload endpoint
```

Run against a local Ollama instead of Claude:
`AI_PROVIDER=openai AI_BASE_URL=http://localhost:11434/v1 AI_MODEL=phi4 uv run proposal-reviewer`

No linter/formatter is configured.

## Architecture

Request flow: `main.py` (routes, auth) → `evaluator.py` (skill selection, scoring) → `prompt.py` (system prompt, user message, JSON schema) → `providers.py` (AI call) → back to `evaluator.py` (validate into `EvaluationResult`, compute `weighted_score`).

- **App factory / DI** — `main.create_app(settings, provider)` builds the app; settings, `SkillRegistry` and provider are created in the lifespan and stored on `app.state`. Tests inject a `FakeProvider` and temp-dir settings through this factory rather than mocking. The module-level `app = create_app()` is what uvicorn loads; `get_settings()` is `lru_cache`d, and `--config` works by setting `PROPOSAL_REVIEWER_CONFIG` before uvicorn imports the app.
- **Config** (`config.py`) — `config.yaml` is loaded with `${VAR}` / `${VAR:-default}` env interpolation, then validated by pydantic models. Comma-separated strings are accepted for list fields (`server.api_keys`, `skills.default`). A relative `skills.directory` resolves against the config file's directory, not the CWD.
- **Skills** (`skills.py`) — `skills/<name>/SKILL.md` with YAML front matter (`name`, `description`, `weight`, `enabled`) + markdown body as instructions. `SkillRegistry` re-reads from disk on every call (no caching, edits apply without restart). Skill names are restricted by `NAME_PATTERN` (also guards path traversal in PUT/DELETE). Selection order: request `skills` → `skills.default` from config → all `enabled` skills; request `extra_skills` (inline, not stored) are appended; duplicates are rejected.
- **Subject areas** — optional `?area=` query parameter on the evaluate and skills endpoints. Area skills live in `skills/areas/<area>/<name>/SKILL.md`; `SkillRegistry.for_area()` returns a registry whose `list()`/`get()` merge general + area skills (area wins on name clash, `Skill.area` records the origin) and whose `save()`/`delete()` touch only the area's own directory. All selection logic runs unchanged on the merged set. `areas` is a reserved skill name.
- **General context** (`context.py`) — `<skills_dir>/CONTEXT.md` (optional front matter `prompt`, `model`, `generated_at`), re-read per request by `ContextStore` and inserted into the system prompt as a `<context>` block before the skills, for every area. `POST /api/v1/context/generate` (admin key) runs `Provider.generate(system, user) -> AIText` with `CONTEXT_SYSTEM_PROMPT`: `AnthropicProvider` uses the web search/fetch server tools (`ai.web_tools`) and resumes `pause_turn`; `OpenAICompatibleProvider` has no tools, so `fetch_pages` downloads the URLs in the prompt plus same-site linked pages (`ai.web_max_linked_pages`) with a separate credential-free httpx client. It lives in the skills directory because that is the volume mounted writable in Docker (`prompts/` is read-only).
- **System prompt** (`prompt.py`) — `BASE_SYSTEM_PROMPT` is the default; `prompts/system_prompt.md` (config `evaluation.system_prompt_file`) replaces it when present and non-empty, re-read per request. Placeholders are substituted with `str.replace`, not `format`, so custom markdown may contain braces. `prompts/system_prompt.example.md` must stay identical to `BASE_SYSTEM_PROMPT` (enforced by a test).
- **Answer schema** (`prompt.evaluation_schema`) — built per request with the selected skill names as an `enum`. For `provider: anthropic` it is enforced via structured outputs (`output_config.format`); for `openai` it is embedded in the user message and `response_format: json_object` is requested (toggle `ai.json_mode`). The schema must stay compatible with Anthropic structured outputs (all properties `required`, `additionalProperties: false`). When changing the answer shape, update both `evaluation_schema` and the `EvaluationResult`/`SkillEvaluation` models in `models.py`.
- **Scoring** — `overall_score` comes from the model; `weight` per skill and `weighted_score` are filled in by the service in `evaluator.py` from the skill weights (the model never sees/returns weights in the schema).
- **Providers** (`providers.py`) — implement the `Provider` protocol (`name`, `async evaluate(system, user, schema) -> AIAnswer`). `AnthropicProvider` uses the official SDK with streaming, caches the system prompt (`cache_control: ephemeral`), passes `effort`, and optionally enables the server-side refusal fallback beta (`FALLBACK_BETA` header + `fallbacks: "default"`; disable `ai.refusal_fallback` behind proxies). All endpoint failures are raised as `AIError(message, status_code)`, which `main.py` maps to the HTTP status (502 default, 503 rate limit, 504 unreachable, 422 refusal). `SkillError` maps to 400. `parse_json_answer` tolerates code fences/prose around JSON.
- **Security model** — the proposal text is untrusted: it is wrapped in `<proposal>` tags and the system prompt tells the model to treat it as data only. Evaluation/read endpoints require an `X-API-Key` from `server.api_keys` (empty = open); skill PUT/DELETE and context PUT/DELETE/generate require `server.admin_api_key` and are disabled (403) when it is empty.
- **File uploads** — `/api/v1/proposals/evaluate/file` accepts PDF (via `pypdf`), `.txt`, `.md`, max 20 MB; form fields `skills` (comma-separated) and `metadata` (JSON string).

`tests/test_api.py::test_shipped_config_and_skills_load` checks that the shipped `config.yaml` and the four skills in `skills/` (`scientific-merit`, `methodology`, `feasibility`, `impact`) load, so keep those valid when editing them.
