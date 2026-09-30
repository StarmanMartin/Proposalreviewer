#!/usr/bin/env bash
# Installs the Proposal Reviewer from Docker Hub into a local directory and starts it.
#
#   ./install.sh [install-dir]            (default: ./proposal-reviewer)
#   curl -fsSL <url>/install.sh | bash -s -- [install-dir]
#
# Creates in the install directory:
#   docker-compose.yml   runs the image
#   .env                 AI endpoint and API keys (asked for interactively)
#   config.yaml          service configuration
#   skills/              evaluation skills (SKILL.md files)
#   prompts/             optional custom system prompt (system_prompt.md)
# Existing files are never overwritten, so re-running the script upgrades the image only.
#
# Non-interactive use: set the answers as environment variables, e.g.
#   AI_PROVIDER=openai AI_BASE_URL=https://ki-toolbox.scc.kit.edu/api AI_API_KEY=... AI_MODEL=... ./install.sh
# Other variables: IMAGE (default below), PORT (default 8000), NO_START=1 (do not start the service).

set -euo pipefail

IMAGE="${IMAGE:-mstarman/knmfi-proposalreviewer:0.0.1}"
PORT="${PORT:-8000}"
DIR="${1:-proposal-reviewer}"
KI_TOOLBOX_URL="https://ki-toolbox.scc.kit.edu/api"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# Read an answer from the terminal (also works with curl | bash). Usage: ask VAR "Question" "default"
ask() {
    local var="$1" question="$2" default="${3:-}" answer=""
    if [ -n "${!var:-}" ]; then return; fi
    if [ -r /dev/tty ]; then
        if [ -n "$default" ]; then question="$question [$default]"; fi
        read -r -p "$question: " answer </dev/tty || true
    fi
    printf -v "$var" '%s' "${answer:-$default}"
}

random_key() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 24
    else
        head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
}

# --- requirements -----------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Docker is not installed: https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon (is it running? is your user in the 'docker' group?)"
docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is missing: https://docs.docker.com/compose/install/"

# --- image ------------------------------------------------------------------
info "Pulling $IMAGE"
if ! docker pull "$IMAGE"; then
    docker image inspect "$IMAGE" >/dev/null 2>&1 || die "Cannot pull $IMAGE"
    warn "Pull failed, using the local image $IMAGE"
fi

mkdir -p "$DIR"
cd "$DIR"
info "Installing into $(pwd)"

