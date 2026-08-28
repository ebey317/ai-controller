#!/usr/bin/env bash
# doctor.sh -- catches the whole "repo moved / venv incomplete / unit drifted"
# failure class in one command instead of discovering it as N crash-looping
# services and a dead desktop icon.
#
# Run it any time, especially after moving the repo or rebuilding .venv:
#   bash scripts/doctor.sh
#
# Exits 0 if everything checks out, 1 if anything is broken (with a list of
# exactly what and where).

set -u
INSTALL_DIR="${AI_CONTROLLER_DIR:-$HOME/ai-controller}"
SERVICE_DIR="$HOME/.config/systemd/user"
FAILS=0

ok()   { echo "  OK    $1"; }
fail() { echo "  FAIL  $1"; FAILS=$((FAILS + 1)); }

echo "== install location =="
if [[ -d "$INSTALL_DIR/.git" ]]; then
    ok "$INSTALL_DIR is a real repo"
else
    fail "$INSTALL_DIR is missing or not a git repo -- everything below this depends on it"
fi

echo "== python venv =="
VENV_PY="$INSTALL_DIR/.venv/bin/python3"
if [[ -x "$VENV_PY" ]]; then
    # Parse requirements.txt into import names by hand for the couple of
    # packages whose import name doesn't match their pip name.
    mapfile -t REQ_PKGS < "$INSTALL_DIR/requirements.txt"
    for pkg in "${REQ_PKGS[@]}"; do
        [[ -z "$pkg" ]] && continue
        case "$pkg" in
            pycairo) mod=cairo ;;
            edge-tts) mod=edge_tts ;;
            *) mod="$pkg" ;;
        esac
        if "$VENV_PY" -c "import $mod" 2>/dev/null; then
            ok "venv can import $pkg"
        else
            fail "venv cannot import $pkg (pip install -r requirements.txt inside .venv)"
        fi
    done
    # gi (PyGObject) isn't in requirements.txt -- it's a system package
    # reached via --system-site-packages, not pip.
    if "$VENV_PY" -c "import gi" 2>/dev/null; then
        ok "venv can import gi (PyGObject, via system-site-packages)"
    else
        fail "venv cannot import gi -- was .venv created with --system-site-packages?"
    fi
else
    fail "$VENV_PY missing -- run install.sh"
fi

echo "== systemd services =="
SERVICES=$(grep -oP '^\s{4}\K[a-z0-9._-]+\.service' "$INSTALL_DIR/install.sh" 2>/dev/null)
if [[ -z "$SERVICES" ]]; then
    fail "could not read the SERVICES list out of install.sh"
else
    while IFS= read -r svc; do
        template="$INSTALL_DIR/systemd/$svc"
        deployed="$SERVICE_DIR/$svc"
        if [[ ! -f "$template" ]]; then
            fail "$svc is in install.sh's SERVICES list but has no template at systemd/$svc"
            continue
        fi
        if [[ ! -f "$deployed" ]]; then
            fail "$svc has a template but isn't installed at $deployed (run install.sh)"
            continue
        fi
        # Every path-looking token in the deployed unit must actually exist,
        # after expanding the %h systemd specifier to $HOME.
        bad_path=0
        for tok in $(grep -E '^(ExecStart|ExecStartPre|WorkingDirectory)=' "$deployed" | grep -oE '(%h|/[^ ]*)?/[A-Za-z0-9_./-]+'); do
            resolved="${tok/#%h/$HOME}"
            [[ "$resolved" == /* ]] || continue
            if [[ ! -e "$resolved" ]]; then
                fail "$svc references missing path: $resolved"
                bad_path=1
            fi
        done
        [[ $bad_path -eq 0 ]] && ok "$svc paths resolve"
        # oneshot units (e.g. f13-xmodmap-heal) are supposed to run once and
        # go inactive -- judge them by last-run result, not by is-active.
        unit_type=$(grep -oP '^Type=\K.*' "$deployed" 2>/dev/null)
        if [[ "$unit_type" == "oneshot" ]]; then
            result=$(systemctl --user show "$svc" -p Result --value 2>/dev/null)
            if [[ "$result" == "success" ]]; then
                ok "$svc (oneshot) last run succeeded"
            else
                fail "$svc (oneshot) last run did not succeed (Result: ${result:-unknown})"
            fi
        else
            state=$(systemctl --user is-active "$svc" 2>/dev/null || true)
            if [[ "$state" != "active" ]]; then
                fail "$svc is not active (state: ${state:-unknown})"
            fi
        fi
    done <<< "$SERVICES"
fi

echo "== desktop launchers =="
for desktop_file in "$HOME/Desktop/AI-Controller.desktop" "$HOME/.config/autostart/ai-controller-launcher.desktop"; do
    [[ -f "$desktop_file" ]] || continue
    exec_path=$(grep -oP '^Exec=\K.*' "$desktop_file")
    if [[ -x "$exec_path" ]]; then
        ok "$(basename "$desktop_file") Exec resolves"
    else
        fail "$(basename "$desktop_file") Exec points at missing/non-executable: $exec_path"
    fi
done

echo "== antimicrox profile =="
PROFILE_PATH=$(grep -oP '(?<=LastSelected=).*' "$HOME/.config/antimicrox/antimicrox_settings.ini" 2>/dev/null | head -1)
if [[ -n "$PROFILE_PATH" ]]; then
    if [[ -f "$PROFILE_PATH" ]]; then
        ok "antimicrox's last-selected profile resolves: $PROFILE_PATH"
    else
        fail "antimicrox's last-selected profile is missing: $PROFILE_PATH"
    fi
fi

echo ""
if [[ $FAILS -eq 0 ]]; then
    echo "All checks passed."
    exit 0
else
    echo "$FAILS check(s) failed -- see FAIL lines above."
    exit 1
fi
