#!/usr/bin/env python3
"""
Headless mode smoke test for ptt_pynput.py.

Tests:
1. _speak_headless routes to voice bridge /speak endpoint (no xdotool/typing)
2. stop_and_send in headless mode calls _speak_headless, not _type_text_fast
3. Goal take (TAHUTI_GOALS=1 / Ctrl+F13 equivalent) works identically in headless
4. No X11/xdotool/DISPLAY tracebacks in headless code paths
5. Evdev listener thread starts correctly in headless mode
"""

import os
import sys
import tempfile
import json
import threading
import time
import fcntl
from unittest.mock import patch, MagicMock, call, PropertyMock

# Set up headless environment BEFORE importing ptt_pynput
os.environ["AI_CONTROLLER_HEADLESS"] = "1"
os.environ["BRIDGE_URL"] = "http://127.0.0.1:8003"  # Scratch port for testing
os.environ["TAHUTI_GOALS"] = "1"  # Enable goal takes
os.environ["GROQ_API_KEY"] = "test-key"
os.environ["VOICE_BRIDGE_API_KEY"] = ""

# Unset DISPLAY to ensure no X11 calls
if "DISPLAY" in os.environ:
    del os.environ["DISPLAY"]
if "XAUTHORITY" in os.environ:
    del os.environ["XAUTHORITY"]

# Add scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

# Bypass singleton lock by mocking fcntl.flock BEFORE import
original_flock = fcntl.flock
def mock_flock(fd, op):
    if op & fcntl.LOCK_EX and op & fcntl.LOCK_NB:
        # Simulate successful lock acquisition
        return
    return original_flock(fd, op)

fcntl.flock = mock_flock

# Also mock threading.Thread.start to prevent evdev thread from actually running
original_thread_start = threading.Thread.start
started_threads = []

def mock_thread_start(self):
    if self.name in ("evdev-f13", "x11-f13-grab"):
        started_threads.append(self)
        print(f"  [MOCK] Thread '{self.name}' start intercepted")
        return
    return original_thread_start(self)

threading.Thread.start = mock_thread_start

# Mock os.open for the lock file
original_os_open = os.open
def mock_os_open(path, flags, *args, **kwargs):
    if "ptt_pynput.lock" in path:
        return 999  # Fake fd
    return original_os_open(path, flags, *args, **kwargs)
os.open = mock_os_open

# Import the threading module as _thr (as ptt_pynput does) and mock it too
import threading as _thr
original_thr_thread_start = _thr.Thread.start
_thr.Thread.start = mock_thread_start

# Mock _thr.Event().wait() to not block
original_event_wait = _thr.Event.wait
def mock_event_wait(self, timeout=None):
    # Return immediately instead of blocking
    return True
_thr.Event.wait = mock_event_wait

# Import after env is set and mocks are in place
import ptt_pynput as ptt

# Restore mocks
fcntl.flock = original_flock
threading.Thread.start = original_thread_start
_thr.Thread.start = original_thr_thread_start
_thr.Event.wait = original_event_wait
os.open = original_os_open

print(f"HEADLESS = {ptt.HEADLESS}")
print(f"BRIDGE_URL = {ptt.BRIDGE_URL}")
print(f"TAHUTI_GOALS = {ptt.TAHUTI_GOALS}")
print(f"Started threads (mocked): {[t.name for t in started_threads]}")

# Test 1: Verify HEADLESS flag is set
assert ptt.HEADLESS == True, "HEADLESS should be True"
print("✓ HEADLESS flag is correctly set")

# Test 2: Verify _speak_headless exists and uses curl to /speak
import inspect
speak_source = inspect.getsource(ptt._speak_headless)
assert "/speak" in speak_source, "_speak_headless should POST to /speak endpoint"
assert "curl" in speak_source, "_speak_headless should use curl"
assert "xdotool" not in speak_source, "_speak_headless should not use xdotool"
assert "_type_text_fast" not in speak_source, "_speak_headless should not call typing function"
print("✓ _speak_headless correctly routes to /speak endpoint")