# --- default config, skills and prompts, copied out of the image -------------
missing=()
[ -e config.yaml ] || missing+=(config.yaml)
[ -e skills ] || missing+=(skills)
[ -e prompts ] || missing+=(prompts)
if [ ${#missing[@]} -gt 0 ]; then
    info "Copying defaults from the image: ${missing[*]}"
    container=$(docker create "$IMAGE")
    trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
    for item in "${missing[@]}"; do
        docker cp "$container:/app/$item" "./$item"
    done
    docker rm -f "$container" >/dev/null
    trap - EXIT
fi

# --- docker-compose.yml -----------------------------------------------------
if [ ! -e docker-compose.yml ]; then
    info "Writing docker-compose.yml"
    cat >docker-compose.yml <<EOF
services:
  proposal-reviewer:
    image: ${IMAGE}
    restart: unless-stopped
    ports:
      - "${PORT}:8000"
    env_file: .env
    volumes:
      # edit skills, config and the system prompt here; no rebuild needed
      - ./skills:/app/skills
      - ./config.yaml:/app/config.yaml:ro
      - ./prompts:/app/prompts:ro
EOF
else
    info "Keeping existing docker-compose.yml (update the image tag there to upgrade)"
fi

# --- .env -------------------------------------------------------------------
if [ ! -e .env ]; then
    echo
    echo "Which AI endpoint should review the proposals?"
    echo "  1) KIT KI-Toolbox ($KI_TOOLBOX_URL)"
    echo "  2) Anthropic Claude API"
    echo "  3) Other OpenAI-compatible endpoint (Ollama, vLLM, OpenAI, ...)"
    if [ -z "${AI_PROVIDER:-}" ]; then
        ask CHOICE "Choice" "1"
        case "$CHOICE" in
            1) AI_PROVIDER=openai; AI_BASE_URL="${AI_BASE_URL:-$KI_TOOLBOX_URL}" ;;
            2) AI_PROVIDER=anthropic ;;
            3) AI_PROVIDER=openai ;;
            *) die "Invalid choice: $CHOICE" ;;
        esac
    fi

    if [ "$AI_PROVIDER" = "anthropic" ]; then
        ask AI_API_KEY "Anthropic API key (sk-ant-...)"
        ask AI_MODEL "Model" "claude-opus-5-5"
        AI_BASE_URL="${AI_BASE_URL:-}"
    else
        ask AI_BASE_URL "Base URL of the endpoint (without /chat/completions)" "$KI_TOOLBOX_URL"
        ask AI_API_KEY "API key (KI-Toolbox: Settings > Account > API Key)"
        ask AI_MODEL "Model ID (list them with: curl -H 'Authorization: Bearer KEY' ${AI_BASE_URL%/}/models)"
        [ -n "$AI_MODEL" ] || warn "No model set: edit AI_MODEL in $(pwd)/.env before using the service"
    fi
    [ -n "${AI_API_KEY:-}" ] || warn "No API key set: edit AI_API_KEY in $(pwd)/.env"

    CLIENT_KEY="${PROPOSAL_REVIEWER_API_KEYS:-$(random_key)}"
    ADMIN_KEY="${PROPOSAL_REVIEWER_ADMIN_KEY:-$(random_key)}"

    info "Writing .env"
    old_umask=$(umask)
    umask 077
    cat >.env <<EOF
# AI endpoint: "anthropic" or "openai" (any OpenAI-compatible endpoint, e.g. KIT KI-Toolbox)
AI_PROVIDER=${AI_PROVIDER}
AI_BASE_URL=${AI_BASE_URL:-}
AI_API_KEY=${AI_API_KEY:-}
AI_MODEL=${AI_MODEL:-}
# Keys clients must send in the X-API-Key header (comma separated, empty = no authentication)
PROPOSAL_REVIEWER_API_KEYS=${CLIENT_KEY}
# Key for creating/updating/deleting skills via the API (empty = disabled)
PROPOSAL_REVIEWER_ADMIN_KEY=${ADMIN_KEY}
EOF
    umask "$old_umask"
else
    info "Keeping existing .env"
fi

# --- start ------------------------------------------------------------------
if [ "${NO_START:-}" = "1" ]; then
    info "Done. Start with: cd $(pwd) && docker compose up -d"
    exit 0
fi

info "Starting the service"
docker compose up -d

url="http://localhost:${PORT}"
if ! command -v curl >/dev/null 2>&1; then
    info "Started. Open $url/docs (API keys are in $(pwd)/.env)"
    exit 0
fi
for _ in $(seq 1 30); do
    if curl -fsS "$url/health" >/dev/null 2>&1; then
        client_key=$(grep -E '^PROPOSAL_REVIEWER_API_KEYS=' .env | cut -d= -f2- | cut -d, -f1)
        echo
        info "Proposal Reviewer is running: $url/docs"
        echo "    Directory:  $(pwd)"
        echo "    Client key: ${client_key:-<none, service is open>}   (header X-API-Key; admin key is in .env)"
        echo "    Try:        curl -H 'X-API-Key: ${client_key}' $url/api/v1/info"
        echo "    Logs:       docker compose logs -f      Stop: docker compose down"
        exit 0
    fi
    sleep 1
done
warn "The service did not answer on $url/health within 30 s; check: cd $(pwd) && docker compose logs"
exit 1
