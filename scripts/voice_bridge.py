#!/usr/bin/env python3
"""
Voice bridge — audio → Groq Whisper → TTS
Runs on :8002. push-to-talk.sh POSTs audio to /voice?mode=transcribe_only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

# Make ai_controller_paths importable when running from any cwd.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
# Shared TTS control (scripts/tts_control.py): driver selection, the
# live sink format, and the stream-level barge-in used by ptt_pynput.
# One implementation, so the bridge and the CLI cannot disagree about
# which device is playing or what rate it wants.
import tts_control  # noqa: E402
import voice_goal_schema as vgs  # noqa: E402
from ai_controller_paths import ai_controller_dir, config_dir, load_env
from speech_text import spoken_form  # noqa: E402  (shared with the goal runner)

# -----------------------------------------------------------------------------
# Logging setup — vocal debug output
# -----------------------------------------------------------------------------
LOG_DIR = Path(ai_controller_dir()) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "voice-bridge.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        # DEBUG-level HTTP tracing (full Groq request/response bodies) makes
        # this grow fast with no bound -- 5.9MB and climbing before this fix,
        # slow to grep/tail and never trimmed. Cap at 5MB x 3 backups.
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
        ),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logger.info("Voice Bridge starting up — logs at %s", LOG_FILE)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

_env = load_env()
CLAF_URL = (os.environ.get("CLAF_URL") or _env.get("CLAF_URL") or "http://localhost:8000").rstrip("/")
VOICE_BRIDGE_API_KEY = os.environ.get("VOICE_BRIDGE_API_KEY", "")


def _load_groq_key() -> str:
    """Load the live Groq key from env / ai-controller config / master keychain."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key and not key.startswith("***") and len(key) > 20:
        return key
    key = load_env().get("GROQ_API_KEY", "").strip()
    if key and not key.startswith("***") and len(key) > 20:
        return key
    # Fallback to the master keychain env file (plain KEY=VALUE format).
    keychain = Path.home() / "Desktop" / "Projects" / "keychain" / "master_ai_keys"
    if keychain.exists():
        try:
            for line in keychain.read_text().splitlines():
                if line.strip().startswith("GROQ_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"\'')
                    if key and not key.startswith("***") and len(key) > 20:
                        return key
        except Exception:
            pass
    return ""


GROQ_KEY = _load_groq_key()


def _load_elevenlabs_key() -> str:
    """Load the live ElevenLabs key. Same 3-source pattern as _load_groq_key."""
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if key and not key.startswith("***") and len(key) > 20:
        return key
    key = load_env().get("ELEVENLABS_API_KEY", "").strip()
    if key and not key.startswith("***") and len(key) > 20:
        return key
    keychain = Path.home() / "Desktop" / "Projects" / "keychain" / "master_ai_keys"
    if keychain.exists():
        try:
            for line in keychain.read_text().splitlines():
                if line.strip().startswith("ELEVENLABS_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"\'')
                    if key and not key.startswith("***") and len(key) > 20:
                        return key
        except Exception:
            pass
    return ""


ELEVENLABS_KEY = _load_elevenlabs_key()

# -----------------------------------------------------------------------------
# Triage: task verbs vs chat/lookup
# -----------------------------------------------------------------------------
# All verb/chat classification lives in voice_goal_schema so the bridge and the
# Tahuti runner agree on the same contract.

GOALS_DIR = Path.home() / ".tahuti" / "goals"


def _write_goal_file(transcript: str) -> Path:
    """Write an enriched goal file for the Tahuti headless runner.

    Format is the shared schema in ~/.tahuti/goals/<uuid>.json.
    Directory is created on first use.
    """
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    path = GOALS_DIR / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(vgs.build_goal_payload(transcript), indent=2), encoding="utf-8")
    return path


GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_TIMEOUT = float(os.environ.get("VOICE_BRIDGE_GROQ_TIMEOUT", "30"))
CLAF_TIMEOUT = float(os.environ.get("VOICE_BRIDGE_CLAF_TIMEOUT", "120"))
MAX_TRANSCRIPT_CHARS = int(os.environ.get("VOICE_BRIDGE_MAX_TRANSCRIPT_CHARS", "2000"))

