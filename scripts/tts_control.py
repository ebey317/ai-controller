#!/usr/bin/env python3
"""Shared TTS control for AI Controller: one interrupt path, one driver choice.

WHY THIS MODULE EXISTS (2026-09-30)
-----------------------------------
Barge-in used to work by guessing. ``_mute_tts()`` in ``ptt_pynput.py`` walked
``/proc`` looking for processes *named* mpv/ffplay/aplay/paplay, then fired a
list of ``pkill -f`` regexes at command lines it hoped contained a temp
filename.

That worked only while every TTS player was a separate process launched with a
recognisable argv. Hermes stopped being one. ``tools/tts_tool_speaker.py``
now plays speech through a **sounddevice OutputStream opened inside the Hermes
python process** -- there is no ffplay, no mpv, no child process to kill. Every
pattern in the old kill list matches nothing, so pressing the trigger silently
did nothing. Four earlier fix attempts (2026-07-29) failed for the same reason:
they all patched a list aimed at a process model Hermes no longer used.

The fix is to stop hunting processes. PulseAudio already tracks every playback
stream in the system, no matter which program opened it, and can kill one by
ID. So:

  * **Interrupt** = kill the TTS *sink inputs*, identified by the PID that owns
    them. Works for sounddevice-in-process, mpv, ffplay, aplay, and whatever
    comes next, with no per-player bookkeeping.
  * **Driver choice** = one named value the user controls, resolved to a real
    sink at play time.
  * **Card reset** = only on a real kernel wedge, never merely because speech
    was interrupted.

Nothing here prints or reads API keys; it touches only PulseAudio state, the
kernel log, and the audio keys in config.env.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

# The kernel message the xone-gip driver emits when its audio output buffers
# wedge. This is the ONLY justification for cycling a card profile -- see
# _wedge_failures() and the note in ptt_pynput._fire_deferred_audio_reset.
_WEDGE_KERNEL_MSG = "gip_send_audio_samples: get buffer failed"

# Process markers that identify a TTS owner. Matched against /proc/<pid>/cmdline
# (exact substring, not a regex) so an unrelated process that merely mentions
# one of these words cannot be mistaken for a player.
_TTS_OWNER_MARKERS = (
    "hermes",            # Hermes agent (sounddevice in-process TTS)
    "voice_bridge.py",   # controller voice bridge
    "hermes_tts_play",   # the mpv wrapper
)

# Playback binaries we can still catch as processes: some paths (spd-say, or a
# client talking straight to ALSA) never become a PulseAudio sink input.
_PLAYER_BINARIES = ("mpv", "ffplay", "aplay", "paplay")

# The tag every controller-owned player carries. Checked against the cmdline of
# processes that are already known to be players, so this can never match a
# shell or editor (see the 2026-07-30 incident in ptt_pynput history).
_BARGE_TAG = b"AI_TTS_BARGE"


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return out.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------------------
# Wedge detection -- the only thing that may cycle a card profile
# ---------------------------------------------------------------------------

def _wedge_failures(window_s: int = 10) -> int:
    """Count xone-gip buffer failures in the kernel log for the last window."""
    try:
        proc = subprocess.run(
            ["journalctl", "-k", f"--since={window_s} seconds ago"],
            capture_output=True, text=True, timeout=5,
        )
        return (proc.stdout or "").count(_WEDGE_KERNEL_MSG)
    except (OSError, subprocess.SubprocessError):
        return 0


def controller_audio_wedged(window_s: int = 10) -> bool:
    """True only when the kernel is actually reporting driver buffer failures.

    Replaces the old condition, which was "a TTS process was killed" -- an
    event that happens to be unrelated to a mic wedge. Cycling the card profile
    rips the controller mic out of PulseAudio for 0.7s and replugs it, so
    firing that on every barge-in (measured: 728 times) risked emptying the
    user's dictation take for no reason.
    """
    return _wedge_failures(window_s) > 0


# ---------------------------------------------------------------------------
# Sink-input interrupt -- the new barge-in
# ---------------------------------------------------------------------------

class SinkInput(NamedTuple):
    index: int
    pid: Optional[int]
    name: str
    sink: str


def _all_pids() -> list[int]:
    out = []
    try:
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                out.append(int(entry))
    except OSError:
        pass
    return out


def _cmdline(pid: int) -> bytes:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def _comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", "r") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def tts_processes() -> list[int]:
    """PIDs that may own a TTS stream.

    Two families:
      * anything whose cmdline mentions a TTS owner marker (Hermes, the
        controller voice bridge, the mpv wrapper) -- this is what catches
        sounddevice playing inside the Hermes interpreter;
      * processes that ARE a known playback binary AND carry the barge tag,
        which is how the controller's own mpv is found without a shell or
        editor being mistaken for a player.
    """
    found: list[int] = []
    for pid in _all_pids():
        cmd = _cmdline(pid)
        if not cmd:
            continue
        if any(marker.encode() in cmd for marker in _TTS_OWNER_MARKERS):
            found.append(pid)
            continue
        if _comm(pid) in _PLAYER_BINARIES and _BARGE_TAG in cmd:
            found.append(pid)
    return found


def list_sink_inputs() -> list[SinkInput]:
    """Every PulseAudio playback stream, with the PID that opened it."""
    raw = _run(["pactl", "list", "sink-inputs"], timeout=5.0)
    if not raw.strip():
        return []

    streams: list[SinkInput] = []
    current_index: Optional[int] = None
    pid: Optional[int] = None
    name = ""
    sink = ""
    in_props = False

    def flush() -> None:
        nonlocal current_index
        if current_index is not None:
            streams.append(SinkInput(current_index, pid, name, sink))

    for line in raw.splitlines():
        header = re.match(r"^\s*Sink Input #(\d+)", line)
        if header:
            flush()
            current_index = int(header.group(1))
            pid, name, sink, in_props = None, "", "", False
            continue
        if current_index is None:
            continue
        if "Properties:" in line:
            in_props = True
            continue
        if not in_props:
            # `Sink: <n>` is a top-level field emitted BEFORE the Properties:
            # block. Skipping everything pre-Properties left SinkInput.sink
            # permanently empty, so `tts_control.py streams` reported a blank
            # sink and nobody could see where audio was actually routed.
            stripped = line.strip()
            if stripped.startswith("Sink:"):
                sink = stripped.split(":", 1)[1].strip()
            continue
        stripped = line.strip()
        if stripped.startswith("media.name = "):
            name = stripped.split("=", 1)[1].strip().strip('"')
        elif stripped.startswith("application.name = "):
            app = stripped.split("=", 1)[1].strip().strip('"')
            # Prefer the application name when no explicit media name is set.
            if not name:
                name = app
        elif stripped.startswith("application.process.id = "):
            try:
                pid = int(stripped.split("=", 1)[1].strip().strip('"'))
            except ValueError:
                pid = None
        elif stripped.startswith("application.process.binary = "):
            if not name:
                name = stripped.split("=", 1)[1].strip().strip('"')
    flush()
    return streams


def tts_sink_inputs() -> list[SinkInput]:
    """Playback streams that belong to a TTS owner.

    Matching has to be two-pronged, because the two players advertise
    themselves differently and neither advertises everything:

      * **Hermes (sounddevice)** sets ``application.process.id`` but no useful
        ``media.name`` -- it has to be found by owner PID.
      * **mpv** sets ``media.name = "AI_TTS_BARGE - mpv"`` but leaves
        ``application.process.id`` unset -- it has to be found by tag.

    Verified against a live stream; matching on either signal alone misses one
    of the two, which is the same class of bug as the old process-name list.
    """
    owners = set(tts_processes())
    found: list[SinkInput] = []
    for stream in list_sink_inputs():
        if stream.pid is not None and stream.pid in owners:
            found.append(stream)
            continue
        # The barge tag is stamped by our own players, so it is an exact
        # signal rather than a guess about a filename.
        if "AI_TTS_BARGE" in (stream.name or ""):
            found.append(stream)
    return found


def silence_stream(index: int) -> str:
    """Silence one playback stream. Returns "muted" or "failed".

    Mute, then automatically unmute a moment later.

    Why not more permanent approaches -- both were tried and both break the
    product, in opposite ways:

      * **Move the stream to a ``module-null-sink``** (2026-09-30, reverted).
        Hermes keeps ONE long-lived sounddevice OutputStream for the whole
        session. Parking that stream on a null sink silenced it permanently:
        Hermes kept feeding the same stream, so every later reply went to the
        null sink too and the agent stayed mute. The existence check for the
        sink also consulted ``available_sinks()``, which filters null sinks
        out, so it could never match and a new module loaded on every
        interrupt -- 31 had accumulated before it was noticed.

      * **Mute and leave it muted** (2026-09-30, reverted). Same root problem:
        the stream survives the cut, so the NEXT reply is muted too.

    A barge-in means the agent has stopped talking, so anything it emits after
    the cut is unwanted by definition. Muting stops the sound immediately, and
    the timed unmute restores the stream for the next utterance -- which
    arrives later, on its own schedule. No phantom sink, nothing stranded,
    nothing left muted.
    """
    if not _mute_sink_input(index):
        return "failed"

    def _restore() -> None:
        _unmute_sink_input(index)

    timer = threading.Timer(_MUTE_HOLD_S, _restore)
    timer.daemon = True
    timer.start()
    return "muted"


def _mute_sink_input(index: int) -> bool:
    proc = subprocess.run(
        ["pactl", "set-sink-input-mute", str(index), "1"],
        capture_output=True, text=True, timeout=5,
    )
    return proc.returncode == 0


def _unmute_sink_input(index: int) -> bool:
    proc = subprocess.run(
        ["pactl", "set-sink-input-mute", str(index), "0"],
        capture_output=True, text=True, timeout=5,
    )
    return proc.returncode == 0


# How long a barge-in holds the mute. Long enough that any audio the agent was
# still emitting when the cut landed stays suppressed; short enough that the
# next reply is audible.
_MUTE_HOLD_S = 2.0


def mute_tts() -> tuple[int, list[int]]:
    """Stop TTS now. Returns ``(streams_silenced, players_signalled)``.

    Primary mechanism: silence the TTS playback streams in PulseAudio. This is
    what finally works on Hermes, which streams TTS from inside its own python
    process via sounddevice -- there is no child player to kill, which is why
    the old process-name/pkill list matched nothing and four earlier fix
    attempts failed against it.

    Secondary: SIGTERM a *player binary* carrying the barge tag, for a client
    writing straight to ALSA that never becomes a sink input.

    Deliberately NOT done: signalling the owning interpreter. "Stop the audio"
    must never become "SIGTERM every process whose cmdline mentions hermes" --
    that matched 14 processes here, including the live agent. Owner PIDs are
    used to *identify* streams, never to kill processes.
    """
    silenced = 0
    for stream in tts_sink_inputs():
        if silence_stream(stream.index) != "failed":
            silenced += 1

    signalled: list[int] = []
    for pid in _tagged_player_pids():
        if pid in (os.getpid(), os.getppid()):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            signalled.append(pid)
        except (OSError, ProcessLookupError):
            continue

    return silenced, signalled


def _tagged_player_pids() -> list[int]:
    """Playback BINARIES carrying the barge tag. Never interpreters.

    The binary name is checked first so a shell, an editor, or an agent whose
    command line merely mentions the tag can never be caught -- that mistake
    killed live terminals twice on 2026-07-30.

    2026-09-30: Hermes' CLI session plays each reply through its own per-reply
    ffplay against a scratch-tts file, and that player carries --force-media-
    title only in the wrapper path, so these arrive UNTAGGED. The mute pass
    (mute_tts) silences them for _MUTE_HOLD_S and then unmutes; the player is
    still alive mid-file, so speech resumes exactly where it was cut -- the
    user heard a "pause", not a stop. Extend the kill pass with one guarded
    signature: a known playback binary whose cmdline reads a Hermes scratch
    TTS file (<home>/.hermes/cache/scratch/tmp*.mp3). The scratch-tts path
    signature is unique to Hermes' per-reply TTS (scratch/tmp is pruned by
    Hermes itself and used for nothing else), and the binary-name check stays
    first so an IPTV mpv, a shell, or an editor can still never match.
    """
    scratch_marker = os.fsencode(str(Path.home() / ".hermes/cache/scratch/tmp"))
    found: list[int] = []
    for pid in _all_pids():
        if _comm(pid) not in _PLAYER_BINARIES:
            continue
        cmd = _cmdline(pid)
        if not cmd:
            continue
        if _BARGE_TAG in cmd or scratch_marker in cmd:
            found.append(pid)
    return found


# ---------------------------------------------------------------------------
# Driver selection -- the user picks; nothing switches underneath them
# ---------------------------------------------------------------------------

# Friendly names the user picks. Each maps to a PATTERN, not a literal sink
# name, because PulseAudio bakes the device serial into the name
# (`alsa_output.usb-Microsoft_Controller_30393731...stereo-fallback`) and that
# serial differs per controller and per machine. Matching a pattern is what
# makes "headphones" keep working when the headset is re-paired.
#
#   hdmi / hdmi-display / display  -> the built-in digital output (office desk)
#   headphones / headset / xbox    -> the controller's own output (office)
#   default                        -> whatever PulseAudio is already using
#
# Matching is case-insensitive and ignores spaces/underscores so voice input
# ("headphones", "Head Phones") all land on the same driver.
DRIVER_PATTERNS: dict[str, str] = {
    "hdmi": r"^alsa_output\.pci-.*hdmi-stereo$",
    # 2026-09-30: the built-in 3.5 mm jack. Added after a session where every
    # other output read "Subdevices: 0/1" -- nothing plugged in anywhere -- and
    # the only port with a live jack was this one. It needs the card profile
    # switched off output:hdmi-stereo before PulseAudio will expose it as a
    # sink at all, which ensure_device_profile() below handles.
    "analog": r"^alsa_output\.pci-.*analog-stereo$",
    "headphones": r"^alsa_output\.usb-Microsoft_Controller.*stereo-fallback$",
    "default": r".*",  # first sink wins
}

# Extra spoken/typed spellings mapped onto canonical driver names.
DRIVER_SYNONYMS: dict[str, str] = {
    "display": "hdmi",
    "hdmiaudio": "hdmi",
    "monitor": "hdmi",
    "tv": "hdmi",
    # 3.5 mm jack
    "jack": "analog",
    "headphonejack": "analog",
    "wired": "analog",
    "speakers": "analog",
    "3.5mm": "analog",
    "aux": "analog",
    "headphone": "headphones",
    "headset": "headphones",
    "xbox": "headphones",
    "controller": "headphones",
    "ears": "headphones",
    "auto": "default",
    "system": "default",
}

DEFAULT_DRIVER = "hdmi"


def config_path() -> Path:
    override = os.environ.get("AI_CONTROLLER_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "ai-controller" / "config.env"


def _read_config_value(key: str) -> Optional[str]:
    path = config_path()
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _write_config_value(key: str, value: str) -> None:
    path = config_path()
    lines: list[str] = []
    replaced = False
    try:
        lines = path.read_text().splitlines()
    except OSError:
        pass
    for index, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Config holds the Groq key, so keep it owner-only. Write via a temp file
    # so a crash mid-write cannot truncate the credentials.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _normalise(name: str) -> str:
    """Fold a spoken or typed driver name onto its canonical key."""
    key = re.sub(r"[\s_-]+", "", (name or "").strip().lower())
    key = DRIVER_SYNONYMS.get(key, key)
    return key if key in DRIVER_PATTERNS else key


def current_driver() -> str:
    """The driver's canonical name, falling back to a sensible default.

    Before this existed the choice was AUDIO_OUTPUT holding a raw PulseAudio
    sink name, which embedded the device serial. If that legacy value still
    matches a driver we recognise, report that driver, so the first run after
    upgrading does not appear to flip the user's output.
    """
    stored = (_read_config_value("AI_TTS_DRIVER") or "").strip()
    if stored and _normalise(stored) in DRIVER_PATTERNS:
        return _normalise(stored)

    legacy = (_read_config_value("AUDIO_OUTPUT") or "").strip()
    if legacy:
        for name, pattern in DRIVER_PATTERNS.items():
            if name == "default":
                continue
            if re.match(pattern, legacy):
                return name
    return DEFAULT_DRIVER


def set_driver(name: str) -> str:
    """Persist a driver choice. Raises ValueError on an unknown name.

    Only the friendly name is written. The concrete sink is resolved at play
    time, so re-pairing the controller or changing seats does not require
    editing the config again.
    """
    key = _normalise(name)
    if key not in DRIVER_PATTERNS:
        raise ValueError(
            f"unknown driver {name!r}; choose one of: "
            + ", ".join(sorted(DRIVER_PATTERNS))
        )
    _write_config_value("AI_TTS_DRIVER", key)
    return key


def available_sinks() -> dict[str, str]:
    """Every real sink -> its friendly driver name ("" when unrecognised).

    Internal sinks are filtered out so the driver list shows only things a
    person would call a speaker.
    """
    present: dict[str, str] = {}
    for line in _run(["pactl", "list", "sinks", "short"]).splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        sink = parts[1]
        if "null" in sink:
            continue
        label = ""
        for friendly, pattern in DRIVER_PATTERNS.items():
            if friendly != "default" and re.match(pattern, sink):
                label = friendly
                break
        present[sink] = label
    return present


def resolve_sink(name: Optional[str] = None) -> str:
    """Map a driver name to a currently-present sink.

    Falls back to the PulseAudio default when the chosen device is absent --
    unplugging the controller must not silence TTS, it must redirect it to
    whatever is left. That is also what makes the office/desk switch safe: if
    the headset is not plugged in, asking for headphones still makes sound.
    """
    name = _normalise(name) if name else current_driver()
    pattern = DRIVER_PATTERNS.get(name, "")
    fallback = _run(["pactl", "get-default-sink"]).strip()
    if not pattern:
        return fallback
    for sink in available_sinks():
        if re.match(pattern, sink):
            return sink
    return fallback


def _controller_card() -> str:
    """The controller's audio card name, or "" if it is not plugged in."""
    for line in _run(["pactl", "list", "cards", "short"]).splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.search(r"Microsoft_Controller|Xbox", parts[1], re.I):
            return parts[1]
    return ""


