"""Shared theming for the AI Controller UI overlays.

Ported 1:1 from Pupil IDE's martial-arts belt system (the Master AI stack's
canonical 7 themes, /pupil.html) so the desktop app and the web GUI share one
palette. Each belt is a full bg/surface/text/accent set, switchable by writing
the belt name to ~/.config/ai-controller/theme. Default = black-belt (Pupil's
default).
"""

from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "ai-controller"
THEME_FILE = CONFIG_DIR / "theme"


@dataclass(frozen=True)
class Theme:
    name: str
    background: str      # Pupil --bg
    surface: str         # Pupil --surface
    text: str            # Pupil --text
    text_secondary: str  # Pupil --text3
    accent: str          # Pupil --accent
    pin: str             # Pupil --ok (success green)
    border: str          # Pupil --line


WHITE_BELT = Theme("white-belt", "#f7f8fa", "#f1f3f6", "#15171c", "#5d6572",
                   "#5b6472", "#16a34a", "rgba(21,23,28,0.14)")
YELLOW_BELT = Theme("yellow-belt", "#131208", "#262213", "#faf7e8", "#a89f7d",
                    "#eab308", "#a3e635", "rgba(250,247,232,0.10)")
BLUE_BELT = Theme("blue-belt", "#090f19", "#18243a", "#eaf1fb", "#8ea3c0",
                  "#3b82f6", "#4ade80", "rgba(234,241,251,0.10)")
GREEN_BELT = Theme("green-belt", "#081209", "#152518", "#ecf7ee", "#93b39b",
                   "#22c55e", "#34d399", "rgba(236,247,238,0.10)")
PURPLE_BELT = Theme("purple-belt", "#0f0a17", "#221738", "#f3ecfd", "#a491c4",
                    "#a855f7", "#4ade80", "rgba(243,236,253,0.10)")
BROWN_BELT = Theme("brown-belt", "#14100b", "#2a2219", "#f0e7d8", "#a99880",
                   "#b45309", "#84cc16", "rgba(240,231,216,0.10)")
BLACK_BELT = Theme("black-belt", "#000000", "#141416", "#fafafa", "#8e8e96",
                   "#ff3b30", "#32d74b", "rgba(255,255,255,0.10)")

THEMES = {t.name: t for t in (
    WHITE_BELT, YELLOW_BELT, BLUE_BELT, GREEN_BELT,
    PURPLE_BELT, BROWN_BELT, BLACK_BELT,
)}

DEFAULT = BLACK_BELT


def load_theme() -> Theme:
    try:
        name = THEME_FILE.read_text().strip().lower()
    except OSError:
        return DEFAULT
    return THEMES.get(name, DEFAULT)


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


def hex_to_rgb(hex_color: str):
    """Return an (r, g, b) float tuple (0.0–1.0) from a '#RRGGBB' hex string.

    Used by controller-legend.py to convert theme colors into cairo
    set_source_rgba() arguments.
    """
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
