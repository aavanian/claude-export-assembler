#!/bin/bash
set -euo pipefail

echo "=== Running ruff format ==="
uv run ruff format claude_export.py tests

echo "=== Running ruff check ==="
uv run ruff check --fix claude_export.py tests

echo "=== Running pytest ==="
uv run pytest -v

echo "=== All checks passed ==="