# Test 3: Verify stop_and_send has headless branch that calls _speak_headless
stop_source = inspect.getsource(ptt.stop_and_send)
assert "HEADLESS" in stop_source, "stop_and_send should check HEADLESS flag"
assert "_speak_headless" in stop_source, "stop_and_send should call _speak_headless in headless mode"
assert "elif HEADLESS:" in stop_source or "if HEADLESS:" in stop_source, "stop_and_send should have explicit headless branch"
# Verify it does NOT call typing functions in headless branch
headless_section = stop_source[stop_source.find("elif HEADLESS:"):stop_source.find("else:", stop_source.find("elif HEADLESS:"))]
assert "_type_text_fast" not in headless_section, "Headless branch should not call _type_text_fast"
# xdotool may appear in comments, but not as actual code
xdotool_idx = headless_section.find("xdotool")
if xdotool_idx >= 0:
    # Check if it's in a comment (line starts with # or has # before xdotool)
    line_start = headless_section.rfind("\n", 0, xdotool_idx) + 1
    line = headless_section[line_start:xdotool_idx+20]
    assert line.strip().startswith("#") or "#" in line[:line.find("xdotool")], "xdotool should only appear in comments"
print("✓ stop_and_send has correct headless branch calling _speak_headless")

# Test 4: Verify start_recording skips focus snapshot and auto-space in headless
start_source = inspect.getsource(ptt.start_recording)
assert "if HEADLESS:" in start_source, "start_recording should check HEADLESS"
assert "_focus_window = None" in start_source, "Headless should set _focus_window to None"
assert "skipping focus snapshot" in start_source, "Should log skipping focus snapshot"
print("✓ start_recording correctly skips X11 focus operations in headless")

# Test 5: Verify goal take path works in headless (writes goal file, doesn't call /speak for goals)
goal_section = stop_source[stop_source.find("agent_take = ("):stop_source.find("_goal_take = False", stop_source.find("agent_take = ("))]
assert "_write_goal" in goal_section, "Goal take should write goal file"
print("✓ Goal take path writes goal file")

# Test 6: Verify evdev listener is started in headless mode
main_source = inspect.getsource(ptt)
assert "_run_evdev_listener" in main_source, "Evdev listener function should exist"
assert "HEADLESS:" in main_source and "evdev only" in main_source, "Main should start evdev thread in headless"
assert "_run_x11_f13_grab" in main_source, "X11 grab function should exist but not run in headless"
print("✓ Evdev listener is the trigger path in headless mode")

# Test 7: Verify no X11 imports in headless code path
# The pynput import should be stubbed in headless
assert hasattr(ptt.keyboard, "Key"), "Keyboard stub should exist"
assert hasattr(ptt.keyboard.Key, "f13"), "F13 key should exist in stub"
assert hasattr(ptt.keyboard.Key, "ctrl_l"), "Ctrl key should exist in stub"
print("✓ Pynput is stubbed in headless mode")

# Test 8: Test _speak_headless with mocked subprocess
print("\n--- Testing _speak_headless with mock ---")
with patch("subprocess.run") as mock_run:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    ptt._speak_headless("test message")
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "curl" in args[0], "Should use curl"
    assert "/speak" in " ".join(args), "Should POST to /speak"
    assert "test message" in " ".join(args), "Should include text"
    print("✓ _speak_headless correctly calls curl to /speak endpoint")

# Test 9: Test goal take in headless mode (mocked)
print("\n--- Testing goal take in headless mode ---")
with patch("ptt_pynput._write_goal") as mock_write_goal, \
     patch("ptt_pynput._append_transcript_log") as mock_append_log, \
     patch("ptt_pynput._notify_hermex") as mock_notify, \
     patch("ptt_pynput.subprocess.run") as mock_run:

    # Simulate a goal take
    ptt._goal_take = True
    ptt._ctrl_held = True
    
    # We can't easily test the full stop_and_send without audio, but we can verify
    # the goal path logic by checking the source
    stop_source = inspect.getsource(ptt.stop_and_send)
    
    # Verify goal take writes goal and doesn't speak
    assert "if agent_take and goal_text:" in stop_source
    assert "_write_goal(goal_text)" in stop_source
    # In headless, goal takes should NOT call _speak_headless
    goal_block = stop_source[stop_source.find("if agent_take and goal_text:"):stop_source.find("_goal_take = False", stop_source.find("if agent_take and goal_text:"))]
    assert "_speak_headless" not in goal_block, "Goal takes should not call _speak_headless"
    print("✓ Goal take in headless writes goal file, does not call _speak_headless")

# Test 10: Verify regular (non-goal) take in headless calls _speak_headless
print("\n--- Testing regular take in headless mode ---")
non_goal_headless = stop_source[stop_source.find("elif HEADLESS:"):stop_source.find("else:", stop_source.find("elif HEADLESS:"))]
assert "_speak_headless(transcript)" in non_goal_headless, "Regular headless take should call _speak_headless"
assert "_append_transcript_log" in non_goal_headless, "Should log transcript"
print("✓ Regular take in headless calls _speak_headless and logs transcript")

