#!/usr/bin/env python3
"""
Pure Unicode font-mapping helpers, standalone and side-effect free.

Deliberately duplicated from ptt_pynput.py's _CURSIVE_MAP/_BOLD_MAP/
_FRAKTUR_MAP rather than imported from there: ptt_pynput.py has no
__main__ guard and ends in a blocking keyboard.Listener at module scope,
plus grabs a singleton lock file and starts an evdev thread on import --
importing it into the GUI process would fight the already-running
dictation daemon for the same input devices. This file exists so
slide_keyboard.py (or anything else that just wants to render text in
one of these styles) can do so without touching that live, locked F13/
STT path. If the maps in ptt_pynput.py ever change, update both.
"""

_CURSIVE_LOWER = "𝓪𝓫𝓬𝓭𝓮𝓯𝓰𝓱𝓲𝓳𝓴𝓵𝓶𝓷𝓸𝓹𝓺𝓻𝓼𝓽𝓾𝓿𝔀𝔁𝔂𝔃"
_CURSIVE_UPPER = "𝓐𝓑𝓒𝓓𝓔𝓕𝓖𝓗𝓘𝓙𝓚𝓛𝓜𝓝𝓞𝓟𝓠𝓡𝓢𝓣𝓤𝓥𝓦𝓧𝓨𝓩"
_CURSIVE_MAP = {
    **{chr(0x61 + i): _CURSIVE_LOWER[i] for i in range(26)},
    **{chr(0x41 + i): _CURSIVE_UPPER[i] for i in range(26)},
}

_BOLD_LOWER = "𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"
_BOLD_UPPER = "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙"
_BOLD_MAP = {
    **{chr(0x61 + i): _BOLD_LOWER[i] for i in range(26)},
    **{chr(0x41 + i): _BOLD_UPPER[i] for i in range(26)},
}

# Old English (blackletter) -- Unicode MATHEMATICAL BOLD FRAKTUR block
# (U+1D56C). Bold Fraktur specifically, not plain Fraktur: plain Fraktur
# (U+1D504) has legacy gaps (missing C/H/I/R/Z, aliased to pre-existing
# Letterlike Symbols). Bold Fraktur is a contiguous, fully populated
# 52-codepoint block with no aliasing.
_FRAKTUR_LOWER = "𝖆𝖇𝖈𝖉𝖊𝖋𝖌𝖍𝖎𝖏𝖐𝖑𝖒𝖓𝖔𝖕𝖖𝖗𝖘𝖙𝖚𝖛𝖜𝖝𝖞𝖟"
_FRAKTUR_UPPER = "𝕬𝕭𝕮𝕯𝕰𝕱𝕲𝕳𝕴𝕵𝕶𝕷𝕸𝕹𝕺𝕻𝕼𝕽𝕾𝕿𝖀𝖁𝖂𝖃𝖄𝖅"
_FRAKTUR_MAP = {
    **{chr(0x61 + i): _FRAKTUR_LOWER[i] for i in range(26)},
    **{chr(0x41 + i): _FRAKTUR_UPPER[i] for i in range(26)},
}


def to_cursive(text: str) -> str:
    """Map ASCII letters to cursive script Unicode (Mathematical Bold Script)."""
    return "".join(_CURSIVE_MAP.get(ch, ch) for ch in text)


def to_bold(text: str) -> str:
    """Map ASCII letters to bold mathematical Unicode."""
    return "".join(_BOLD_MAP.get(ch, ch) for ch in text)


def to_old_english(text: str) -> str:
    """Map ASCII letters to Old English (bold Fraktur) Unicode."""
    return "".join(_FRAKTUR_MAP.get(ch, ch) for ch in text)
