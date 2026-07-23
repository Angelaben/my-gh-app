#!/usr/bin/env bash
# Build and run gh-review-tool in Docker — the default way to run this tool.
# Wraps `docker compose` with the same flags launch.sh exposes, so switching
# from the native tmux launcher is a one-word change.
#
# Usage:
#   ./docker-launch.sh                          # default provider (opencode), port 4500, detached
#   ./docker-launch.sh --port 9000               # custom host port
#   ./docker-launch.sh --provider claude-code    # override AI provider
#   ./docker-launch.sh -p opencode -P 9000       # provider + port
#   ./docker-launch.sh --mode review-auto        # auto-start the PR watcher at boot
#   ./docker-launch.sh --no-build                # reuse the existing image (skip rebuild)
#   ./docker-launch.sh -a                        # run attached (foreground) instead of detached
#   ./docker-launch.sh --down                    # stop and remove the container
#   API_PORT=9000 ./docker-launch.sh             # port via env var
set -euo pipefail

PROVIDER="${AI_PROVIDER:-opencode}"
PORT="${API_PORT:-4500}"
MODE=normal
BUILD=1
DETACH=1
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
    -m|--mode)
      MODE="$2"; shift 2 ;;
    --mode=*)
      MODE="${1#*=}"; shift ;;
    --no-build)
      BUILD=0; shift ;;
    -a|--attach)
      DETACH=0; shift ;;
    --down)
      ACTION=down; shift ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
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

case "$MODE" in
  normal)      AUTOSTART=0 ;;
  review-auto) AUTOSTART=1 ;;
  *)
    echo "Unknown mode: '$MODE' (supported: normal, review-auto)" >&2
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
export LIVE_REVIEW_AUTOSTART="$AUTOSTART"

echo "→ AI provider : $PROVIDER"
echo "→ Port        : $PORT"
echo "→ Mode        : $MODE"
echo "→ Detached    : $([[ $DETACH -eq 1 ]] && echo yes || echo no)"

RUN_ARGS=(up)
[[ $BUILD -eq 1 ]] && RUN_ARGS+=(--build)
[[ $DETACH -eq 1 ]] && RUN_ARGS+=(-d)

exec "${COMPOSE[@]}" "${RUN_ARGS[@]}"
