#!/usr/bin/env bash
set -euo pipefail

MODE=${1:-uv}

if [[ "$MODE" == "uv" ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found; installing..." >&2
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  echo "Syncing dependencies (dev + examples) with uv..."
  uv sync --extra dev --extra examples
  echo "Done. Use: uv run pytest -q"
else
  PYBIN=${PYBIN:-python3}
  if [[ ! -d .venv ]]; then
    echo "Creating virtual environment (.venv) using $PYBIN" >&2
    $PYBIN -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip
  echo "Installing editable package with dev,examples extras..."
  pip install -e .[dev,examples]
  echo "Done. Activate later with: source .venv/bin/activate"
fi
