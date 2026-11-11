#!/usr/bin/env python3
"""make_tahuti_readme.py — build the Tahuti / Sensei README from the live system.

The command list comes from Sensei's own registry and the controller map from
the real AntiMicroX profile (via the legend's own tables), so the README cannot
drift the way hand-typed help did. Writes:

    ~/ai-controller/docs/TAHUTI_README.md   (kept in the repo)
    ~/.tahuti/README.html                   (what "Open README" shows)

Run:  python3 make_tahuti_readme.py            # regenerate both
      python3 make_tahuti_readme.py --print    # markdown to stdout
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sensei_slash as sb  # noqa: E402

MD_OUT = HERE.parent / "docs" / "TAHUTI_README.md"
HTML_OUT = Path.home() / ".tahuti" / "README.html"
LEGEND = HERE / "controller-legend.py"


# --------------------------------------------------------------------------
# controller map: read the legend's own tables WITHOUT importing it (importing
# takes its single-instance lock and exits while the legend is running)
# --------------------------------------------------------------------------
def controller_layouts() -> dict[str, list[tuple[str, str]]]:
    try:
        src = LEGEND.read_text(encoding="utf-8")
        head = src[: src.index("LEGEND_BOX_SCALE")]
        a = head.index("# Singleton:")
        b = head.index("# Make shared helpers available")
        head = head[:a] + head[b:]
        ns: dict = {"__file__": str(LEGEND), "__name__": "legend_tables"}
        sys.path.insert(0, str(HERE))
        exec(compile(head, str(LEGEND), "exec"), ns)  # noqa: S102 - our own file
        return {k: list(v) for k, v in ns["ALL_LAYOUTS"].items()}
    except Exception as exc:  # the README must still build without the legend
        print(f"(controller map unavailable: {exc})", file=sys.stderr)
        return {}


# --------------------------------------------------------------------------
# content as blocks, so Markdown and HTML come from one source
# --------------------------------------------------------------------------
Block = tuple


def build_blocks() -> list[Block]:
    rows = sorted(sb.load_commands(), key=lambda r: r[0].lower())
    sections: dict[str, list[tuple]] = {}
    for r in rows:
        sections.setdefault(r[3], []).append(r)
    titles = sb._SECTION_TITLE
    order = [s for s in sb._SECTION_ORDER if s in sections] + [
        s for s in sections if s not in sb._SECTION_ORDER
    ]

    menus = []
    for base in sorted({sb._base(r[0]).split()[0].lower() for r in rows}):
        m = sb.menu_for(base)
        if m:
            menus.append(f"slash {base}")

    B: list[Block] = []
    B.append(("h1", "Tahuti & Sensei — how to use them"))
    B.append(
        (
            "p",
            "**Tahuti** is the voice you talk to. **Sensei** is the AI that works in your terminal. "
            "The avatar on your screen shows what Tahuti is doing and gives you cards to read and click.",
        )
    )

    B.append(("h2", "1. Talk to it"))
    B.append(
        (
            "ul",
            [
                "Hold the trigger (**L + R** on the controller, or **F13** on a keyboard) and speak. Let go when you are done.",
                "The avatar appears while you talk, stays while it works, and goes away when it has finished speaking.",
                'Ask anything: "What is two plus two?", "Open YouTube", "Give me the newest AI tools updates on a web page."',
            ],
        )
    )

    B.append(("h2", "2. Slash commands by voice"))
    B.append(
        (
            "p",
            'Say **"slash"** and then the command name. Every Sensei slash command works this way, '
            "and nothing is sent to an AI model — commands run instantly.",
        )
    )
    B.append(
        (
            "table",
            ["Say", "What happens"],
            [
                [
                    "slash compact",
                    "Saves and summarises the chat, then restarts Sensei with a shorter history",
                ],
                ["slash plan", "Switches Sensei to plan mode (also: slash review)"],
                [
                    "slash mode",
                    "Opens a menu: current mode, Auto, Connected, Local, Plan, Review",
                ],
                [
                    "slash models",
                    "Opens the model picker so you can choose which model answers",
                ],
                [
                    "slash help  (or: how do I use this)",
                    "Opens the command guide — every command and the words to say",
                ],
                ["slash sensei help", "Opens Sensei's own help pages"],
                ["slash commands", "Same command guide"],
            ],
        )
    )
    B.append(
        (
            "p",
            "If a command has ready-made choices, saying just its name opens a menu of buttons. "
            "Menus exist for: " + ", ".join(f"`{m}`" for m in menus) + ".",
        )
    )
    B.append(
        (
            "p",
            "Some commands are protected and ask you to approve first: "
            + ", ".join(f"`{w}`" for w in sorted(sb._GATED_FIRST_WORDS))
            + ", and "
            + ", ".join(f"`{w}`" for w in sorted(sb._GATED_FULL))
            + ".",
        )
    )
    B.append(
        (
            "p",
            "If you say a command that does not exist, Tahuti tells you and suggests the closest ones.",
        )
    )

    B.append(("h2", "3. Using the cards"))
    B.append(
        (
            "table",
            ["Key", "Does"],
            [
                ["◀  (Left)", "Back / previous choice / scroll up"],
                ["▶  (Right)", "Next / next choice / scroll down"],
                ["⏎  (Enter)", "Select"],
                ["Esc", "Close or cancel"],
                ["1 – 6", "Pick that number in a menu"],
            ],
        )
    )
    B.append(
        (
            "p",
            "You can also click the buttons. When a card has more text than fits, the bottom line shows "
            "`▼ 1-10/27 ◀ ▶ more` — keep pressing ▶ to read on, or use the mouse wheel.",
        )
    )

    layouts = controller_layouts()
    if layouts.get("desktop"):
        B.append(("h2", "4. Controller buttons"))
        B.append(("table", ["Button", "Does"], [[a, b] for a, b in layouts["desktop"]]))

    B.append(("h2", "5. Every slash command (A–Z by group)"))
    B.append(
        (
            "p",
            '"…" means the command needs more words after it, for example '
            "`slash remember …` then what to remember.",
        )
    )
    for sec in order:
        B.append(("h3", titles.get(sec, sec.title())))
        B.append(
            (
                "table",
                ["Say", "What it does"],
                [
                    [
                        f"slash {sb._base(n).replace('_', ' ')}"
                        + (" …" if arg else ""),
                        hint,
                    ]
                    for n, hint, arg, _s in sections[sec]
                ],
            )
        )

    B.append(("h2", "6. Models"))
    B.append(
        (
            "ul",
            [
                "Say **slash models** to pick a provider and model. Sensei and Tahuti's voice use the same choice.",
                "On **Auto**, Sensei's router chooses for typed work and Tahuti answers with Meta Muse.",
                "If the model you picked ever fails, Tahuti says so out loud and falls back to Muse.",
                "Meta Muse (Muse Spark / Wonder) is a provider in the picker.",
            ],
        )
    )

    B.append(("h2", "7. Where things are"))
    B.append(
        (
            "table",
            ["What", "Where"],
            [
                [
                    "Your chats",
                    "`~/.master_ai_chats` — say **slash chats** to browse them",
                ],
                [
                    "Sensei's settings",
                    "`~/.master_ai_settings` and `~/.master_ai_active_model`",
                ],
                [
                    "Tahuti's goal queue and history",
                    "`~/.tahuti/goals` (finished goals in `done/`)",
                ],
                ["Card log (if a card misbehaves)", "`~/.tahuti/slash_card.log`"],
                [
                    "This README",
                    "`~/.tahuti/README.html` (regenerate: `python3 ~/ai-controller/scripts/make_tahuti_readme.py`)",
                ],
            ],
        )
    )

    B.append(("h2", "8. The reminder"))
    B.append(
        (
            "p",
            "About every two hours the avatar asks if you want to learn more. Choose **Open README**, "
            "**Later**, or **Turn off**. Say **slash reminders on** to bring it back, "
            "**slash reminders off** to silence it, or **slash readme** to open this page any time.",
        )
    )

    B.append(("h2", "9. If something is wrong"))
    B.append(
        (
            "ul",
            [
                "No sound: check the HDMI port is the one your speakers use. Tahuti falls back to a built-in voice if the main voice is out of credits.",
                "A card is stuck: press **Esc**. If that does not help, say **slash commands** to get a fresh one.",
                "Services: `systemctl --user status ai-controller-goal-runner voice-bridge ptt-pynput`.",
            ],
        )
    )
    return B


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------
def to_markdown(blocks: list[Block]) -> str:
    out: list[str] = []
    for b in blocks:
        k = b[0]
        if k in ("h1", "h2", "h3"):
            out.append("#" * int(k[1]) + " " + b[1] + "\n")
        elif k == "p":
            out.append(b[1] + "\n")
        elif k == "ul":
            out.append("\n".join(f"- {x}" for x in b[1]) + "\n")
        elif k == "table":
            head, rows = b[1], b[2]
            out.append("| " + " | ".join(head) + " |\n|" + "---|" * len(head))
            out.extend(
                "| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in rows
            )
            out.append("")
    return "\n".join(out).rstrip() + "\n"


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _inline(s: str) -> str:
    s = html.escape(s)
    s = _INLINE_CODE.sub(r"<code>\1</code>", s)
    return _BOLD.sub(r"<strong>\1</strong>", s)


_CSS = """
:root{--bg:#fff;--fg:#1d2330;--mut:#5b6577;--line:#dde2ea;--acc:#b3261e;--code:#f1f3f7}
@media(prefers-color-scheme:dark){:root{--bg:#10131a;--fg:#e6e9ef;--mut:#9aa3b5;--line:#2a3040;--acc:#ff7a70;--code:#1b2030}}
body{background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,sans-serif;margin:0}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:28px;margin:0 0 8px}h2{margin:36px 0 8px;padding-top:8px;border-top:1px solid var(--line);font-size:21px}
h3{margin:20px 0 6px;font-size:16px;color:var(--acc)}
table{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:14.5px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600}
code{background:var(--code);padding:1px 6px;border-radius:5px;font-size:.92em}
nav{margin:12px 0 4px;color:var(--mut);font-size:14px}nav a{color:var(--acc);margin-right:12px}
"""


def to_html(blocks: list[Block]) -> str:
    body: list[str] = []
    toc: list[str] = []
    for b in blocks:
        k = b[0]
        if k == "h1":
            body.append(f"<h1>{_inline(b[1])}</h1>")
        elif k == "h2":
            slug = re.sub(r"[^a-z0-9]+", "-", b[1].lower()).strip("-")
            toc.append(f'<a href="#{slug}">{html.escape(b[1].split(". ", 1)[-1])}</a>')
            body.append(f'<h2 id="{slug}">{_inline(b[1])}</h2>')
        elif k == "h3":
            body.append(f"<h3>{_inline(b[1])}</h3>")
        elif k == "p":
            body.append(f"<p>{_inline(b[1])}</p>")
        elif k == "ul":
            body.append(
                "<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in b[1]) + "</ul>"
            )
        elif k == "table":
            head = "".join(f"<th>{_inline(h)}</th>" for h in b[1])
            rws = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                for r in b[2]
            )
            body.append(f"<table><tr>{head}</tr>{rws}</table>")
    nav = "<nav>" + "".join(toc) + "</nav>"
    # nav goes right after the h1
    body.insert(1, nav)
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Tahuti README</title><style>"
        + _CSS
        + "</style></head><body><main>"
        + "\n".join(body)
        + "</main></body></html>"
    )


def main() -> int:
    blocks = build_blocks()
    md = to_markdown(blocks)
    if "--print" in sys.argv:
        print(md)
        return 0
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    MD_OUT.write_text(md, encoding="utf-8")
    HTML_OUT.write_text(to_html(blocks), encoding="utf-8")
    print(f"wrote {MD_OUT} ({len(md.splitlines())} lines) and {HTML_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
