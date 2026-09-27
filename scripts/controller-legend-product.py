#!/usr/bin/env python3
"""Controller Legend — Product-ready static image for marketing.

Displays an Xbox-style controller diagram with each button labeled by its
CONVENTIONAL NAME (A/B/X/Y, LB/RB, LT/RT, Menu/View, L3/R3). A note indicates
this works for any standard controller, not just Xbox-branded hardware.

Designed for product screenshots, documentation, and demo materials.
"""
import os
import sys
import xml.etree.ElementTree as ET

# Profile path (locked/active profile from AntiMicroX)
LOCKED_PROFILE_PATH = os.path.join(
    os.path.expanduser("~/.config/antimicrox"), 
    "dont delete .gamecontroller.amgp"
)

# CONVENTIONAL BUTTON LABELS - these are the button names, not their key bindings
# The live profile mapping is used for the subtitle/footnote, but button labels
# always show the physical button name for a teaching/reference diagram
_BUTTON_LABELS = {
    'A': 'A',
    'B': 'B', 
    'X': 'X',
    'Y': 'Y',
    'LB': 'LB',
    'RB': 'RB',
    'LT': 'LT',
    'RT': 'RT',
    '⧉': 'View/Back',  # Physical button name, not key binding
    '☰': 'Menu',      # Physical button name, not key binding
    'LS': 'L3',       # Left stick click
    'RS': 'R3',       # Right stick click
}

# Fallback layout - just button names with minimal key info for footnote
_DESKTOP_FALLBACK = [
    ("A",    "A"),
    ("B",    "B"),
    ("X",    "X"),
    ("Y",    "Y"),
    ("LB",   "LB"),
    ("RB",   "RB"),
    ("LT",   "LT"),
    ("RT",   "RT"),
    ("⧉", "View/Back"),
    ("☰", "Menu"),
    ("LS", "L3"),
    ("RS", "R3"),
]

# Button index positions (SDL controller standard)
LEGEND_SLOT_ORDER = [
    ("A", ("button", 1)), ("B", ("button", 2)),
    ("X", ("button", 3)), ("Y", ("button", 4)),
    ("LB", ("button", 10)), ("RB", ("button", 11)),
    ("LT", ("trigger", 5)), ("RT", ("trigger", 6)),
    ("⧉", ("button", 5)),   # Back / View
    ("☰", ("button", 7)),   # Start
    ("LS", ("stick", 1)),   # left-stick click
    ("RS", ("stick", 2)),   # right-stick click
]


def _first_label(container_el):
    """Extract human-readable label from a button XML element's first slot.
    
    Note: This returns the KEY BINDING (what the button sends), which we then
    DISCARD for display purposes. We want the physical button name.
    """
    if container_el is None:
        return None
    for slot in container_el.findall("./slots/slot"):
        mode = (slot.findtext("mode") or "").strip()
        if mode == "keyboard":
            # Return the key code, but we'll convert to button name elsewhere
            code = (slot.findtext("code") or "").strip()
            return code
        if mode == "mousebutton":
            code = (slot.findtext("code") or "").strip()
            return f"mouse:{code}"
        if mode == "execute":
            name = os.path.basename((slot.findtext("path") or "").strip())
            return f"script:{name}"
    return None


# Qt key codes this profile actually emits -> short display label. Copied
# from controller-legend.py's _KEY_LABELS, which was verified against
# AntiMicroX's own source (eventhandlers/uinputeventhandler.cpp) on
# 2026-09-07 -- don't edit without re-checking that file. Duplicated here
# rather than imported since controller-legend.py's hyphenated filename
# isn't importable as a plain module.
_KEY_LABELS = {
    "0x1000000": "Esc", "0x1000001": "Tab", "0x1000003": "Bksp",
    "0x1000007": "Del", "0x1000004": "Enter", "0x1000020": "Shift",
    "0x1000021": "Ctrl", "0x100003c": "Talk",
    "0x61000028": "Enter",
}


