# Tahuti — Muse Spark driving the real Linux desktop

Tahuti is AI Controller's second mode. Today the controller is *reflexive*:
the trigger maps to a fixed function. Tahuti gives it a **directive mode** —
you say what you want ("check my emails", "build this"), and an agent figures
out how, acting on the machine as it goes.

The reasoning engine is Meta's **Muse Spark** (vision + reasoning), reached
through Meta's Model API.

## The gap this closes

Meta publishes a "Computer use" cookbook recipe. Read it and the constraint
is immediate:

> The desktop lives in a throwaway **Cua** sandbox... Unlike the sibling
> macOS computer use recipe, which drives your real Mac with native events,
> this recipe keeps everything inside a disposable container — **the model
> never touches your machine.**

Meta's macOS recipe drives a real Mac. **There is no Linux equivalent that
drives a real desktop.** That is the gap Tahuti fills: Muse Spark driving
the operator's actual Linux desktop, through the same look → act → look loop,
with a real screen instead of a disposable container.

Everything here is Linux-desktop only. The macOS and Windows ports of AI
Controller do not carry this module.

## What it is

| File | Role |
|---|---|
| `scripts/tahuti_desktop.py` | The desktop primitives: screenshot, click, type, key, command. Each gated on the shared approval gate. |
| `scripts/tahuti_mcp.py` | The MCP server exposing them over stdio as `cua_*` tools. |
| `tests/test_tahuti_desktop.py` | 17 tests. |

The tool names match Meta's exactly — `cua_screenshot`, `cua_left_click`,
`cua_right_click`, `cua_move_cursor`, `cua_type_text`, `cua_press_key`,
`cua_run_command` — so **the published prompt works verbatim**:

> You are operating a separate Linux desktop computer. The ONLY way to
> interact with it is through the cua_\* tools...

That "ONLY way" framing is load-bearing. Without it the agent reaches for its
own host shell, finds the target app isn't there, and gets confused about
which machine it is on.

## Safety: it fails closed

Every action goes through master-ai-cli's shared approval gate
(`~/master-ai-cli/scripts/approval_gate.py`) using the `cua:<action>:<mode>`
pattern keys that already existed in `master-ai-cli/scripts/computer_use_tool.py`
for exactly this tool.

- **Fails closed.** With no human present — a cron run, a headless API
  server — every action is a hard DENY. Verified live: with no callback
  registered, screenshot / click / type / run_command are all refused. An
  unattended agent cannot take over the operator's live desktop.
- **Mode-scoped.** A grant for `foreground` never covers `background`.
- **The screenshot is deleted** from `/tmp` immediately after it is read. It
  can contain whatever was on screen.
- **A refusal is reported, not raised.** The model sees `REFUSED: ...` and
  stops, instead of retrying forever against a gate that will keep saying no.

## Not reimplemented on purpose

Every mechanism already existed on this box and is proven live. Tahuti is
the bridge that exposes them to a vision model — not a second implementation
of any of them:

- `scripts/focus_guard.py` — xdotool fires blind at whatever is focused. If
  focus drifts between aiming and acting, the input lands in the wrong place
  *silently* (confirmed live on this box: a keystroke landed in an unrelated
  terminal with zero error). focus_guard verifies focus **before and after**
  every injection and raises instead of guessing. Mouse and keyboard actions
  route through it.
- The click "settle" trick — a raw uinput click fires no mousemove, so
  browser/Electron hover state can be stale and the click lands on nothing.
  A 1px out-and-back mousemove before the click wakes the target
  (confirmed live 2026-07-21).
- `scrot` for capture, `xdotool` for input, `ptt_pynput.py` for typing.

## Setup

Register with OpenCode in `~/.config/opencode/opencode.json`:

```jsonc
{
  "mcp": {
    "cua": {
      "type": "local",
      "command": [
        "/home/elijah/ai-controller/.venv/bin/python",
        "/home/elijah/ai-controller/scripts/tahuti_mcp.py"
      ],
      "environment": { "TAHUTI_MODE": "foreground" },
      "enabled": true
    }
  }
}
```

Then pick the Meta provider in OpenCode (`/connect` → Meta) and select a
Muse Spark model.

**One dependency outside this repo:** the approval gate lives in
`~/master-ai-cli/scripts/approval_gate.py` and is imported by path. It is
loaded under its canonical module name so the host and this module share
**one** gate instance — the gate holds process-wide state (the explicit
callback, session grants, the permanent allowlist), and loading it under a
private name would silently create a second copy whose grants never apply.
If the gate file is missing, this module refuses to act at all.

## Verified live on this machine

- Screenshot: real PNG of the actual desktop, 1280x720, valid base64, and
  the temp file deleted afterwards.
- Typing: `TAHUTI_MARKER_7731` typed into a scratch terminal, confirmed by
  reading the screen back.
- Click: pointer moved to exactly (640, 360) and confirmed via
  `xdotool getmouselocation`.
- Key press: Return delivered through the focus-verified path.
- MCP over stdio: `initialize`, `tools/list` (7 tools), `tools/call`
  returning a real PNG image block, a real command, and a real click.

**Not verified:** the model loop itself. There is no Meta API key on this
machine, so Muse Spark has not been driven against this bridge. The
untested seam is the model call; the bridge underneath it is tested.

## Known limits

- **The 50-images-per-request API limit** is real. Meta's own recipe hit it
  playing Minesweeper and solved it by delegating the board to a series of
  short-lived sub-agents, each taking a fresh batch of screenshots. A long
  task here needs the same shape: an orchestrator delegating to
  short-lived sub-agents, not one flat loop.
- `run_command` is not scoped to the desktop the way a click is — it runs
  wherever the agent runs. That is what lets the model discover an app it
  was never told the location of (`which <app>`, `ls /usr/games`), and it
  is also the highest-blast-radius tool in the set.
- Pillow is used to downscale screenshots but is not a declared dependency
  of AI Controller. Without it the original (larger) image is returned — a
  bigger image costs tokens but is still a correct image; a missing image
  is a dead loop.