app = FastAPI(title="voice-bridge", version="1.1")

AI_DIR = ai_controller_dir()
PIPER_MODEL = os.path.join(AI_DIR, "voices", "en_US-joe-medium.onnx")
HERMES_TTS_PLAY = os.path.join(AI_DIR, "scripts", "hermes_tts_play.sh")
EDGE_VOICE = "en-US-AriaNeural"
EDGE_PITCH = "-22Hz"
EDGE_RATE = "+18%"

# TTS_ENGINE switch (2026-11-01): "elevenlabs" while the operator tunes voice
# choices for latency + realism, "edge" to go back to the free Microsoft
# voice above. Override in ~/.config/ai-controller/config.env, no code
# change needed to flip back.
TTS_ENGINE = (os.environ.get("TTS_ENGINE", "").strip()
              or load_env().get("TTS_ENGINE", "elevenlabs").strip()).lower()
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
# Default voice: Sarah (premade, free-tier). Auditioned 2026-11-01 —
# Rachel (21m00Tcm4TlvDq8ikWAM) is library-gated (paid only, 402
# payment_required on free). Samples: /tmp/opencode/voice_{Sarah,Laura,
# EdgeAria}.mp3. Laura is the backup pick. Override ELEVENLABS_VOICE_ID in
# config.env to switch.
# Sarah - Mature, Reassuring, Confident:
ELEVENLABS_VOICE_ID = (os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
                        or load_env().get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL").strip())
# Human-tune (2026-11-01, free-tier Flash): stability 0.5 keeps expression
# without wobble; similarity_boost 0.75 (docs default); style 0.0 (neutral —
# raise to ~0.3 only if she sounds flat); use_speaker_boost true (clarity).
# v4 models drop style/speed — these settings target eleven_flash_v2_5.
ELEVENLABS_STABILITY = float(os.environ.get("ELEVENLABS_STABILITY", "")
                             or load_env().get("ELEVENLABS_STABILITY", "0.5"))
ELEVENLABS_SIMILARITY = float(os.environ.get("ELEVENLABS_SIMILARITY", "")
                              or load_env().get("ELEVENLABS_SIMILARITY", "0.75"))
ELEVENLABS_STYLE = float(os.environ.get("ELEVENLABS_STYLE", "")
                         or load_env().get("ELEVENLABS_STYLE", "0.0"))
# Free-tier quota guard (Flash = 20k chars/mo). Usage tracked locally;
# synthesis refuses into Edge fallback past the cap — never a bill.
ELEVENLABS_QUOTA_FILE = os.path.join(config_dir(), "elevenlabs_usage.json")
ELEVENLABS_MONTHLY_CAP = 20000
# Flash model: lowest latency ElevenLabs offers -- this switch's whole point
# is cutting response time, not just changing the voice.
ELEVENLABS_MODEL = (os.environ.get("ELEVENLABS_MODEL", "").strip()
                     or load_env().get("ELEVENLABS_MODEL", "eleven_flash_v2_5").strip())

# Strong refs to in-flight background TTS tasks. asyncio only holds weak refs to
# tasks, so without this a speak task can be garbage-collected mid-generation.
_tts_tasks: set = set()


def _speak_bg(text: str) -> None:
    """Fire-and-forget _speak on a worker thread.

    _speak() blocks: it shells out to edge_tts, which is a NETWORK call to
    Microsoft with timeout=30. Calling it directly from an `async def` handler
    blocks the whole asyncio event loop, so the bridge can serve nothing else
    for the duration — that is what produced the historical "transcribe timed
    out after 30 seconds" failures. The STT request was never slow; it was
    queued behind a TTS generation holding the only thread.
    """
    if not text:
        return
    task = asyncio.create_task(asyncio.to_thread(_speak, text))
    _tts_tasks.add(task)
    task.add_done_callback(_tts_tasks.discard)


def _generate_edge(spoken: str, mp3_path: str) -> None:
    """Generate speech via Edge TTS (AriaNeural) with tuned voice settings."""
    subprocess.run(
        [sys.executable, "-m", "edge_tts", "--voice", EDGE_VOICE,
         "--pitch=" + EDGE_PITCH,
         "--rate=" + EDGE_RATE,
         "--text", spoken,
         "--write-media", mp3_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=30
    )


def _elevenlabs_quota_ok(n_chars: int) -> bool:
    """Free-tier guard: refuse into Edge fallback past the monthly cap."""
    import datetime as _dt
    month = _dt.date.today().strftime("%Y-%m")
    try:
        usage = json.loads(Path(ELEVENLABS_QUOTA_FILE).read_text())
    except Exception:
        usage = {}
    if usage.get("month") != month:
        usage = {"month": month, "chars": 0}
    if usage.get("chars", 0) + n_chars > ELEVENLABS_MONTHLY_CAP:
        logger.warning("ElevenLabs quota cap reached — Edge fallback")
        return False
    usage["chars"] = usage.get("chars", 0) + n_chars
    try:
        Path(ELEVENLABS_QUOTA_FILE).write_text(json.dumps(usage))
    except Exception as exc:
        logger.warning("quota write failed: %s", exc)
    return True


def _generate_elevenlabs(spoken: str, mp3_path: str) -> None:
    """Generate speech via ElevenLabs Flash streaming endpoint.

    Raises on any failure (or quota cap) so the caller falls back to Edge
    TTS instead of going silent. Measured 2026-11-01: stream total ~0.8s
    vs batch ~2.2s vs edge batch ~1.4s; first audio ~0.45s.
    """
    if not _elevenlabs_quota_ok(len(spoken)):
        raise RuntimeError("quota cap")
    # stream endpoint, not convert: chunks arrive while generating.
    req = {"text": spoken, "model_id": ELEVENLABS_MODEL,
           "voice_settings": {"stability": ELEVENLABS_STABILITY,
                              "similarity_boost": ELEVENLABS_SIMILARITY,
                              "style": ELEVENLABS_STYLE,
                              "use_speaker_boost": True}}
    data = json.dumps(req).encode()
    url = (f"{ELEVENLABS_TTS_URL}/{ELEVENLABS_VOICE_ID}/stream"
           f"?model_id={ELEVENLABS_MODEL}&output_format=mp3_22050_32")
    hreq = urllib.request.Request(
        url, data=data,
        headers={"xi-api-key": ELEVENLABS_KEY, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(hreq, timeout=20) as resp:
        body = resp.read()
    if len(body) < 1000:
        raise RuntimeError(f"suspicious reply ({len(body)}B): {body[:120]!r}")
    Path(mp3_path).write_bytes(body)


def _speak(text: str) -> None:
    """Speak text using the configured TTS_ENGINE (elevenlabs, falling back
    to Edge TTS/AriaNeural on failure; "edge" to use Edge TTS directly).

    BLOCKING — never call directly from an async handler. Use _speak_bg().
    """
    if not text:
        return
    # Barge-in tombstone: edge_tts generation takes ~4s, so a trigger press
    # during that window has no player to SIGTERM — the speech would start
    # AFTER the user asked for silence. Record when this request began; if the
    # barge file (touched by ptt_pynput._mute_tts on every press) is newer by
    # the time generation finishes, the user has spoken since — stay silent.
    t0 = time.time()
    spoken = spoken_form(text)  # whole answer (was: first line only)
    if not spoken:
        return
    try:
        mp3_fd, mp3_path = tempfile.mkstemp(suffix=".mp3", prefix="tts_")
        os.close(mp3_fd)

        if TTS_ENGINE == "elevenlabs" and ELEVENLABS_KEY:
            try:
                _generate_elevenlabs(spoken, mp3_path)
            except Exception as exc:
                logger.warning("ElevenLabs TTS failed (%s) -- falling back to Edge TTS", exc)
                _generate_edge(spoken, mp3_path)
        else:
            _generate_edge(spoken, mp3_path)

        try:
            if os.path.getmtime('/tmp/ai_tts_barge') >= t0:
                logger.info("TTS suppressed: barge-in during generation window")
                os.unlink(mp3_path)
                return
        except OSError:
            pass  # no barge file yet — nothing to suppress

        # Play through hermes_tts_play.sh, which resamples once with soxr into
        # the sink's native format. Do NOT add a lowpass filter here: it does
        # not tune the device, it masks resampler aliasing by throwing away
        # everything above 3 kHz. See hermes_tts_play.sh for the full trace.
        if os.path.exists(HERMES_TTS_PLAY):
            subprocess.Popen(
                [HERMES_TTS_PLAY, mp3_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Fallback: direct mpv, used only when hermes_tts_play.sh is
            # missing. Every TTS player MUST carry --force-media-title=
            # AI_TTS_BARGE or barge-in cannot find it and the speech becomes
            # unstoppable.
            #
            # The rate must match the sink that is actually live. Hardcoding
            # 48k (as this did until 2026-09-29) was correct only for the
            # controller headset; on a split mic-controller/HDMI-headphones
            # setup the sink is 44.1k, and the mismatch forced a second
            # resample in PulseAudio that audibly crackled.
            # apply_driver() also brings the controller's output profile up
            # when the user has selected headphones, so the sink exists to be
            # targeted at all.
            rate, channels = tts_control.sink_spec(tts_control.apply_driver())
            subprocess.Popen(
                ["mpv", "--no-video", "--force-media-title=AI_TTS_BARGE",
                 f"--audio-samplerate={rate}", f"--audio-channels={channels}",
                 "--audio-format=s16", mp3_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        # Ultimate fallback: speech-dispatcher. No -w — that blocks until the
        # sentence finishes, which is the one thing a barge-in must never do.
        # spd-say is cancelled with `spd-say -C`, which tts_stop.sh issues.
        subprocess.Popen(
            ["spd-say", spoken],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


# -----------------------------------------------------------------------------
# Security: local-only + optional API key
# -----------------------------------------------------------------------------

def _local_only(request: Request) -> None:
    """Reject requests that do not originate from localhost."""
    host = request.client.host if request.client else None
    # 'testclient' is the host used by FastAPI TestClient during unit tests.
    allowed = {"127.0.0.1", "::1", "localhost", "testclient", None}
    if host not in allowed:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="voice bridge is localhost-only")


def _api_key_ok(request: Request) -> None:
    """Reject requests without the configured API key, if one is set."""
    if not VOICE_BRIDGE_API_KEY:
        return
    header = request.headers.get("x-api-key", "")
    if header != VOICE_BRIDGE_API_KEY:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="invalid or missing API key")


async def _secure(request: Request) -> None:
    _local_only(request)
    _api_key_ok(request)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _extract_claf_text(payload: dict) -> str:
    """Pull assistant text out of CLAF's Anthropic-format response."""
    for block in payload.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "").strip()
    return ""


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.post("/voice")
async def voice(
    request: Request,
    audio: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    mode: str = Form("execute"),
):
    transcript = (text or "").strip()[:MAX_TRANSCRIPT_CHARS]

    # ── STT — transcribe audio if no text provided ────────────────────────────
    if audio and not transcript:
        data = await audio.read()
        logger.debug("Received audio: %d bytes", len(data))
        if len(data) < 2000:
            logger.warning("Audio too short: %d bytes", len(data))
            return JSONResponse({"error": "audio too short"}, status_code=400)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
            tmp = f.name

        try:
            logger.info("Sending audio to Groq STT (whisper-large-v3-turbo)")
            async with httpx.AsyncClient(timeout=GROQ_TIMEOUT) as client:
                with open(tmp, "rb") as fp:
                    try:
                        r = await client.post(
                            GROQ_STT_URL,
                            headers={"Authorization": f"Bearer {GROQ_KEY}"},
                            files={"file": ("audio.wav", fp, "audio/wav")},
                            data={
                                "model": "whisper-large-v3-turbo",
                                "prompt": (
                                    "Common terms: CLAF, Madam Mary, Hermes, Kimi, "
                                    "Groq, Whisper, STT, TTS, PTT, Xbox, Microsoft, AI controller, "
                                    "AI Desktop, Fair Chance, Command Center, Monday.com, Railway, "
                                    "Meta, Facebook, Indeed, ZipRecruiter, Discord, Telegram, Sensei."
                                ),
                            },
                        )
                        r.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        return JSONResponse(
                            {"error": f"Groq STT HTTP {exc.response.status_code}"},
                            status_code=502,
                        )
                    except httpx.RequestError as exc:
                        logger.error("Groq STT request failed: %s", exc)
                        return JSONResponse(
                            {"error": f"Groq STT request failed: {exc}"},
                            status_code=502,
                        )
            transcript = r.json().get("text", "").strip()[:MAX_TRANSCRIPT_CHARS]
            logger.info("STT transcript: '%s'", transcript[:200])
        finally:
            os.unlink(tmp)

    if not transcript:
        logger.warning("Empty transcript received")
        return JSONResponse({"error": "empty transcript"}, status_code=400)

    # FAIL CLOSED on mode. Only an explicit mode=="execute" may reach the
    # LLM + TTS path below. This used to be `if mode == "transcribe_only"`,
    # which meant any OTHER string — a typo, a test curl sending
    # mode=transcribe — fell through to execute: the bridge transcribed 3s of
    # room audio, asked the LLM about it, and SPOKE the answer unprompted.
    # Observed 2026-08-06 as "ghost conversations": the verifier's test
    # captures were being answered aloud by llama-3.3-70b.
    if mode != "execute":
        if mode != "transcribe_only":
            logger.warning("Unknown mode %r — failing closed to transcribe-only", mode)
        logger.debug("Transcribe-only mode, returning transcript")
        return JSONResponse({"text": transcript, "transcript": transcript})

    # ── Local device commands ────────────────────────────────────────────────
    # "use headphones" / "switch to hdmi" / "what audio am I using" are
    # handled HERE rather than being sent to the LLM. Three reasons: the answer
    # is a fact about this machine, not something to reason about; it must work
    # the same whichever agent happens to be running (Hermes, Master AI,
    # Claude); and a device change must not depend on a model call succeeding.
    # The reply is spoken, so you get confirmation without looking.
    local = tts_control.handle_driver_command(transcript)
    if local is not None:
        logger.info("Local device command: %r", transcript[:80])
        _speak_bg(local)
        return JSONResponse(
            {"transcript": transcript, "response": local, "local": True}
        )

    # ── Everything is a Tahuti goal now (2026-11-01) ──────────────────────────
    # This used to fork here: "task" transcripts got a real goal file, while
    # "chat"/"lookup" transcripts were answered inline, right here, by a
    # separate direct LLM call (Meta Spark / Groq fallback below -- removed).
    # That inline path had no persistent memory and bypassed Tahuti's actual
    # agent framework entirely (tools, approval gating, the conversation
    # history Tahuti now keeps across goals) -- a plain conversational
    # question got a different, thinner path than a task, with no memory of
    # what either one said a minute ago. One path, one memory, same as every
    # other goal source (ptt_pynput.py's Ctrl+F13 goal-take already only ever
    # went this way).
    goal_type = vgs.task_type(transcript) or vgs.classify_transcript(transcript)
    goal_path = _write_goal_file(transcript)
    ack = "On it"
    logger.info("Goal written: %r -> %s (%s)", transcript[:80], goal_path, goal_type)
    _speak_bg(ack)
    return JSONResponse(
        {
            "transcript": transcript,
            "response": ack,
            "routed": "task",
            "goal_type": goal_type,
            "goal_path": str(goal_path),
        }
    )


@app.post("/speak")
async def speak_endpoint(request: Request, text: str = Form(...)):
    """Hermes-compatible TTS endpoint: POST text and speak it immediately."""
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    _speak_bg(text)  # spoken_form() inside _speak applies the length cap
    return JSONResponse({"spoken": True})


# Apply security dependency to all routes.
app.router.dependencies.append(Depends(_secure))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="warning")
