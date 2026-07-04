#!/usr/bin/env python3
"""
conversation_speaker.py — perpetuate a conversation thread through the speakers.

Keeps speech from flooding the room:
- skips commands, code blocks, and overly long messages
- rate-limits announcements
- deduplicates repeats within a window

Modes:
    say "text"              Speak one message immediately
    tail <file>             Watch a log file and speak new lines
    telegram                Poll a Telegram bot for new messages
    loop                    Repeat specific messages for a set duration
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import voice_toggle
from ai_controller_paths import config_dir

CONFIG_FILE = os.path.join(config_dir(), "config.env")
STATE_FILE = os.path.join(config_dir(), "conversation_speaker_state.json")

# Tunable defaults; overridden by config.env
DEFAULTS = {
    "SPEAK_MAX_CHARS": "300",
    "SPEAK_RATE_PER_MIN": "6",
    "SPEAK_DEDUP_SECS": "300",
    "TELEGRAM_POLL_SECS": "3",
    "TELEGRAM_ALLOWED_CHATS": "",  # comma-separated, empty = allow all
}


class Speaker:
    def __init__(self):
        self.cfg = self._load_config()
        self.max_chars = int(self.cfg.get("SPEAK_MAX_CHARS", DEFAULTS["SPEAK_MAX_CHARS"]))
        self.rate_per_min = int(self.cfg.get("SPEAK_RATE_PER_MIN", DEFAULTS["SPEAK_RATE_PER_MIN"]))
        self.dedup_secs = int(self.cfg.get("SPEAK_DEDUP_SECS", DEFAULTS["SPEAK_DEDUP_SECS"]))
        self.telegram_poll_secs = float(self.cfg.get("TELEGRAM_POLL_SECS", DEFAULTS["TELEGRAM_POLL_SECS"]))
        self.allowed_chats = {
            int(x.strip())
            for x in self.cfg.get("TELEGRAM_ALLOWED_CHATS", DEFAULTS["TELEGRAM_ALLOWED_CHATS"]).split(",")
            if x.strip()
        }
        self._recent = deque()  # (timestamp, text_hash)

    def _load_config(self):
        cfg = dict(DEFAULTS)
        if not os.path.isfile(CONFIG_FILE):
            return cfg
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    cfg[key.strip()] = val.strip().strip('"').strip("'")
        except Exception as exc:
            print(f"[warn] could not read config: {exc}", file=sys.stderr)
        return cfg

    def _clean(self, text: str) -> str:
        # strip code blocks
        text = re.sub(r"```[\s\S]*?```", " code snippet ", text)
        text = re.sub(r"`[^`]+`", " code ", text)
        # collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _already_said(self, text: str) -> bool:
        now = time.time()
        h = hash(text.lower())
        cutoff = now - self.dedup_secs
        while self._recent and self._recent[0][0] < cutoff:
            self._recent.popleft()
        if any(item[1] == h for item in self._recent):
            return True
        self._recent.append((now, h))
        return False

    def _rate_ok(self) -> bool:
        now = time.time()
        window = 60.0
        cutoff = now - window
        while self._recent and self._recent[0][0] < cutoff:
            self._recent.popleft()
        return sum(1 for t, _ in self._recent if t >= cutoff) < self.rate_per_min

    def should_speak(self, text: str, sender: str = "") -> bool:
        text = self._clean(text)
        if not text:
            return False
        if text.startswith("/"):
            return False
        if len(text) > self.max_chars:
            return False
        if self._already_said(text):
            return False
        if not self._rate_ok():
            print(f"[rate limit] skipping: {text[:60]}...")
            return False
        return True

    def speak(self, text: str):
        text = self._clean(text)
        if not self.should_speak(text):
            return
        print(f"[speak] {text}")
        try:
            voice_toggle.speak(text)
        except Exception as exc:
            print(f"[tts error] {exc}", file=sys.stderr)


def cmd_say(args):
    speaker = Speaker()
    speaker.speak(args.text)


def cmd_loop(args):
    speaker = Speaker()
    # Loops are intentional, so allow a higher default speech rate.
    speaker.rate_per_min = args.rate_per_min

    messages = list(args.message or [])
    if args.messages_file:
        try:
            with open(args.messages_file, "r", encoding="utf-8") as f:
                messages.extend(line.strip() for line in f if line.strip())
        except Exception as exc:
            print(f"[error] could not read messages file: {exc}", file=sys.stderr)
            sys.exit(1)
    if not messages:
        print("[error] no messages provided; use --message or --messages-file", file=sys.stderr)
        sys.exit(1)

    end = time.time() + args.duration
    idx = 0
    fixed = args.interval is not None
    if fixed:
        print(f"[loop] running for {args.duration}s, fixed interval {args.interval}s, {len(messages)} message(s)")
    else:
        print(f"[loop] running for {args.duration}s, random gaps {args.min_interval}s-{args.max_interval}s, {len(messages)} message(s)")

    while time.time() < end:
        if args.shuffle:
            random.shuffle(messages)
            idx = 0
        msg = messages[idx % len(messages)]
        speaker.speak(msg)
        idx += 1

        if fixed:
            delay = args.interval
        else:
            delay = random.randint(args.min_interval, args.max_interval)

        remaining = delay
        while remaining > 0 and time.time() < end:
            step = min(remaining, 1.0)
            time.sleep(step)
            remaining -= step
    speaker.speak("Loop finished.")


def cmd_tail(args):
    speaker = Speaker()
    path = os.path.abspath(args.file)
    print(f"[tail] watching {path}")
    inode = None
    pos = 0
    while True:
        try:
            st = os.stat(path)
            if inode != st.st_ino:
                inode = st.st_ino
                pos = 0
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                for line in f:
                    line = line.strip()
                    if line:
                        speaker.speak(line)
                pos = f.tell()
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"[tail error] {exc}", file=sys.stderr)
        time.sleep(1)


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_update_id": 0}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def cmd_telegram(args):
    speaker = Speaker()
    token = speaker.cfg.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("[error] TELEGRAM_BOT_TOKEN not set in config.env", file=sys.stderr)
        sys.exit(1)

    allowed = speaker.allowed_chats
    state = _load_state()
    offset = state.get("last_update_id", 0) + 1
    print("[telegram] polling for messages...")

    while True:
        url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&limit=10"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            print(f"[telegram] connection error: {exc}", file=sys.stderr)
            time.sleep(speaker.telegram_poll_secs)
            continue
        except Exception as exc:
            print(f"[telegram] error: {exc}", file=sys.stderr)
            time.sleep(speaker.telegram_poll_secs)
            continue

        if not data.get("ok"):
            print(f"[telegram] API error: {data}", file=sys.stderr)
            time.sleep(speaker.telegram_poll_secs)
            continue

        for upd in data.get("result", []):
            offset = max(offset, upd.get("update_id", 0) + 1)
            msg = upd.get("message") or upd.get("channel_post") or upd.get("edited_message")
            if not msg:
                continue
            text = msg.get("text") or msg.get("caption", "")
            if not text:
                continue
            chat = msg.get("chat", {})
            chat_id = chat.get("id")
            sender = msg.get("from", {}).get("username") or msg.get("from", {}).get("first_name") or ""
            if allowed and chat_id not in allowed:
                continue
            who = f"{sender}: " if sender else ""
            final = f"{who}{text}"
            speaker.speak(final)

        state["last_update_id"] = offset - 1
        _save_state(state)
        time.sleep(speaker.telegram_poll_secs)


def main():
    parser = argparse.ArgumentParser(description="Perpetuate conversation through speakers")
    sub = parser.add_subparsers(dest="command")

    p_say = sub.add_parser("say", help="Speak one message")
    p_say.add_argument("text", help="Text to speak")

    p_tail = sub.add_parser("tail", help="Tail a log file and speak new lines")
    p_tail.add_argument("file", help="Log file to watch")

    p_tg = sub.add_parser("telegram", help="Poll Telegram and speak messages")

    p_loop = sub.add_parser("loop", help="Repeat specific messages for a set duration")
    p_loop.add_argument("--duration", type=int, default=7200, help="Total seconds to run (default 7200 = 2 hours)")
    p_loop.add_argument("--interval", type=int, default=None, help="Fixed seconds between messages (overrides random gaps)")
    p_loop.add_argument("--min-interval", type=int, default=10, help="Minimum random gap in seconds (default 10)")
    p_loop.add_argument("--max-interval", type=int, default=60, help="Maximum random gap in seconds (default 60)")
    p_loop.add_argument("--shuffle", action="store_true", help="Shuffle message order each cycle")
    p_loop.add_argument("--rate-per-min", type=int, default=120, help="Speech rate limit for this loop (default 120)")
    p_loop.add_argument("--message", action="append", help="Message to speak; repeatable")
    p_loop.add_argument("--messages-file", help="File with one message per line")

    args = parser.parse_args()
    if args.command == "say":
        cmd_say(args)
    elif args.command == "tail":
        cmd_tail(args)
    elif args.command == "telegram":
        cmd_telegram(args)
    elif args.command == "loop":
        cmd_loop(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