def _block_for(raw: str, header: str, end_re: str) -> list[str]:
    """Lines belonging to one `pactl` object, delimited line-wise.

    Why this exists: three different call sites were slicing `pactl` output by
    fixed offsets or by looking for a heading that `pactl` never emits. Both
    fail the same way -- the block silently runs past the object it was meant
    to capture and the wrong value is read as if it were the right one.

    Stop on the *object header* of the next record (`Card #N`, `Sink #N`),
    which pactl always emits, rather than on an inner heading.
    """
    lines = raw.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i
            break
    if start is None:
        return []
    block: list[str] = []
    for line in lines[start + 1:]:
        if re.match(end_re, line):
            break
        block.append(line)
    return block


def _card_block(raw: str, card: str) -> list[str]:
    """The lines of one card's block, or [] if that card is not present."""
    return _block_for(raw, f"Name: {card}", r"^Card #\d+")


def _sink_block(raw: str, sink: str) -> list[str]:
    """The lines of one sink's block, or [] if that sink is not present."""
    return _block_for(raw, f"Name: {sink}", r"^Sink #\d+")


def _active_profile(block: list[str]) -> str:
    """The card's active profile, or "" when unreadable.

    Searching the whole `pactl list cards` output for a profile string is a
    false positive machine: every *available* profile is listed too, so the
    inactive ones match and the check reports success while a different
    profile is actually in use.
    """
    for line in block:
        match = re.match(r"^\s*Active Profile:\s*(.+?)\s*$", line)
        if match:
            return match.group(1)
    return ""


