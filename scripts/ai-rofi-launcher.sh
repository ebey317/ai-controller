#!/bin/bash
# AI Controller app launcher -- replaces Cinnamon's native Menu applet
# popup for Super_L/Super_R.
#
# Why: the native Menu applet is rendered by the shell's own Clutter
# compositor, not as a separate X11 client window. Confirmed live (2026-07-17):
# opening it via synthetic key, synthetic click, and a real click on its own
# search box all left getactivewindow/getwindowfocus reporting some other
# normal window -- real X input focus never moved to the menu by any method
# xdotool can see. That means dictation (ptt_pynput.py) and the on-screen
# slide keyboard, which both go through xdotool, can never reach it: there
# is no X11 window for focus_guard to even target, let alone verify.
#
# rofi is a normal top-level X11 window, so it works with the exact same
# focus_guard-verified typing pipeline as any other app dialog already
# fixed today.
#
# Resolve the install dir via $AI_CONTROLLER_DIR first, $HOME/ai-controller
# otherwise -- same fallback ai_controller_paths.py uses. Confirmed live
# (2026-07-17): a BASH_SOURCE[0]/dirname chain here resolved to "/" when
# antimicrox invoked this via its own "execute" action (rofi errored with
# theme path "//config/rofi-ai-controller.rasi"), even though the identical
# chain worked fine invoked directly from a shell. Root cause of that
# specific invocation context wasn't worth chasing further -- this sidesteps
# it entirely by never introspecting how the script was invoked.
AI_DIR="${AI_CONTROLLER_DIR:-$HOME/ai-controller}"
exec /usr/bin/rofi -show drun -theme "${AI_DIR}/config/rofi-ai-controller.rasi"
