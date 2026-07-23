# syntax=docker/dockerfile:1
#
# gh-review-tool — container image
#
# Three stages:
#   1. frontend-build — compiles the Svelte 5 frontend to static assets.
#   2. ai-clis        — installs the opencode / Claude Code CLIs (both are
#                        plain npm packages, so this stage is just node:20).
#   3. runtime        — the actual image: Python backend + git + gh CLI +
#                        the two AI CLIs copied over from stage 2, serving
#                        the built frontend as static files on :8000.
#
# Credentials for `gh`, `opencode` and `claude` are NOT baked into the image
# — they're mounted from the host at runtime (see docker-compose.yml), the
# same "bring your own authenticated CLI" model as the native install.

# ---- Stage 1: frontend build ------------------------------------------------
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build
# vite.config.ts sets outDir: '../dist', so the build lands at /dist, not
# /frontend/dist — matches the project-root-relative "dist" main.py serves.

# ---- Stage 2: AI provider CLIs ----------------------------------------------
FROM node:20-slim AS ai-clis
RUN --mount=type=cache,target=/root/.npm \
    npm install -g opencode-ai @anthropic-ai/claude-code

# ---- Stage 3: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime

# git: required by the worktree/fix flow. curl+gnupg: only needed transiently
# to add the gh CLI apt repo, then removed to keep the image lean.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg \
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && apt-get purge -y gnupg && apt-get autoremove -y

# Node runtime + the two AI CLIs, copied from the ai-clis stage (no npm
# registry access needed in this stage, and no npm/npx carried over — the
# CLIs only need `node` to execute their shebang).
COPY --from=ai-clis /usr/local/bin/node /usr/local/bin/node
COPY --from=ai-clis /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=ai-clis /usr/local/bin/opencode /usr/local/bin/opencode
COPY --from=ai-clis /usr/local/bin/claude /usr/local/bin/claude

# uv, copied straight from Astral's image (no installer script needed).
COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /usr/local/bin/uv

WORKDIR /app

# Install Python deps in their own layer so `uv sync` is cached across
# frontend/app code changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY --from=frontend-build /dist ./dist

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