def ensure_controller_output(enable: bool) -> bool:
    """Turn the controller's *output* profile on or off.

    Why this exists: the controller card presents the microphone in
    ``input:mono-fallback`` but the headphones in
    ``output:stereo-fallback+input:mono-fallback``. With only the input
    profile active there is no headset sink at all, so "headphones" has
    nothing to resolve to.

    That output profile is also the one the xone-gip driver is documented to
    wedge (``gip_send_audio_samples: get buffer failed: -28``), which is why
    it was left off. Rather than picking a side permanently, it now tracks
    the user's choice: pick headphones and the output comes up; pick HDMI and
    it goes back down. The microphone is untouched either way.
    """
    card = _controller_card()
    if not card:
        return False
    with_output = "output:stereo-fallback+input:mono-fallback"
    target = with_output if enable else "input:mono-fallback"
    subprocess.run(
        ["pactl", "set-card-profile", card, target],
        capture_output=True, text=True, timeout=5,
    )
    # Exact check against THIS card's Active Profile. `target in pactl output`
    # also matches the profile in the inactive `Profiles:` list, so it reported
    # success while a different profile was live.
    return _active_profile(_card_block(_run(["pactl", "list", "cards"]), card)) == target


def apply_driver(name: Optional[str] = None) -> str:
    """Report the sink the requested driver resolves to.

    Deliberately does NOT touch the card profile.

    2026-09-30: this used to call ensure_controller_output() on every call, and
    this function is on the speech path. `pactl set-card-profile` tears the
    device down and brings it back, so every reply would have re-plugged the
    controller's audio mid-playback -- a guaranteed click, and a live audio
    disruption on the one machine doing the playing.

    Profile changes belong to an explicit driver switch, which is a deliberate
    user action, not to routine speech. See set_driver_with_profile().
    """
    name = _normalise(name) if name else current_driver()
    return resolve_sink(name)


