#!/usr/bin/env bash
# Build and run gh-review-tool in Docker — the default way to run this tool.
# Wraps `docker compose` with the same flags launch.sh exposes, so switching
# from the native tmux launcher is a one-word change.
#
# Usage:
#   ./docker-launch.sh                          # default provider (opencode), port 8000
#   ./docker-launch.sh --port 9000               # custom host port
#   ./docker-launch.sh --provider claude-code    # override AI provider
#   ./docker-launch.sh -p opencode -P 9000       # provider + port
#   ./docker-launch.sh --no-build                # reuse the existing image (skip rebuild)
#   ./docker-launch.sh -d                        # run detached (background)
#   ./docker-launch.sh --down                    # stop and remove the container
#   API_PORT=9000 ./docker-launch.sh             # port via env var
set -euo pipefail

PROVIDER="${AI_PROVIDER:-opencode}"
PORT="${API_PORT:-8000}"
BUILD=1
DETACH=0
ACTION=up

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--provider)
      PROVIDER="$2"; shift 2 ;;
    --provider=*)
      PROVIDER="${1#*=}"; shift ;;
    -P|--port)
      PORT="$2"; shift 2 ;;
    --port=*)
      PORT="${1#*=}"; shift ;;
    --no-build)
      BUILD=0; shift ;;
    -d|--detach)
      DETACH=1; shift ;;
    --down)
      ACTION=down; shift ;;
    -h|--help)
      sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "Unexpected argument: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose -f "$ROOT/docker-compose.yml")

if [[ "$ACTION" == "down" ]]; then
  echo "→ Stopping container…"
  exec "${COMPOSE[@]}" down
fi

case "$PROVIDER" in
  opencode|claude-code) ;;
  *)
    echo "Unknown AI provider: '$PROVIDER' (supported: opencode, claude-code)" >&2
    exit 2 ;;
esac

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [[ "$PORT" -lt 1 ]] || [[ "$PORT" -gt 65535 ]]; then
  echo "Invalid port: '$PORT' (must be 1–65535)" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but not installed." >&2; exit 1
fi

export AI_PROVIDER="$PROVIDER"
export API_PORT="$PORT"

echo "→ AI provider : $PROVIDER"
echo "→ Port        : $PORT"

RUN_ARGS=(up)
[[ $BUILD -eq 1 ]] && RUN_ARGS+=(--build)
[[ $DETACH -eq 1 ]] && RUN_ARGS+=(-d)

exec "${COMPOSE[@]}" "${RUN_ARGS[@]}"