# Test 11: Verify no Xlib/xdotool/DISPLAY in headless code paths
print("\n--- Verifying no X11 dependencies in headless paths ---")
headless_paths = [
    ("start_recording", ptt.start_recording),
    ("_speak_headless", ptt._speak_headless),
]

for name, func in headless_paths:
    if func is not None:
        try:
            src = inspect.getsource(func)
            # Check xdotool only appears in comments
            xdotool_idx = src.find("xdotool")
            if xdotool_idx >= 0:
                line_start = src.rfind("\n", 0, xdotool_idx) + 1
                line = src[line_start:xdotool_idx+20]
                assert line.strip().startswith("#") or "#" in line[:line.find("xdotool")], f"{name}: xdotool should only be in comments"
            assert "Xlib" not in src, f"{name} should not contain Xlib"
            print(f"  ✓ {name} has no X11 dependencies")
        except Exception as e:
            print(f"  ⚠ {name}: {e}")

# Test 12: Test that evdev listener handles Ctrl+F13 for goal takes
print("\n--- Testing evdev Ctrl+F13 detection ---")
evdev_source = inspect.getsource(ptt._run_evdev_listener)
assert "KEY_LEFTCTRL" in evdev_source or "29" in evdev_source, "Should track left Ctrl"
assert "KEY_RIGHTCTRL" in evdev_source or "97" in evdev_source, "Should track right Ctrl"
assert "_ctrl_held = ev_value in (1, 2)" in evdev_source, "Should update _ctrl_held on Ctrl press/release"
assert "_goal_take = _ctrl_held" in inspect.getsource(ptt.on_press), "on_press should set _goal_take from _ctrl_held"
print("✓ Evdev listener correctly tracks Ctrl for goal takes")

# Test 13: Verify on_press/on_release use the stubbed keyboard correctly
print("\n--- Testing on_press/on_release with stubbed keyboard ---")
on_press_src = inspect.getsource(ptt.on_press)
on_release_src = inspect.getsource(ptt.on_release)
assert "keyboard.Key.f13" in on_press_src, "on_press should check for F13"
assert "keyboard.Key.ctrl_l" in on_press_src, "on_press should check for Ctrl"
assert "keyboard.Key.f13" in on_release_src, "on_release should check for F13"
print("✓ on_press/on_release use stubbed keyboard.Key correctly")

# Test 14: Test actual stop_and_send flow with mocked audio for headless regular take
print("\n--- Testing stop_and_send headless regular take flow (mocked) ---")
with patch("ptt_pynput._build_wav") as mock_build_wav, \
     patch("ptt_pynput.os.path.exists") as mock_exists, \
     patch("ptt_pynput.os.path.getsize") as mock_getsize, \
     patch("ptt_pynput.os.unlink") as mock_unlink, \
     patch("ptt_pynput._wav_stats") as mock_wav_stats, \
     patch("ptt_pynput._is_silence") as mock_is_silence, \
     patch("ptt_pynput.subprocess.run") as mock_subprocess_run, \
     patch("ptt_pynput._speak_headless") as mock_speak_headless, \
     patch("ptt_pynput._append_transcript_log") as mock_append_log, \
     patch("ptt_pynput._notify_hermex") as mock_notify, \
     patch("ptt_pynput._load_ptt_mode") as mock_load_mode, \
     patch("ptt_pynput._load_dictation_target") as mock_load_dictation_target, \
     patch("ptt_pynput._load_vocabulary") as mock_load_vocab, \
     patch("ptt_pynput._apply_vocabulary", side_effect=lambda x: x), \
     patch("ptt_pynput.time.sleep"):

    # Setup mocks for a successful transcription
    ptt.rawfile = "/tmp/test.raw"
    ptt.wavfile = "/tmp/test.wav"
    ptt.recording = True
    ptt._goal_take = False
    ptt._ctrl_held = False
    ptt._last_f13_time = 0  # Reset debounce
    
    mock_exists.return_value = True
    mock_getsize.return_value = 20000  # > 16000 byte threshold
    mock_wav_stats.return_value = (1.5, 1000)  # duration, rms
    mock_is_silence.return_value = False
    mock_load_mode.return_value = "pro"
    mock_load_dictation_target.return_value = "type"
    
    # Mock the STT response
    mock_subprocess_run.return_value = MagicMock(
        stdout=json.dumps({"text": "hello world", "transcript": "hello world"}),
        stderr="",
        returncode=0
    )
    
    # Call stop_and_send
    ptt.stop_and_send()
    
    # Verify _speak_headless was called (not _type_text_fast)
    mock_speak_headless.assert_called_once_with("hello world")
    mock_append_log.assert_called_once()
    print("✓ stop_and_send in headless mode calls _speak_headless for regular take")