# Card profile required before a driver can exist as a sink at all.
# The Intel HDA card exposes one profile at a time: HDMI and the 3.5 mm jack
# are mutually exclusive, so picking one means telling the card which to use.
_CARD_PROFILE_FOR_DRIVER = {
    "analog": ("pci-", "output:analog-stereo"),
    "hdmi": ("pci-", "output:hdmi-stereo"),
    "headphones": ("usb-Microsoft_Controller", "output:stereo-fallback+input:mono-fallback"),
}


def ensure_device_profile(driver: str) -> None:
    """Put the hardware into the profile the chosen driver needs."""
    entry = _CARD_PROFILE_FOR_DRIVER.get(driver)
    if not entry:
        return
    match, profile = entry
    card = _controller_card() if match.startswith("usb-") else _hda_card()
    if not card:
        return
    try:
        current = _run(["pactl", "list", "cards"])
    except Exception:
        return
    block = _card_block(current, card)
    if not block:
        return
    # Line-oriented and exact. The old version took current[index:index+2000],
    # which both truncated a card whose Active Profile sat past the window and
    # could swallow the following card entirely.
    if _active_profile(block) == profile:
        return
    subprocess.run(
        ["pactl", "set-card-profile", card, profile],
        capture_output=True, text=True, timeout=5,
    )


