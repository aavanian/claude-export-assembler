#!/bin/bash
set -euo pipefail

# Locally, fix what can be fixed; under CI, only report it.
format=(uv run ruff format)
lint=(uv run ruff check)
if [ -n "${CI:-}" ]; then
    format+=(--check --diff)
else
    lint+=(--fix)
fi

echo "=== Running ruff format ==="
"${format[@]}" claude_export.py tests

echo "=== Running ruff check ==="
"${lint[@]}" claude_export.py tests

echo "=== Running pytest ==="
uv run pytest -v

echo "=== All checks passed ==="
