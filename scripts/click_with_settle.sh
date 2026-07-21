#!/bin/bash
# click_with_settle.sh — A button (button index 1) left-click.
#
# Root cause (2026-07-21): a native AntiMicroX `mousebutton` slot fires a raw
# uinput click at the current pointer position with no accompanying mousemove
# event. Browser/Electron chat UIs only recompute what's under the pointer
# (hover state, I-beam cursor, click target) on an actual mousemove or scroll
# event -- if the cursor arrived via stick-driven movement that already
# stopped ticking before the click, the app's hover target can be stale, so
# the click lands on nothing. Confirmed live: scrolling the thread first
# (which forces a hover recheck) reliably "wakes" the correct target, and the
# cursor visibly flips to the I-beam right when it does -- a hover-state
# desync, not a button/debounce problem.
#
# Fix: force a tiny real mousemove immediately before clicking, so hover
# state is always freshly recomputed at the actual click coordinate.
export DISPLAY="${DISPLAY:-:0}"
xdotool mousemove_relative -- 1 0
xdotool mousemove_relative -- -1 0
sleep 0.03
xdotool click 1
