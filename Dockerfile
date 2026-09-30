FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY proposal_reviewer ./proposal_reviewer
COPY config.yaml ./
COPY skills ./skills
COPY prompts ./prompts
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "proposal-reviewer", "--host", "0.0.0.0", "--port", "8000"]
