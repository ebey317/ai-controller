"""Focus-verified input injection for AI Controller.

xdotool fires blind at "whatever window is currently active." If focus drifts
between aiming at a target window and sending the keystroke -- another
window raising, a popover grabbing focus, a service restart, anything -- the
input lands wherever focus now is, silently. Confirmed live: a test
keystroke sequence landed in an unrelated terminal instead of the intended
dialog with zero error.

This module wraps xdotool with an explicit check-before/check-after so a
drift is caught and reported instead of typed into the wrong place.
"""
import logging
import os
import subprocess
import time

log = logging.getLogger(__name__)

_ENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}


class FocusLostError(Exception):
    """The intended target window could not be confirmed focused."""


def active_window():
    """Return the current X11 active window id, or None."""
    try:
        out = subprocess.check_output(
            ["xdotool", "getactivewindow"], env=_ENV,
            stderr=subprocess.DEVNULL, timeout=2)
        return out.decode().strip()
    except Exception:
        return None


def _focus_acceptable(cur, target_win):
    """True if `cur` (an active_window() reading) isn't a *wrong* window.

    Exact match is the normal case. `cur is None` is the other acceptable
    case: confirmed live that rofi -- and presumably other grab-based
    popups -- never becomes the EWMH active window at all (getactivewindow
    raises BadWindow(0x0) the entire time it's open, even mid-keystroke),
    yet blind xdotool type still landed correctly inside it, because an
    active X11 keyboard grab intercepts synthetic input the same as real
    hardware input regardless of what getactivewindow reports. There is no
    *wrong* identifiable window to mistype into in that state -- the thing
    this guard exists to catch is a DIFFERENT concrete window taking over,
    which this still refuses.
    """
    return cur == target_win or cur is None


def ensure_focus(target_win, retries=6, delay=0.04):
    """Re-activate `target_win` until confirmed active (or unreadable), or give up.

    Returns True once getactivewindow reports target_win or None (see
    _focus_acceptable); False if some other concrete window holds it for
    every attempt within `retries` (~240ms total at the defaults).
    """
    if not target_win:
        # No target was ever captured -- e.g. a grab-based popup was
        # already open (and thus already EWMH-invisible) at the moment the
        # keyboard/PTT snapshot ran. Only proceed if the CURRENT state is
        # that same "nothing identifiable" signature; if some concrete
        # window is active instead, there's no basis to believe blind
        # input goes anywhere the user intended.
        return active_window() is None
    for _ in range(retries):
        cur = active_window()
        if _focus_acceptable(cur, target_win):
            return True
        subprocess.run(["xdotool", "windowactivate", target_win],
                        env=_ENV, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=2)
        time.sleep(delay)
    return _focus_acceptable(active_window(), target_win)


def guarded_run(target_win, xdotool_args, retries=6, delay=0.04,
                 start_new_session=False):
    """Run an xdotool command against target_win, verified before and after.

    Raises FocusLostError instead of firing blind if focus can't be
    confirmed before the call, or drifted away during it. Callers decide the
    fallback (clipboard, skip, retry) -- this module only ever refuses to
    guess.
    """
    if not ensure_focus(target_win, retries=retries, delay=delay):
        raise FocusLostError(f"could not focus {target_win} before {xdotool_args}")
    subprocess.run(["xdotool", *xdotool_args], env=_ENV,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=start_new_session)
    if not _focus_acceptable(active_window(), target_win):
        raise FocusLostError(f"focus left {target_win} during {xdotool_args}")


def guarded_type(target_win, text, delay_ms=0, retries=6, delay=0.04,
                  start_new_session=False):
    guarded_run(target_win,
                ["type", "--clearmodifiers", f"--delay={delay_ms}", "--", text],
                retries=retries, delay=delay, start_new_session=start_new_session)


def _window_tree_count():
    """Count every X11 window, including override-redirect popovers/popups.

    getactivewindow can't see a GTK/Qt popover appear -- a popover usually
    grabs input without ever becoming the EWMH "active window," so the
    before/after check in guarded_run passes throughout while the real
    destination (the popover) is still mid-appear. Confirmed live: typing
    into a GTK file-chooser location bar right after Ctrl+L garbled two
    extra characters in, even with zero gap between the key and the type.
    Counting the raw window tree is toolkit-agnostic -- it doesn't care
    whether the new surface is a popover, a dialog, or anything else.
    """
    try:
        out = subprocess.check_output(
            ["xwininfo", "-root", "-tree"], env=_ENV,
            stderr=subprocess.DEVNULL, timeout=2)
        return len(out.decode(errors="ignore").splitlines())
    except Exception:
        return None


def wait_for_surface_settle(timeout=0.3, interval=0.02, stable_checks=2):
    """Block until the X11 window tree stops changing, or timeout.

    Call this after a keystroke that might summon a new popover/dialog
    (Ctrl+L, Escape, Return, Tab...) and before the next injected keystroke.
    Best effort: if xwininfo is missing, this degrades to one fixed wait.
    """
    deadline = time.monotonic() + timeout
    last = _window_tree_count()
    stable = 0
    while time.monotonic() < deadline:
        time.sleep(interval)
        cur = _window_tree_count()
        if cur is not None and cur == last:
            stable += 1
            if stable >= stable_checks:
                return
        else:
            stable = 0
            last = cur


def guarded_key(target_win, key_spec, clearmodifiers=True, retries=6, delay=0.04,
                 start_new_session=False, settle=True):
    args = ["key"] + (["--clearmodifiers"] if clearmodifiers else []) + [key_spec]
    guarded_run(target_win, args, retries=retries, delay=delay,
                start_new_session=start_new_session)
    if settle:
        # Named keys (accelerators, Escape, Return...) are the ones that
        # plausibly summon a new surface. Plain character typing doesn't get
        # this -- see guarded_type -- so dictation speed is unaffected.
        wait_for_surface_settle()
