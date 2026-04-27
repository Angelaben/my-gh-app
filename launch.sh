#!/usr/bin/env bash
# Launch backend (uvicorn) and frontend (vite) in a split tmux session.
# Usage: ./launch.sh [session-name]
set -euo pipefail

SESSION="${1:-gh-review}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required but not installed." >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists — attaching."
  exec tmux attach -t "$SESSION"
fi

tmux new-session -d -s "$SESSION" -c "$ROOT" -n app \
  "uv run uvicorn app.main:app --reload"

tmux split-window -h -t "$SESSION:app" -c "$ROOT/frontend" \
  "npm run dev"

tmux select-pane -t "$SESSION:app.0"

exec tmux attach -t "$SESSION"