def get_key_binding(button_name: str, live_layout: dict) -> str | None:
    """Real current key binding for a button, read from the live AntiMicroX
    profile (live_layout, as returned by get_current_layout()) -- NOT a
    hardcoded table. LEGEND_SLOT_ORDER maps each physical button name to
    its (kind, index) in that profile; kind is "button"/"trigger"/"stick",
    matching the top-level keys get_current_layout() returns."""
    pos = dict(LEGEND_SLOT_ORDER).get(button_name)
    if pos is None:
        return None
    kind, index = pos
    label = live_layout.get(kind, {}).get(index)
    if not label:
        return None
    if label.startswith("mouse:") or label.startswith("script:"):
        return label  # already a friendly descriptor, e.g. "script:foo.sh"
    return _KEY_LABELS.get(label, label)  # unrecognized code -> show raw hex


def load_desktop_layout(path=LOCKED_PROFILE_PATH):
    """Parse the AntiMicroX profile and return button info.
    
    Returns a dict mapping button position names to their key bindings.
    """
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return {}
    
    set_el = root.find("./sets/set")
    if set_el is None:
        return {}

    buttons = {int(b.get("index", "0")): _first_label(b) 
               for b in set_el.findall("button")}
    triggers = {}
    for trig in set_el.findall("trigger"):
        idx = int(trig.get("index", "0"))
        triggers[idx] = _first_label(trig.find("triggerbutton"))
    sticks = {}
    for stick in set_el.findall("stick"):
        idx = int(stick.get("index", "0"))
        sticks[idx] = _first_label(stick.find("stickbutton[@index='2']"))

    return {"button": buttons, "trigger": triggers, "stick": sticks}


def get_current_layout() -> dict:
    """Load the live layout from the profile."""
    return load_desktop_layout() or {}


