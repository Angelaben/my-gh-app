#!/usr/bin/env bash
# Launch backend (uvicorn) + frontend (vite dev) in a split tmux session.
# Automatically runs `npm install` and `npm run build` before starting so
# that a plain `git pull && ./launch.sh` is always enough to pick up changes.
#
# Usage:
#   ./launch.sh                          # default provider (opencode), port 8000
#   ./launch.sh --port 9000              # custom backend port
#   ./launch.sh --provider claude-code   # override AI provider
#   ./launch.sh -p opencode -P 9000      # provider + port
#   ./launch.sh my-session               # custom tmux session name
#   ./launch.sh --no-build               # skip npm install/build (fast restart)
#   API_PORT=9000 ./launch.sh            # port via env var
set -euo pipefail

PROVIDER="${AI_PROVIDER:-opencode}"
PORT="${API_PORT:-8000}"
SESSION=""
BUILD=1

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
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      if [[ -z "$SESSION" ]]; then
        SESSION="$1"; shift
      else
        echo "Unexpected argument: $1" >&2; exit 2
      fi ;;
  esac
done

SESSION="${SESSION:-gh-review}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required but not installed." >&2; exit 1
fi

# ── Frontend build ──────────────────────────────────────────────────────────
if [[ $BUILD -eq 1 ]]; then
  echo "→ npm install…"
  (cd "$ROOT/frontend" && npm install --silent 2>&1 | grep -v "^npm warn\|^$" || true)
  echo "→ Building frontend…"
  (cd "$ROOT/frontend" && npm run build)
  echo "→ Build done."
fi

# ── tmux session ────────────────────────────────────────────────────────────
echo "→ AI provider : $PROVIDER"
echo "→ Port        : $PORT"
echo "→ Session     : $SESSION"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "→ tmux session '$SESSION' already exists — attaching."
  exec tmux attach -t "$SESSION"
fi

tmux new-session -d -s "$SESSION" -c "$ROOT" -n app \
  "AI_PROVIDER='$PROVIDER' uv run uvicorn app.main:app --reload --port '$PORT'"

tmux split-window -h -t "$SESSION:app" -c "$ROOT/frontend" \
  "API_PORT='$PORT' npm run dev"

tmux select-pane -t "$SESSION:app.0"

exec tmux attach -t "$SESSION"
