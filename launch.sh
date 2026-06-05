#!/usr/bin/env bash
# Launch backend (uvicorn) + frontend (vite dev) in a split tmux session.
# Automatically runs `npm install` and `npm run build` before starting so
# that a plain `git pull && ./launch.sh` is always enough to pick up changes.
#
# Usage:
#   ./launch.sh                          # default provider (opencode), auto-build
#   ./launch.sh --provider claude-code   # override AI provider
#   ./launch.sh -p opencode my-session   # provider + custom tmux session name
#   ./launch.sh --no-build               # skip npm install/build (fast restart)
#   AI_PROVIDER=claude-code ./launch.sh  # provider via env var
set -euo pipefail

PROVIDER="${AI_PROVIDER:-opencode}"
SESSION=""
BUILD=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--provider)
      PROVIDER="$2"; shift 2 ;;
    --provider=*)
      PROVIDER="${1#*=}"; shift ;;
    --no-build)
      BUILD=0; shift ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
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
echo "→ Session     : $SESSION"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "→ tmux session '$SESSION' already exists — attaching."
  exec tmux attach -t "$SESSION"
fi

tmux new-session -d -s "$SESSION" -c "$ROOT" -n app \
  "AI_PROVIDER='$PROVIDER' uv run uvicorn app.main:app --reload"

tmux split-window -h -t "$SESSION:app" -c "$ROOT/frontend" \
  "npm run dev"

tmux select-pane -t "$SESSION:app.0"

exec tmux attach -t "$SESSION"