def _hda_card() -> str:
    """The built-in Intel HDA card, which owns HDMI and the analog jack."""
    for line in _run(["pactl", "list", "cards", "short"]).splitlines():
        parts = line.split()
        if len(parts) >= 2 and "alsa_card.pci-" in parts[1]:
            return parts[1]
    return ""


def set_driver_with_profile(name: str) -> str:
    """Switch driver AND bring its device into line. For explicit switches only.

    headphones -> controller output profile up; anything else -> input-only.
    Called from the CLI and the spoken command, never from the play path.
    """
    driver = set_driver(name)
    ensure_device_profile(driver)
    ensure_controller_output(driver == "headphones")
    sink = resolve_sink(driver)
    # Also make it the system default, because Hermes (and anything else that
    # spawns a bare ffplay) routes by the PulseAudio default, not by anything
    # this module knows about. Without this the driver choice only affects
    # AI Controller's own playback and agents stay silent on the old device.
    if sink:
        subprocess.run(
            ["pactl", "set-default-sink", sink],
            capture_output=True, text=True, timeout=5,
        )
        subprocess.run(
            ["pactl", "set-sink-mute", sink, "0"],
            capture_output=True, text=True, timeout=5,
        )
    return sink


def sink_spec(sink: Optional[str] = None) -> tuple[int, int]:
    """``(sample_rate_hz, channels)`` for a sink. (48000, 2) if unreadable.

    The controller headset and the HDMI output both report 48000, but that is
    a property of this pair of devices rather than a law -- and asking the
    wrong rate forces a resample in the player and another in PulseAudio.
    Ask the device; never assume.
    """
    sink = sink or resolve_sink()
    if not sink:
        return 48000, 2
    raw = _run(["pactl", "list", "sinks"])
    if not raw:
        return 48000, 2
    # Stop at the next `Sink #N` header. The previous terminator looked for a
    # line starting with `Sinks:`, which pactl never emits inside a sink
    # block, so the block ran to end of output when the sink was missing and
    # the regex happily returned the rate of some *other* sink.
    block = _sink_block(raw, sink)
    match = re.search(
        r"Sample Specification:\s*\S+\s+(\d+)ch\s+(\d+)Hz", "\n".join(block)
    )
    if not match:
        return 48000, 2
    return int(match.group(2)), int(match.group(1))


