#!/bin/bash
# settle_wiggle.sh — A button (button index 1) pre-click hover fix, drag-safe.
#
# Companion to the native antimicrox `mousebutton` slot on button 1: this
# script does the tiny mousemove that forces a hover recompute (see
# click_with_settle.sh for the full root-cause writeup) but does NOT send
# the click itself. The native mousebutton slot that runs right after this
# handles the actual down-on-press/up-on-release, so holding the button
# still produces a real sustained mousedown for dragging.
export DISPLAY="${DISPLAY:-:0}"
xdotool mousemove_relative -- 1 0
xdotool mousemove_relative -- -1 0
