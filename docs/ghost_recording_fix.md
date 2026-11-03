# Ghost Recording Fix — Push-to-Talk Debounce & Lock

## Problem
Rapid double-taps on the push-to-talk trigger (F13) caused ghost recordings: the second press would open the mic while the previous take's STT round-trip was still in flight, capturing room audio and sending it as a phantom utterance. The ptt log showed recordings starting spontaneously when the trigger wasn't pressed.

## Root Cause
`start_recording()` and `stop_and_send()` both acquired `lock` (a threading.Lock) independently, but there was no coordination between them. A fast F13 press sequence could:
1. `start_recording()` acquires lock → starts capture
2. Before `stop_and_send()` runs, a second F13 press fires
3. `start_recording()`'s lock check passes (non-blocking evaluation) → second capture starts
4. First `stop_and_send()` completes and fires STT with room audio from second capture
5. Result: ghost utterance sent to Hermes TUI

## Fix
Three changes to `scripts/ptt_pynput.py`:

### 1. Added `_processing_lock` (global threading lock)
- Acquired at the top of both `start_recording()` and `stop_and_send()`
- Released in `finally` blocks after the inner `lock` release
- Prevents either function from running while the other is in flight

### 2. Increased debounce from 200ms to 500ms
```
-_DEBOUNCE_MS = 200
+_DEBOUNCE_MS = 500
```
Gives the user's finger more time to fully leave the trigger between presses, reducing race conditions.

### 3. Reduced recorder wait timeouts from 2s to 1s
```
-rec_proc.wait(timeout=2)
+rec_proc.wait(timeout=1)
```
Faster teardown of the `parec` process means the capture source closes sooner, leaving the mic available for the next press cycle.

## Files Modified
- `scripts/ptt_pynput.py` — ghost recording prevention

## Testing
No new test infrastructure required. The fix is behavioral:
- Before: two F13 presses within ~100ms would cause ghost captures
- After: minimum 500ms debounce + serialized start/stop prevents races
- Existing log analysis confirms the ghost recordings stopped after this change

## Deployment
Reinstall or hot-reload the script (no process restart needed — lock is in-memory):
```bash
# Kill existing ptt_pynput if running
pkill -f ptt_pynput

# Restart (e.g., via start-all.sh or manual launch)
bash /home/elijah/ai-controller/scripts/start-voice-stack.sh
```
---

# F13 Still Leaking to Focused Windows (2026-10-06)

## Problem
Despite the 2026-09-08 X11 grab fix (`_run_x11_f13_grab()`), F13 kept bleeding
through to the focused window on some trigger presses.

## Root Cause
`fix-f13-keymap.sh` overlays **two** X11 keycodes onto the F13 keysym:
`xmodmap -e "keycode 191 = F13"` and `... 202 = F13"`. Antimicrox's uinput
backend can synthesize this trigger as either kernel `KEY_F13` (evdev 183 ->
X11 keycode 191) or `KEY_F24` (evdev 194 -> X11 keycode 202), depending on
which slot/profile path fires (trigger index="6" vs the profile's other
F13-producing path). `_run_x11_f13_grab()` called `keysym_to_keycode("F13")`,
which only ever resolves to ONE of the two keycodes (confirmed: it picked
191) — so the other kept leaking straight to the focused window exactly like
the original bug, just intermittently (whichever path the controller/profile
happened to route through).

## Fix
`_run_x11_f13_grab()` now grabs both keycode 191 and 202 directly (hardcoded
`_F13_KEYCODES`, same two values `fix-f13-keymap.sh` already overlays) instead
of resolving one keycode from the keysym. Grabbing by raw keycode also means
the grab no longer has to wait for the xmodmap overlay to land first.

## Files Modified
- `scripts/ptt_pynput.py` — `_run_x11_f13_grab()` / `_F13_KEYCODES`

## Verify
```
systemctl --user restart ptt-pynput.service
journalctl --user -u ptt-pynput.service -n 10
# should show: X11 F13 grab active (keycodes=[191, 202])
```
