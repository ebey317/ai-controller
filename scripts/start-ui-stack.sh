#!/bin/bash
# Start just the on-screen UI (legend + keyboard).
set -euo pipefail
systemctl --user start controller-legend.service ai-slide-keyboard.service
echo "UI stack started."