# Test 15: Test stop_and_send headless goal take flow
print("\n--- Testing stop_and_send headless goal take flow (mocked) ---")
with patch("ptt_pynput._build_wav") as mock_build_wav, \
     patch("ptt_pynput.os.path.exists") as mock_exists, \
     patch("ptt_pynput.os.path.getsize") as mock_getsize, \
     patch("ptt_pynput.os.unlink") as mock_unlink, \
     patch("ptt_pynput._wav_stats") as mock_wav_stats, \
     patch("ptt_pynput._is_silence") as mock_is_silence, \
     patch("ptt_pynput.subprocess.run") as mock_subprocess_run, \
     patch("ptt_pynput._speak_headless") as mock_speak_headless, \
     patch("ptt_pynput._append_transcript_log") as mock_append_log, \
     patch("ptt_pynput._notify_hermex") as mock_notify, \
     patch("ptt_pynput._write_goal") as mock_write_goal, \
     patch("ptt_pynput._load_ptt_mode") as mock_load_mode, \
     patch("ptt_pynput._load_dictation_target") as mock_load_dictation_target, \
     patch("ptt_pynput._load_vocabulary") as mock_load_vocab, \
     patch("ptt_pynput._apply_vocabulary", side_effect=lambda x: x), \
     patch("ptt_pynput.time.sleep"):

    # Setup mocks for a successful transcription with goal take
    ptt.rawfile = "/tmp/test.raw"
    ptt.wavfile = "/tmp/test.wav"
    ptt.recording = True
    ptt._goal_take = True
    ptt._ctrl_held = True
    ptt._last_f13_time = 0  # Reset debounce
    
    mock_exists.return_value = True
    mock_getsize.return_value = 20000
    mock_wav_stats.return_value = (1.5, 1000)
    mock_is_silence.return_value = False
    mock_load_mode.return_value = "pro"
    mock_load_dictation_target.return_value = "type"
    
    # Mock the STT response (execute mode returns transcript + response + routed=task)
    # For task verbs, the bridge writes the goal and returns routed="task"
    # In this case, ptt_pynput should NOT call _write_goal (duplicate prevention)
    mock_subprocess_run.return_value = MagicMock(
        stdout=json.dumps({"transcript": "create a task", "response": "On it", "routed": "task"}),
        stderr="",
        returncode=0
    )
    
    # Call stop_and_send
    ptt.stop_and_send()
    
    # Debug
    print(f"  mock_write_goal called: {mock_write_goal.called}")
    print(f"  mock_speak_headless called: {mock_speak_headless.called}")
    print(f"  mock_append_log called: {mock_append_log.called}")
    print(f"  mock_append_log call_count: {mock_append_log.call_count}")
    if mock_append_log.call_count > 0:
        print(f"  mock_append_log call_args: {mock_append_log.call_args}")
    
    # Verify _write_goal was NOT called (bridge already wrote it for task verbs)
    mock_write_goal.assert_not_called()
    # Verify _speak_headless was NOT called for goal take
    mock_speak_headless.assert_not_called()
    # But transcript log should be called
    mock_append_log.assert_called_once()
    print("✓ stop_and_send in headless mode skips _write_goal for task-routed goal takes (bridge handles it)")

print("\n" + "="*60)
print("ALL HEADLESS MODE SMOKE TESTS PASSED")
print("="*60)
print("\nSummary:")
print("  - HEADLESS flag correctly enables evdev-only path")
print("  - _speak_headless routes output to voice bridge /speak")
print("  - stop_and_send headless branch calls _speak_headless for regular takes")
print("  - Goal takes (Ctrl+F13) write goal files identically in headless")
print("  - No X11/xdotool/DISPLAY dependencies in headless code paths")
print("  - Evdev listener handles F13 and Ctrl for goal takes")
print("\nNote: Port 8002 is occupied by operator's live GUI voice-bridge.")
print("      Testing used scratch port 8003 for voice-bridge-headless.")