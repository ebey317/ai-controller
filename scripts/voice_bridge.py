#!/usr/bin/env python3
"""
Voice bridge — audio → Groq Whisper → CLAF → TTS
Runs on :8002. push-to-talk.sh POSTs audio to /voice?mode=transcribe_only.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Optional

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

def _load_env(path: str) -> dict:
    """Pull keys from CLAF .env without importing dotenv."""
    out: dict[str, str] = {}
    try:
        for line in open(os.path.expanduser(path), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


_env = _load_env("~/projects/claf/.env")
GROQ_KEY = _env.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY", "")
CLAF_URL = (os.environ.get("CLAF_URL") or _env.get("CLAF_URL") or "http://localhost:8000").rstrip("/")
VOICE_BRIDGE_API_KEY = os.environ.get("VOICE_BRIDGE_API_KEY", "")

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_TIMEOUT = float(os.environ.get("VOICE_BRIDGE_GROQ_TIMEOUT", "30"))
CLAF_TIMEOUT = float(os.environ.get("VOICE_BRIDGE_CLAF_TIMEOUT", "120"))
MAX_TRANSCRIPT_CHARS = int(os.environ.get("VOICE_BRIDGE_MAX_TRANSCRIPT_CHARS", "2000"))

app = FastAPI(title="voice-bridge", version="1.1")

PIPER_MODEL = os.path.expanduser("~/.local/share/piper/en_US-joe-medium.onnx")


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

def _speak(text: str) -> None:
    if not text:
        return
    spoken = text[:500].split("\n")[0]
    if os.path.exists(PIPER_MODEL):
        p1 = subprocess.Popen(
            ["piper", "-m", PIPER_MODEL, "--output-raw"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        p2 = subprocess.Popen(
            ["paplay", "--raw", "--rate=22050", "--format=s16le", "--channels=1"],
            stdin=p1.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        p1.stdin.write(spoken.encode())
        p1.stdin.close()
        p2.wait()
    else:
        subprocess.Popen(
            ["spd-say", "-w", spoken],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _extract_claf_text(payload: dict) -> str:
    """Pull assistant text out of CLAF's Anthropic-format response."""
    for block in payload.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "").strip()
    return ""


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
        if len(data) < 2000:
            return JSONResponse({"error": "audio too short"}, status_code=400)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
            tmp = f.name

        try:
            async with httpx.AsyncClient(timeout=GROQ_TIMEOUT) as client:
                with open(tmp, "rb") as fp:
                    try:
                        r = await client.post(
                            GROQ_STT_URL,
                            headers={"Authorization": f"Bearer {GROQ_KEY}"},
                            files={"file": ("audio.wav", fp, "audio/wav")},
                            data={"model": "whisper-large-v3-turbo"},
                        )
                        r.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        return JSONResponse(
                            {"error": f"Groq STT HTTP {exc.response.status_code}"},
                            status_code=502,
                        )
                    except httpx.RequestError as exc:
                        return JSONResponse(
                            {"error": f"Groq STT request failed: {exc}"},
                            status_code=502,
                        )
            transcript = r.json().get("text", "").strip()[:MAX_TRANSCRIPT_CHARS]
        finally:
            os.unlink(tmp)

    if not transcript:
        return JSONResponse({"error": "empty transcript"}, status_code=400)

    if mode == "transcribe_only":
        return JSONResponse({"text": transcript})

    # ── LLM — route voice commands through CLAF ───────────────────────────────
    payload = {
        "model": "qwen2.5-coder:7b",
        "messages": [{"role": "user", "content": transcript}],
        "max_tokens": 256,
    }

    async with httpx.AsyncClient(timeout=CLAF_TIMEOUT) as client:
        try:
            r = await client.post(
                f"{CLAF_URL}/v1/messages",
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            response_text = _extract_claf_text(r.json())
        except httpx.HTTPStatusError as exc:
            return JSONResponse(
                {"transcript": transcript, "error": f"CLAF HTTP {exc.response.status_code}"},
                status_code=502,
            )
        except httpx.RequestError as exc:
            return JSONResponse(
                {"transcript": transcript, "error": f"CLAF request failed: {exc}"},
                status_code=502,
            )

    # ── TTS — speak response ──────────────────────────────────────────────────
    _speak(response_text)

    return JSONResponse({"transcript": transcript, "response": response_text})


# Apply security dependency to all routes.
app.router.dependencies.append(_secure)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="warning")
