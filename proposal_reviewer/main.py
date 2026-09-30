"""FastAPI application. OpenAPI spec: /openapi.json, interactive docs: /docs."""

import io
import json
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Security, UploadFile, status
from fastapi.security import APIKeyHeader
from pydantic import ValidationError

from . import __version__
from .config import Settings, get_settings
from .evaluator import evaluate_proposal
from .models import EvaluationResponse, ProposalRequest, SkillIn, SkillOut
from .providers import AIError, Provider, create_provider
from .skills import Skill, SkillError, SkillRegistry

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def create_app(settings: Settings | None = None, provider: Provider | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings or get_settings()
        app.state.registry = SkillRegistry(app.state.settings.skills_dir)
        app.state.provider = provider or create_provider(app.state.settings.ai)
        yield

    app = FastAPI(
        title="Proposal Reviewer",
        version=__version__,
        description=(
            "Submit a proposal and have it evaluated by an AI agent. The agent is guided by "
            "configurable evaluation skills (SKILL.md files) and a configurable AI endpoint."
        ),
        lifespan=lifespan,
    )

    def get_state(request: Request) -> tuple[Settings, SkillRegistry, Provider]:
        s = request.app.state
        return s.settings, s.registry, s.provider

    State = Annotated[tuple[Settings, SkillRegistry, Provider], Depends(get_state)]

    def require_api_key(state: State, key: Annotated[str | None, Security(api_key_header)]) -> None:
        allowed = state[0].server.api_keys
        if allowed and not any(secrets.compare_digest(key or "", k) for k in allowed):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-API-Key")

    def require_admin_key(state: State, key: Annotated[str | None, Security(api_key_header)]) -> None:
        admin = state[0].server.admin_api_key
        if not admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Skill editing is disabled (server.admin_api_key not set)")
        if not secrets.compare_digest(key or "", admin):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing admin X-API-Key")

    async def run_evaluation(body: ProposalRequest, state: tuple) -> EvaluationResponse:
        settings, registry, provider = state
        try:
            return await evaluate_proposal(body, provider, registry, settings)
        except SkillError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
        except AIError as e:
            raise HTTPException(e.status_code, str(e)) from e

    @app.get("/health", tags=["service"])
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/info", tags=["service"], dependencies=[Depends(require_api_key)])
    async def info(state: State) -> dict:
        """Active (non-secret) configuration."""
        settings, registry, _ = state
        return {
            "provider": settings.ai.provider,
            "model": settings.ai.model,
            "base_url": settings.ai.base_url or None,
            "default_skills": settings.skills.default or [s.name for s in registry.list() if s.enabled],
            "score_range": [settings.evaluation.score_min, settings.evaluation.score_max],
            "recommendations": settings.evaluation.recommendations,
        }

    @app.post(
        "/api/v1/proposals/evaluate",
        tags=["proposals"],
        response_model=EvaluationResponse,
        dependencies=[Depends(require_api_key)],
    )
    async def evaluate(body: ProposalRequest, state: State) -> EvaluationResponse:
        """Evaluate a proposal given as text."""
        return await run_evaluation(body, state)

    @app.post(
        "/api/v1/proposals/evaluate/file",
        tags=["proposals"],
        response_model=EvaluationResponse,
        dependencies=[Depends(require_api_key)],
    )
    async def evaluate_file(
        state: State,
        file: Annotated[UploadFile, File(description="Proposal as PDF, .txt or .md")],
        title: Annotated[str | None, Form()] = None,
        skills: Annotated[str | None, Form(description="Comma-separated skill names")] = None,
        metadata: Annotated[str | None, Form(description="JSON object with extra context")] = None,
    ) -> EvaluationResponse:
        """Evaluate a proposal uploaded as a file."""
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 20 MB)")
        text = extract_text(file.filename or "", file.content_type or "", data)
        try:
            body = ProposalRequest(
                title=title or file.filename,
                text=text,
                metadata=json.loads(metadata) if metadata else {},
                skills=[s.strip() for s in skills.split(",") if s.strip()] if skills else None,
            )
        except (json.JSONDecodeError, ValidationError) as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid form data: {e}") from e
        return await run_evaluation(body, state)

    @app.get("/api/v1/skills", tags=["skills"], response_model=list[SkillOut], dependencies=[Depends(require_api_key)])
    async def list_skills(state: State) -> list[Skill]:
        return state[1].list()

    @app.get(
        "/api/v1/skills/{name}", tags=["skills"], response_model=SkillOut, dependencies=[Depends(require_api_key)]
    )
    async def get_skill(name: str, state: State) -> Skill:
        return _get_or_404(state[1], name)

    @app.put(
        "/api/v1/skills/{name}", tags=["skills"], response_model=SkillOut, dependencies=[Depends(require_admin_key)]
    )
    async def put_skill(name: str, body: SkillIn, state: State) -> Skill:
        """Create or replace a skill (requires the admin key)."""
        try:
            return state[1].save(Skill(name=name, **body.model_dump()))
        except (SkillError, ValidationError) as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    @app.delete(
        "/api/v1/skills/{name}",
        tags=["skills"],
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin_key)],
    )
    async def delete_skill(name: str, state: State) -> None:
        """Delete a skill (requires the admin key)."""
        try:
            deleted = state[1].delete(name)
        except SkillError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Skill '{name}' not found")

    return app


def _get_or_404(registry: SkillRegistry, name: str) -> Skill:
    try:
        skill = registry.get(name)
    except SkillError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Skill '{name}' not found")
    return skill


def extract_text(filename: str, content_type: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf") or content_type == "application/pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:  # pypdf raises many different error types for broken files
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Cannot read PDF: {e}") from e
    else:
        text = data.decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No text could be extracted from the file")
    return text


app = create_app()
