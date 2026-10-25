"""Shared theming for the AI Controller UI overlays.

Centralizes the hardcoded palette (dark background + HUD-orange accent, formerly
scattered across controller-legend.py / slide_keyboard.py /
ai-controller-launcher.py) into named themes, so a single config value switches
the whole desktop UI. Mirrors the Android app's AppTheme, but for the Python
GTK overlays. Selected theme persists at ~/.config/ai-controller/theme.
"""

from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "ai-controller"
THEME_FILE = CONFIG_DIR / "theme"


@dataclass(frozen=True)
class Theme:
    name: str
    background: str  # window background (rgba())
    surface: str     # keys / surfaces
    text: str        # primary text
    text_secondary: str
    accent: str      # HUD accent (was #FF6A00)
    pin: str         # pin-snippet green (was #3ddc97)
    border: str


SIGNATURE = Theme(
    name="signature",
    background="rgba(13,13,18,0.94)",
    surface="#23232b",
    text="#e8e8e8",
    text_secondary="#8a8a92",
    accent="#FF6A00",
    pin="#3ddc97",
    border="#3a3a44",
)

LIGHT = Theme(
    name="light",
    background="rgba(250,250,250,0.96)",
    surface="#ffffff",
    text="#1a1a1a",
    text_secondary="#5a5a5a",
    accent="#FF6A00",
    pin="#00a06a",
    border="#e0e0e0",
)

HIGH_CONTRAST = Theme(
    name="high-contrast",
    background="rgba(0,0,0,0.98)",
    surface="#000000",
    text="#ffff00",
    text_secondary="#ffffff",
    accent="#ffff00",
    pin="#ffff00",
    border="#ffff00",
)

GREEN_ON_BLACK = Theme(  # Master AI brand signature
    name="green-on-black",
    background="rgba(0,0,0,0.96)",
    surface="#0f1f0f",
    text="#e8e8e8",
    text_secondary="#6a8a6a",
    accent="#00E5A0",
    pin="#00E5A0",
    border="#1f3a1f",
)

THEMES = {t.name: t for t in (SIGNATURE, LIGHT, HIGH_CONTRAST, GREEN_ON_BLACK)}


def load_theme() -> Theme:
    try:
        name = THEME_FILE.read_text().strip().lower()
    except OSError:
        return SIGNATURE
    return THEMES.get(name, SIGNATURE)


def save_theme(name: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    THEME_FILE.write_text(name.strip().lower() + "\n")


def css(theme: Theme) -> str:
    """CSS block shared by the GTK overlays (slide keyboard / launcher)."""
    return (
        "window { background-color: %s; border: 2px solid %s; }\n"
        "button { background-color: %s; color: %s; border: 1px solid %s; }\n"
        "button:hover { background-color: %s; }\n"
        "button.special { background-color: %s; color: %s; border-color: %s; }\n"
        "button.mode { background-color: %s; color: %s; border-color: %s; "
        "font-weight: bold; padding: 1px 7px; }\n"
        "button.mode-active { background-color: %s; color: #0d0d12; "
        "border-color: %s; font-weight: bold; padding: 1px 7px; }\n"
        "button.pin { background-color: %s; color: %s; border-color: %s; }\n"
        ".shelf-title { color: %s; font-weight: bold; font-size: 9px; "
        "margin-bottom: 3px; }\n"
    ) % (
        theme.background, theme.accent,
        theme.surface, theme.text, theme.border,
        theme.border,
        theme.surface, theme.accent, theme.accent,
        theme.surface, theme.accent, theme.accent,
        theme.accent, theme.accent,
        theme.surface, theme.pin, theme.border,
        theme.accent,
    )
