#!/bin/zsh
set -e
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  GRIDWISE_PYTHON=""
  for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
      GRIDWISE_PYTHON="$(command -v "$candidate")"
      break
    fi
  done
  if [[ -z "$GRIDWISE_PYTHON" && -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]]; then
    GRIDWISE_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  fi
  if [[ -z "$GRIDWISE_PYTHON" ]]; then
    print "Install Python 3.12 or newer, then run this launcher again."
    read "?Press Enter to close."
    exit 1
  fi
  "$GRIDWISE_PYTHON" -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
if [[ ! -f models/demand_model.joblib ]]; then
  .venv/bin/python train.py
fi
print "Starting Gridwise. Keep this window open; press Control-C to stop."
if command -v open >/dev/null 2>&1; then
  (sleep 2; open "http://127.0.0.1:${PORT:-8000}") &
fi
exec .venv/bin/python app.py