def handle_driver_command(text: str) -> Optional[str]:
    """Act on a spoken/typed driver command. Returns what to say, or None.

    Called from the voice bridge before the LLM, so "use headphones" is handled
    locally: instant, offline, no model call, and identical no matter which
    agent is running. The choice then applies to the reply that is spoken, and
    to every TTS after it.

    Only short, imperative utterances are matched. A 1-word match like
    "headphones" is deliberately NOT treated as a command on its own -- that
    is far more likely to be a fragment of a sentence the user is dictating to
    an agent than a request to change output devices.
    """
    if not text:
        return None
    words = text.strip().lower()
    if len(words) > 60 or "\n" in words:
        return None

    # A switch command is a short, whole utterance -- "use headphones",
    # "switch to hdmi". Requiring that keeps a dictated sentence that merely
    # mentions the word ("use headphones in the office next week") from being
    # swallowed and turned into a device change nobody asked for.
    # The driver must be the LAST word of the utterance. That single rule
    # separates every real request from ordinary dictation far better than a
    # length limit:
    #     "use headphones"                  -> headphones is last  = command
    #     "route sound through the monitor" -> monitor is last     = command
    #     "use headphones in the office"    -> office is last      = dictation
    tokens = re.sub(r"[^a-z ]", " ", words).split()
    # Skip conversational filler so "ok use headphones" still works.
    while tokens and tokens[0] in _COMMAND_FILLER:
        tokens.pop(0)
    if tokens and tokens[0] in _SWITCH_VERBS and tokens[-1] in _SPOKEN_DRIVER_NAMES:
        # Resolve the spoken word to its canonical name so the reply says
        # "headphones" even when the user said "ears" or "xbox".
        driver = _normalise(tokens[-1])
        if driver in DRIVER_PATTERNS:
            # set_driver_with_profile(), not set_driver()+apply_driver().
            # That pair only wrote the config and resolved a name, so a spoken
            # "use headphones" left the card in input-only mode and never moved
            # the PulseAudio default -- Hermes/ffplay then kept playing to the
            # old sink, which is silence whenever that port is empty.
            # A profile flip is correct HERE because this is an explicit user
            # action arriving before the reply is spoken; it must never run on
            # the routine play path (see apply_driver's docstring).
            sink = set_driver_with_profile(driver)
            rate, channels = sink_spec(sink)
            return (
                f"Voice now uses {driver}. "
                f"{rate} hertz, {channels} channels."
            )

    # Which one am I on?
    if re.search(
        r"\b(which|what)\b.*\b(audio|sound|voice|driver|output|device|"
        r"speaker|soundcard)\b",
        words,
    ) or re.search(r"\b(audio|sound|voice)\s+(status|settings?)\b", words):
        sink = apply_driver()
        rate, channels = sink_spec(sink)
        return (
            f"Voice is using {current_driver()} at {rate} hertz, "
            f"{channels} channels."
        )

    # What can I choose from?
    if re.search(
        r"\b(list|show|what|which)\b.*\b(choices?|options?|drivers?|devices?)\b",
        words,
    ):
        # Report the names the user would actually say, not PulseAudio's
        # serial-laden sink identifiers.
        labels = sorted({label for label in available_sinks().values() if label})
        if not labels:
            return "No audio drivers are available right now."
        return "You can choose: " + ", ".join(labels) + "."

    return None


