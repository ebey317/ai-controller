#!/bin/bash
# One-time wiring: swap Cinnamon's native Menu applet (Super_L/Super_R) for
# the AI Controller rofi launcher. Run once rofi is installed
# (sudo apt install -y rofi). Safe to re-run.
#
# What this does, in order:
#   1. Disables the Menu applet's own overlay-key (its private Clutter-level
#      Super_L/Super_R listener -- see ai-rofi-launcher.sh for why that popup
#      is unreachable by xdotool-based typing at all).
#   2. Registers a Cinnamon custom keybinding on Super_L/Super_R that runs
#      the rofi launcher instead -- a normal X11 window, so it works with
#      the existing focus_guard-verified typing pipeline.
#
# A backup of the pre-change Menu applet config is at
# ~/ai-controller/config/menu-applet-0.json.bak (already saved before this
# script ever runs). To revert: copy it back over
# ~/.config/cinnamon/spices/menu@cinnamon.org/0.json.
set -euo pipefail

if ! command -v rofi >/dev/null 2>&1; then
    echo "rofi is not installed yet -- run: sudo apt install -y rofi" >&2
    exit 1
fi

MENU_CONFIG="$HOME/.config/cinnamon/spices/menu@cinnamon.org/0.json"
LAUNCHER="$HOME/ai-controller/scripts/ai-rofi-launcher.sh"

echo "Disabling the Menu applet's native overlay-key..."
python3 - "$MENU_CONFIG" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
data["overlay-key"]["value"] = ""
with open(path, "w") as f:
    json.dump(data, f, indent=4)
print(f"  {path}: overlay-key value -> ''")
PYEOF

echo "Registering the custom keybinding..."
EXISTING=$(gsettings get org.cinnamon.desktop.keybindings custom-list 2>/dev/null || echo "@as []")
if [[ "$EXISTING" != *"ai-rofi-launcher"* ]]; then
    # Append our custom binding id to whatever custom bindings already exist.
    NEW_LIST=$(python3 -c "
import ast
existing = ast.literal_eval('''$EXISTING'''.replace('@as ', ''))
existing = list(existing) if existing else []
if 'ai-rofi-launcher' not in existing:
    existing.append('ai-rofi-launcher')
print(existing)
")
    gsettings set org.cinnamon.desktop.keybindings custom-list "$NEW_LIST"
fi
BINDING_PATH="/org/cinnamon/desktop/keybindings/custom-keybindings/ai-rofi-launcher/"
gsettings set "org.cinnamon.desktop.keybindings.custom-keybinding:$BINDING_PATH" name "AI Controller Launcher"
gsettings set "org.cinnamon.desktop.keybindings.custom-keybinding:$BINDING_PATH" command "$LAUNCHER"
gsettings set "org.cinnamon.desktop.keybindings.custom-keybinding:$BINDING_PATH" binding "['Super_L', 'Super_R']"

echo "Done. Press Super to test -- it should open the AI Controller launcher, not the native menu."
echo "If Cinnamon doesn't pick up the applet change live, log out/in (or run: cinnamon --replace &disown) once."
