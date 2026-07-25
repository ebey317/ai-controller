#!/usr/bin/env python3
"""
Voice bridge — audio → Groq Whisper → TTS
Runs on :8002. push-to-talk.sh POSTs audio to /voice?mode=transcribe_only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

# -----------------------------------------------------------------------------
# Logging setup — vocal debug output
# -----------------------------------------------------------------------------
LOG_DIR = Path.home() / "ai-controller" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "voice-bridge.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logger.info("Voice Bridge starting up — logs at %s", LOG_FILE)

# Make ai_controller_paths importable when running from any cwd.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from ai_controller_paths import ai_controller_dir, load_env
import voice_toggle


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

VOICE_BRIDGE_API_KEY = os.environ.get("VOICE_BRIDGE_API_KEY", "")


def _load_groq_key() -> str:
    """Load the live Groq key from env / ai-controller config.env."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key and not key.startswith("***") and len(key) > 20:
        return key
    key = load_env().get("GROQ_API_KEY", "").strip()
    if key and not key.startswith("***") and len(key) > 20:
        return key
    return ""


GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_TIMEOUT = float(os.environ.get("VOICE_BRIDGE_GROQ_TIMEOUT", "30"))
MAX_TRANSCRIPT_CHARS = int(os.environ.get("VOICE_BRIDGE_MAX_TRANSCRIPT_CHARS", "2000"))

app = FastAPI(title="voice-bridge", version="1.1")

AI_DIR = ai_controller_dir()
HERMES_TTS_PLAY = os.path.join(AI_DIR, "scripts", "hermes_tts_play.sh")


def _speak(text: str) -> None:
    """Speak text using the active AI Controller voice pack (Edge TTS)."""
    if not text:
        return
    spoken = text[:500].split("\n")[0]

    # Load the active voice pack for voice/rate/pitch settings.
    voice_id = voice_toggle.load_voice()
    voice = voice_toggle.get_voice(voice_id)
    if voice and voice.get("engine") == "edge-tts":
        edge_voice = voice.get("voice", "en-US-AriaNeural")
        edge_pitch = voice.get("pitch", "+0Hz")
        edge_rate = voice.get("rate", "+0%")
    else:
        # Fallback to Aria defaults if voice pack is Piper or missing.
        edge_voice = "en-US-AriaNeural"
        edge_pitch = "-22Hz"
        edge_rate = "+18%"

    try:
        mp3_fd, mp3_path = tempfile.mkstemp(suffix=".mp3", prefix="tts_")
        os.close(mp3_fd)

        subprocess.run(
            [sys.executable, "-m", "edge_tts", "--voice", edge_voice,
             "--pitch=" + edge_pitch,
             "--rate=" + edge_rate,
             "--text", spoken,
             "--write-media", mp3_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=30
        )
        
        # Play through tuned mpv pipeline (lowpass filter, correct sink)
        if os.path.exists(HERMES_TTS_PLAY):
            subprocess.Popen(
                [HERMES_TTS_PLAY, mp3_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Fallback: direct mpv playback
            subprocess.Popen(
                ["mpv", "--no-video", "--af=lowpass=f=3000", mp3_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        # Ultimate fallback to spd-say
        subprocess.Popen(
            ["spd-say", "-w", spoken],
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


def _extract_groq_text(payload: dict) -> str:
    """Pull assistant text out of a Groq chat completion."""
    choices = payload.get("choices") or [{}]
    return (choices[0].get("message", {}) or {}).get("content", "").strip()


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
                            headers={"Authorization": f"Bearer {_load_groq_key()}"},
                            files={"file": ("audio.wav", fp, "audio/wav")},
                            data={
                                "model": "whisper-large-v3-turbo",
                                "prompt": (
                                    "Common terms: AntiMicroX, Xbox, Microsoft, "
                                    "AI controller, controller, headset, dictation, "
                                    "keyboard, mouse, Discord, Telegram."
                                ),
                            },
                            timeout=GROQ_TIMEOUT,
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

    if mode == "transcribe_only":
        logger.debug("Transcribe-only mode, returning transcript")
        return JSONResponse({"text": transcript})

    # ── LLM — route voice commands through Groq free tier ───────────────────
    # Direct Groq call for speed.
    # Free models: llama-3.3-70b-versatile, llama-3.1-8b-instant, qwen3-32b
    GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
    GROQ_LLM_MODEL = os.environ.get("VOICE_BRIDGE_LLM_MODEL", "llama-3.3-70b-versatile")
    payload = {
        "model": GROQ_LLM_MODEL,
        "messages": [{"role": "user", "content": transcript}],
        "max_tokens": 256,
    }

    logger.info("Sending transcript to Groq LLM (%s)", GROQ_LLM_MODEL)
    async with httpx.AsyncClient(timeout=GROQ_TIMEOUT) as client:
        try:
            r = await client.post(
                GROQ_CHAT_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {_load_groq_key()}",
                },
                json=payload,
            )
            r.raise_for_status()
            response_text = _extract_groq_text(r.json())
            logger.info("LLM response: '%s'", response_text[:200])
        except httpx.HTTPStatusError as exc:
            logger.error("Groq LLM HTTP %s", exc.response.status_code)
            return JSONResponse(
                {"transcript": transcript, "error": f"Groq LLM HTTP {exc.response.status_code}"},
                status_code=502,
            )
        except httpx.RequestError as exc:
            logger.error("Groq LLM request failed: %s", exc)
            return JSONResponse(
                {"transcript": transcript, "error": f"Groq LLM request failed: {exc}"},
                status_code=502,
            )

    # ── TTS — speak response ──────────────────────────────────────────────────
    # Offloaded to a thread: _speak() runs a blocking subprocess.run() for
    # edge-tts generation, which would otherwise freeze uvicorn's single
    # event loop for the whole call — stalling every other in-flight request
    # (including the next dictation trigger press) until it returns.
    logger.info("Speaking response via TTS")
    asyncio.create_task(asyncio.to_thread(_speak, response_text))

    return JSONResponse({"transcript": transcript, "response": response_text})


@app.get("/health")
async def health_endpoint():
    """Readiness check used by start-all.sh."""
    key = _load_groq_key()
    if not key:
        return JSONResponse({"status": "not_configured", "groq_key": False}, status_code=503)
    return JSONResponse({"status": "ok", "groq_key": True})


@app.post("/speak")
async def speak_endpoint(request: Request, text: str = Form(...)):
    """Hermes-compatible TTS endpoint: POST text and speak it immediately."""
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    asyncio.create_task(asyncio.to_thread(_speak, text[:500]))
    return JSONResponse({"spoken": True})


# Apply security dependency to all routes.
app.router.dependencies.append(Depends(_secure))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="warning")
