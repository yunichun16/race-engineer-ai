# The API (tools, chat, /mcp) as a container: the fallback to Modal, for Google Cloud Run or any
# container host (plan M7 section 6.5; docs/DEPLOY.md has the Cloud Run commands). The build
# context is the repository root, whose .dockerignore keeps the data, the virtual environments
# and node_modules out of it:
#
#     docker build -f engine/Dockerfile -t race-engineer-api .
#     docker run -p 8080:8080 -v "$PWD/data/serve:/data:ro" -e RACE_ENGINEER_CHAT_SECRET=... \
#         -e ANTHROPIC_API_KEY -e UPSTASH_REDIS_REST_URL -e UPSTASH_REDIS_REST_TOKEN race-engineer-api
#
# Run with no proxy in front (plain `docker run`), add -e RACE_ENGINEER_TRUSTED_PROXY_HOPS=0: the
# image trusts one proxy's X-Forwarded-For entry, as on Cloud Run.
#
# No data in the image: the served-data bundle (make serve-data) is mounted at /data, read-only
# (on Cloud Run, a Cloud Storage bucket mounted as a volume). The secrets come from the host's
# secret store, never from the image.

# --- 1. The chart bundles the MCP server serves (gitignored, so built here) ---------------------
FROM node:24-slim AS charts
WORKDIR /repo/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Vite writes them to ../engine/src/race_engineer/mcp_server/ui/.
RUN npm run build:mcp-app

# --- 2. The API ------------------------------------------------------------------------------
FROM python:3.13-slim
# The uv that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app/engine

# The serving dependencies only (no `train` or `dev` group: no torch, CUDA, MLflow or FastF1),
# in a layer of their own that code changes don't rebuild.
COPY engine/pyproject.toml engine/uv.lock ./
RUN uv sync --locked --no-default-groups --no-install-project

# Then the project itself, with the chart bundles, installed as a package: that also installs
# the `race-engineer-api` command.
COPY engine/README.md ./
COPY engine/src ./src
COPY --from=charts /repo/engine/src/race_engineer/mcp_server/ui ./src/race_engineer/mcp_server/ui
RUN uv sync --locked --no-default-groups --no-editable

# A public host (the MCP SDK then accepts the host's Host headers, and the limits apply),
# stateless MCP, the limits on with the Upstash store, and the client address taken from the
# last X-Forwarded-For entry, which Cloud Run's front end appends (check it on the first deploy).
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    RACE_ENGINEER_DATA=/data \
    RACE_ENGINEER_HOST=0.0.0.0 \
    RACE_ENGINEER_MCP_STATELESS=1 \
    RACE_ENGINEER_TRUSTED_PROXY_HOPS=1 \
    RACE_ENGINEER_LIMITS=on \
    RACE_ENGINEER_LIMITS_STORE=upstash \
    PORT=8080

# Not root; a home of its own, for any library that keeps a cache there.
RUN useradd --system --create-home --shell /usr/sbin/nologin api
USER api
EXPOSE 8080

# For Docker itself; Cloud Run uses its own startup probe (point it at /api/ready).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8080')}/api/ready\", timeout=4)"]

# The port comes from $PORT; no access log (it prints every client's address).
CMD ["race-engineer-api", "--host", "0.0.0.0", "--no-access-log"]
