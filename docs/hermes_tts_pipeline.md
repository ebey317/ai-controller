# Hermes TTS pipeline — schematic + failure record

Two separate TTS stacks exist on this machine. They must never be cross-wired —
doing exactly that is what broke voice for about a month (see Incident 001).

## Stack A — Hermes agent's own voice (this doc's subject)

```
Hermes agent decides to speak
        |
        v
tools/tts_tool.py  (~/.hermes/hermes-agent/tools/tts_tool.py)
        |
        v
_load_tts_config()  <-- reads ~/.hermes/config.yaml  "tts:" block  (NOT in this git repo)
        |
        v
tts.provider  (must be one of: piper | edge | elevenlabs | gemini | openai |
               mistral | neutts | kittentts | xai | a custom "command" provider)
        |
        v
  provider == "piper"                    provider == "<custom>" (type: command)
  --------------------                   --------------------------------------
  _generate_piper_tts()                  runs config's `command:` template with
  - native piper-tts pip package,        {text_path}/{output_path} substituted.
    already installed in Hermes's own    CONTRACT: the command must GENERATE
    venv, fully offline                  audio and WRITE it to {output_path},
  - tts.piper.voice = absolute path      then exit 0. It must NOT play audio —
    to voices/joe/en_US-joe-medium.onnx  Hermes plays the result itself, next.
    (this repo, Case-1 direct-path       Any script that only knows how to PLAY
    resolution, no download/network)     an existing file (takes one positional
        |                                arg, no --text/--output) will crash
        v                                here with "File not found: <flag>".
tts_tool writes the returned audio file
        |
        v
tools/voice_mode.py -> play_audio_file()
  - Linux: ffplay (preferred) / aplay fallback, sounddevice for .wav
  - plays to the SYSTEM DEFAULT PulseAudio sink — no device selection logic
    of its own, no custom resampling
        |
        v
PulseAudio  (system defaults matter here, see "System audio defaults" below)
        |
        v
Xbox controller headset (hardware)
```

## Stack B — ai-controller's own voice announcements (scripts/, this repo)

Separate, unrelated pipeline used for controller/dictation announcements
(`speak.sh`, `tts_say.sh`, `tts_stop.sh`, `voice_toggle.py`). Entry point for
playback is `scripts/hermes_tts_play.sh`, which is **playback-only**: one
positional `<audio_file>` argument, resolves the Xbox/Microsoft controller
sink explicitly, and resamples with `soxr:precision=28` before handing
PulseAudio a stream already in the sink's native rate — see the comment block
at the top of that script for the full static/aliasing story.

`hermes_tts_play.sh` belongs to Stack B only. It does not implement Stack A's
generate-to-{output_path} contract and must never be referenced from
`~/.hermes/config.yaml`.

## System audio defaults (checked 2026-08-18, not part of this repo)

- `pactl get-default-sink` = the Xbox/Microsoft controller headset sink.
- `/etc/pulse/daemon.conf` has `resample-method = soxr-vhq` set system-wide.

Both of these mean Stack A's plain `ffplay`/`aplay` default-device playback
already gets the right device and high-quality resampling without going
through `hermes_tts_play.sh`. If either of these system defaults ever
changes (new default sink, resample-method reverted), Stack A's audio
quality/routing will regress even though Stack A's code hasn't changed —
check `pactl get-default-sink` and `/etc/pulse/daemon.conf` first before
assuming a code regression.

## Incident 001 — Stack A crashed for ~1 month (found/fixed 2026-08-18)

**Symptom:** Hermes desktop showed `tts chunk one failed` / `TTS generation
failed` / `TTS provider 'ai-controller' exited with code 1` on every attempt
to speak.

**Root cause:** `~/.hermes/config.yaml` had:

```yaml
tts:
  provider: ai-controller
  providers:
    ai-controller:
      command: /home/elijah/ai-controller/scripts/hermes_tts_play.sh --text {text_path} --output {output_path}
      type: command
```

That wires Stack A to Stack B's playback-only script. `hermes_tts_play.sh`
takes `$1` as an audio file path; called this way `$1` was the literal string
`--text`, its `-f` existence check failed, and it printed
`Error: File not found: --text` / exit 1 — reproduced verbatim against the
error log by running the command directly.

**Fix:** Set `tts.provider: piper` and removed the `tts.providers.ai-controller`
block. `tts.piper.voice` was already correctly pointed at
`voices/joe/en_US-joe-medium.onnx` in this repo (Case-1 direct-path
resolution — no download, no network dependency). Verified by calling
`tools.tts_tool._generate_piper_tts()` directly (the same code path the live
agent uses) — synthesis succeeded and played cleanly.

**Why it drifted:** a comment in `scripts/ptt_pynput.py`'s `_mute_tts()`
docstring documents the *intended* config as "Hermes' built-in TTS
(provider: piper)" — so `tts.provider: piper` was the known-good baseline.
Something rewired it to the broken custom-command form roughly a month
before the fix, most likely while trying to force Stack A's audio through
the same Xbox-headset path Stack B uses, without realizing Stack A's
command-provider contract is generate-only.

**Guardrail for next time:** `~/.hermes/config.yaml` is user-level app config
and is intentionally *not* tracked by this repo's git history, so a bad edit
there leaves no diff to catch it. Before changing `tts.provider` or
`tts.providers.*`, re-read this file. If Stack A breaks again with a
`TTS provider '<name>' exited with code N` error, check `tts.provider` +
the matching `command:` template first, and confirm the command is
generate-only (writes to `{output_path}`, does not itself call mpv/ffplay).