# How a switch request may name each driver out loud. Built from the same
# synonym table the resolver uses, so "headset", "xbox" and "ears" all reach
# the headset just as "headphones" does.
_SPOKEN_DRIVER_NAMES = {
    "headphones", "headphone", "headset", "xbox", "controller", "ears",
    "hdmi", "display", "monitor", "tv",
    "analog", "jack", "wired", "speakers", "aux",
}

# A switch must READ as a command: start with one of these verbs, end with the
# driver. Both ends are required, because either alone is fooled by ordinary
# speech -- "remember to buy headphones" ends with the driver but is a note to
# an agent, not a device change.
_SWITCH_VERBS = {
    "use", "switch", "change", "move", "put", "route", "play", "turn",
    "set", "go", "switching", "using",
}

# Words that may precede the verb without being one.
_COMMAND_FILLER = {"ok", "okay", "hey", "please", "and", "um", "uh", "now", "just"}




# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="tts_control.py", description="AI Controller TTS control"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("driver", help="show the current driver")
    listing = sub.add_parser("drivers", help="list available drivers")
    listing.add_argument("--json", action="store_true")

    use = sub.add_parser("use", help="switch driver")
    use.add_argument("name")

    sub.add_parser("status", help="streams, wedge state, sink spec")
    status = sub.add_parser("streams", help="list TTS playback streams")
    status.add_argument("--json", action="store_true")

    # Machine-readable single values, for the shell wrappers to consume.
    # Deliberately print nothing else: these are captured with $(...).
    sub.add_parser("sink", help="print the resolved sink name for the driver")
    sub.add_parser("rate", help="print the resolved sink sample rate in Hz")
    sub.add_parser("channels", help="print the resolved sink channel count")
    sub.add_parser("wedged", help="exit 0 if the audio driver is wedged")
    sub.add_parser(
        "mute-streams", help="silence TTS streams; prints the count silenced"
    )

    args = parser.parse_args(argv)

    if args.cmd == "driver":
        print(current_driver())
    elif args.cmd == "sink":
        print(resolve_sink())
    elif args.cmd == "rate":
        print(sink_spec()[0])
    elif args.cmd == "channels":
        print(sink_spec()[1])
    elif args.cmd == "wedged":
        return 0 if controller_audio_wedged() else 1
    elif args.cmd == "mute-streams":
        # Stream silence only -- deliberately does not signal any process.
        # Used by tts_stop.sh, which does its own narrow player kill.
        count = 0
        for stream in tts_sink_inputs():
            if silence_stream(stream.index) != "failed":
                count += 1
        print(count)
    elif args.cmd == "drivers":
        found = available_sinks()
        if args.json:
            print(json.dumps(found, indent=2))
        else:
            if not found:
                print("no sinks found")
            for sink, friendly in found.items():
                label = friendly or "(unnamed)"
                marker = " <- current" if sink == resolve_sink() else ""
                print(f"{label:14} {sink}{marker}")
    elif args.cmd == "use":
        try:
            chosen = set_driver(args.name)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        sink = set_driver_with_profile(chosen)
        rate, channels = sink_spec(sink)
        print(f"driver = {chosen}\nsink   = {sink}\nformat = {rate}Hz {channels}ch")
    elif args.cmd == "streams":
        streams = tts_sink_inputs()
        if args.json:
            print(json.dumps([s._asdict() for s in streams], indent=2))
        else:
            if not streams:
                print("no TTS playback streams")
            for s in streams:
                print(f"  #{s.index} pid={s.pid} name={s.name!r} sink={s.sink}")
    elif args.cmd == "status":
        sink = resolve_sink()
        rate, channels = sink_spec(sink)
        print(f"driver     : {current_driver()}")
        print(f"sink       : {sink}")
        print(f"format     : {rate}Hz {channels}ch")
        print(f"wedged     : {controller_audio_wedged()}")
        streams = tts_sink_inputs()
        print(f"tts streams: {len(streams)}")
        for s in streams:
            print(f"  #{s.index} pid={s.pid} name={s.name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
