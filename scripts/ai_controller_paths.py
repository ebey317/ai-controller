"""Shared path/config helpers for AI Controller scripts.

Every script uses these helpers so the install can live anywhere under $HOME
and never hardcode the original developer's home directory.
"""
import os
from pathlib import Path


def ai_controller_dir() -> str:
    """Return the AI Controller install root.

    Priority:
      1. $AI_CONTROLLER_DIR environment variable
      2. Directory containing this file (../scripts -> root)
      3. $HOME/ai-controller
    """
    env = os.environ.get("AI_CONTROLLER_DIR")
    if env:
        return os.path.abspath(env)
    # __file__ is in <root>/scripts/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_dir() -> str:
    """Return the per-user configuration directory."""
    return os.path.expanduser("~/.config/ai-controller")


def config_file() -> str:
    """Return the path to config.env."""
    return os.path.join(config_dir(), "config.env")


def load_env(path: str | None = None) -> dict[str, str]:
    """Read KEY=VALUE lines from a file, ignoring comments and blanks."""
    path = path or config_file()
    out: dict[str, str] = {}
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"\'')
    except FileNotFoundError:
        pass
    return out


def ensure_config_dir() -> None:
    os.makedirs(config_dir(), exist_ok=True)
