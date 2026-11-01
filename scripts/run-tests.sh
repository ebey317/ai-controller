#!/usr/bin/env bash
# run-tests.sh -- run AI Controller's pytest suite through the canonical venv.
#
# Hermes Agent and some parent shells leak PYTHONPATH pointing at a different
# Python version's site-packages. That makes the canonical .venv/bin/python
# import packages compiled for the wrong interpreter (e.g. pydantic_core for
# Python 3.14) and crash. This wrapper removes the leaked PYTHONPATH before
# invoking pytest so the canonical venv is isolated.
#
# Usage:
#   bash scripts/run-tests.sh
#   bash scripts/run-tests.sh tests/test_utils.py -v

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO/.venv/bin/python3"

if [[ ! -x "$VENV_PY" ]]; then
    echo "error: canonical venv python not found or not executable: $VENV_PY" >&2
    exit 1
fi

cd "$REPO"
exec env -u PYTHONPATH "$VENV_PY" -m pytest "$@"