def render_svg(width=520, height=380):
    """Render the controller legend as an SVG string.
    
    Uses an Xbox-style controller outline with buttons labeled by their
    conventional physical names. Works for any standard controller layout.
    """
    # Load live key bindings for the footnote -- button labels themselves
    # always stay conventional names (_BUTTON_LABELS), but the footnote
    # calls out the two buttons whose physical name and current sent key
    # actually differ, read live so it can't drift from the real profile.
    live_layout = get_current_layout()

    # SVG template parts
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">')
    parts.append('  <style>')
    parts.append('    body { font-family: "Segoe UI", "Inter", sans-serif; }')
    parts.append('    .btn { fill: #f0f0f0; stroke: #444444; stroke-width: 2; }')
    parts.append('    .btn-active { fill: #ff6a00; stroke: #e05c00; stroke-width: 2; }')
    parts.append('    .btn-label { font-size: 10px; font-weight: 500; fill: #222222; text-anchor: middle; dominant-baseline: middle; pointer-events: none; }')
    parts.append('    .title { font-size: 14px; font-weight: 600; fill: #333333; }')
    parts.append('    .subtitle { font-size: 9px; fill: #666666; }')
    parts.append('    .footnote { font-size: 8px; fill: #888888; text-anchor: middle; }')
    parts.append('  </style>')
    parts.append(f'  <rect width="{width}" height="{height}" fill="#fafafa"/>')
    
    # Title bar
    parts.append(f'  <text x="{width/2}" y="20" class="title" text-anchor="middle">Controller Legend</text>')
    
    # Xbox-style controller body: one continuous silhouette (wide top lobe
    # tapering into two rounded grip horns) instead of a separate body path
    # + two disconnected grip shapes, which is what made the first version
    # look like unrelated blobs stacked together rather than one controller.
    parts.append('  <!-- Xbox-style controller body -->')
    parts.append(
        '  <path d="M 130,90 '
        'C 90,90 60,110 55,150 '
        'C 50,190 55,230 75,260 '
        'C 90,282 110,290 125,275 '
        'C 138,262 140,240 150,225 '
        'C 165,205 190,195 220,192 '
        'L 300,192 '
        'C 330,195 355,205 370,225 '
        'C 380,240 382,262 395,275 '
        'C 410,290 430,282 445,260 '
        'C 465,230 470,190 465,150 '
        'C 460,110 430,90 390,90 '
        'C 360,80 340,85 320,95 '
        'C 300,88 220,88 200,95 '
        'C 180,85 160,80 130,90 Z" '
        'fill="#e2e2e2" stroke="#9a9a9a" stroke-width="2"/>'
    )

    # D-pad (lower-left of the face, cross drawn directly -- no bounding box)
    parts.append('  <!-- D-pad -->')
    dpad_x, dpad_y, arm = 150, 255, 16
    parts.append(
        f'  <rect x="{dpad_x - 6}" y="{dpad_y - arm}" width="12" height="{arm * 2}" rx="3" fill="#f5f5f5" stroke="#888888"/>'
    )
    parts.append(
        f'  <rect x="{dpad_x - arm}" y="{dpad_y - 6}" width="{arm * 2}" height="12" rx="3" fill="#f5f5f5" stroke="#888888"/>'
    )

    # Button positions (x, y, size) -- upper-left stick, lower-left d-pad,
    # upper-right ABXY diamond, lower-right stick: standard controller
    # face layout, not a random scatter.
    positions = {
        'LS': (150, 150, 20),   # left stick
        'RS': (370, 255, 20),   # right stick
        'Y': (370, 122, 15),
        'X': (343, 150, 15),
        'B': (397, 150, 15),
        'A': (370, 178, 15),
        '⧉': (225, 100, 20),   # View/Back
        '☰': (295, 100, 14),   # Menu
        'LB': (150, 68, 30, 14),
        'RB': (370, 68, 30, 14),
        'LT': (150, 40, 26, 10),
        'RT': (370, 40, 26, 10),
    }

    for pos_name, spec in positions.items():
        label = _BUTTON_LABELS.get(pos_name, pos_name)
        font_style = ' style="font-size:7px"' if len(label) > 5 else ""
        if len(spec) == 3:
            x, y, r = spec
            parts.append(f'  <circle cx="{x}" cy="{y}" r="{r}" class="btn btn-active"/>')
            parts.append(f'  <text x="{x}" y="{y}" class="btn-label"{font_style}>{label}</text>')
        else:
            x, y, w, h = spec
            parts.append(
                f'  <rect x="{x - w / 2}" y="{y - h / 2}" width="{w}" height="{h}" '
                f'rx="{h / 2}" class="btn btn-active"/>'
            )
            parts.append(f'  <text x="{x}" y="{y}" class="btn-label"{font_style}>{label}</text>')
    parts.append(f'  <text x="150" y="{255 + 34}" class="subtitle" text-anchor="middle">D-Pad</text>')
    
    # Footnote - explain that button names are conventional, not key bindings.
    # View/Back and Menu are the two buttons whose physical name and current
    # sent key actually diverge (e.g. Menu currently sends Tab) -- read live
    # so this can't silently drift from the real profile.
    view_bind = get_key_binding('⧉', live_layout)
    menu_bind = get_key_binding('☰', live_layout)
    bind_notes = [
        f"{name} currently sends {bind}"
        for name, bind in (("View/Back", view_bind), ("Menu", menu_bind))
        if bind
    ]

    parts.append('  <!-- Footnote - shown for marketing context -->')
    parts.append(f'  <text x="{width/2}" y="{height-24}" class="footnote">')
    parts.append('    Shown on an Xbox-style layout — works the same on any standard controller')
    parts.append('  </text>')
    if bind_notes:
        parts.append(f'  <text x="{width/2}" y="{height-13}" class="footnote">')
        parts.append(f'    {" · ".join(bind_notes)}')
        parts.append('  </text>')

    parts.append('</svg>')
    return '\n'.join(parts)


def main():
    svg = render_svg()
    
    # Output: SVG by default, PNG if --png flag given
    if len(sys.argv) >= 3 and sys.argv[1] == '--png':
        png_path = sys.argv[2]
        try:
            import cairosvg
            cairosvg.svg2png(bytestring=svg.encode('utf-8'), write_to=png_path)
            print(f"Wrote PNG to {png_path}")
        except ImportError:
            print("cairosvg not available, outputting SVG instead...", file=sys.stderr)
            print(svg)
        except Exception as e:
            print(f"Failed to convert SVG to PNG: {e}", file=sys.stderr)
            print(svg)
    elif len(sys.argv) >= 2:
        with open(sys.argv[1], 'w') as f:
            f.write(svg)
        print(f"Wrote SVG to {sys.argv[1]}")
    else:
        print(svg)


if __name__ == "__main__":
    main()