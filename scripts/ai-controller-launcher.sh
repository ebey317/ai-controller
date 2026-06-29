#!/bin/bash
# Desktop / autostart wrapper for the AI Controller GTK launcher.
# Handles $HOME resolution so the .desktop file works on any user account.
set -euo pipefail
export AI_CONTROLLER_AUTOSTART=1
exec "${HOME}/ai-controller/.venv/bin/python3" "${HOME}/ai-controller/scripts/ai-controller-launcher.py"
