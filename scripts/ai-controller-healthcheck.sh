#!/bin/bash
# Healthcheck: if Xbox controller is on USB but not an input device, fix it.
set -euo pipefail
if lsusb | grep -q '045e:0b12' && ! xinput list | grep -qi 'xbox.*pad'; then
    logger -t ai-controller "Controller on USB but no input driver; reloading xpad"
    sudo modprobe xpad || true
    sleep 1
fi
for svc in antimicrox-autoload voice-bridge ptt-pynput controller-legend ai-slide-keyboard; do
    if ! systemctl --user is-active "${svc}.service" >/dev/null 2>&1; then
        logger -t ai-controller "${svc}.service not active; restarting"
        systemctl --user start "${svc}.service"
    fi
done
