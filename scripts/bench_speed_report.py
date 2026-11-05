#!/usr/bin/env python3
"""TAHUTI-BENCH A1–A6 speed report, read straight from the goal archive.

Each archived goal now carries a `stages` map (see `_archive` in
tahuti_goal_runner.py), so speed is measured instead of inferred:

    goal_time  when the trigger wrote the goal        (A1 start, from the producer)
    claimed    when the runner took the file          (A1 end)
    routed     when the lane was chosen               (A2 end)
    answer     when the answer existed                (A4 end)
    spoken     when /speak returned                   (A5 end)

A3 (slash dispatch) and A6 (first audio sample) do not exist in the archive
anywhere yet — their timestamps live in the Sensei slash log and the voice
bridge. This report prints them as NOT MEASURED rather than inventing a number.

Usage:  python3 scripts/bench_speed_report.py [--dir DIR] [--json]
Exit code: 0 when every measured stage meets its p95 target, 1 when any misses.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DIR = Path.home() / ".tahuti" / "goals"

# stage -> (from, to, p95 target seconds, label). None target = not measured yet.
STAGES = [
    ("A1", "goal_time", "claimed", 2.0, "listen -> text (trigger write -> claimed)"),
    ("A2", "claimed", "routed", 0.2, "route (claimed -> lane chosen)"),
    ("A4", "routed", "answer", None, "model answer (lane -> answer ready)"),
    ("A5", "answer", "spoken", 0.5, "card up / speak issued (answer -> spoken)"),
]
NOT_MEASURED = {
    "A3": "slash dispatch — timestamp lives in the slash log, not the archive",
    "A6": "first audio sample — timestamp lives in the voice bridge, not the archive",
}


def load_archives(root: Path) -> list[dict]:
    goals = []
    for sub in ("done", "failed"):
        for path in sorted((root / sub).glob("*.json")):
            try:
                goals.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
    return goals


def _dt(value: float) -> datetime:
    return datetime.fromtimestamp(value)


def collect(goals: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for name, start, end, _target, _label in STAGES:
        samples: list[float] = []
        for g in goals:
            st = g.get("stages") or {}
            try:
                a, b = float(st[start]), float(st[end])
            except (KeyError, TypeError, ValueError):
                continue
            if a <= 0 or b <= 0 or b < a:
                continue
            # A1 can straddle a clock jump; anything above an hour is junk.
            if name == "A1" and b - a > 3600:
                continue
            samples.append(b - a)
        out[name] = samples
    return out


def p95(samples: list[float]) -> float:
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


def report(goals: list[dict], as_json: bool = False,
           archive: Path = DEFAULT_DIR) -> int:
    data = collect(goals)
    rows = []
    failed = False
    for name, start, end, target, label in STAGES:
        samples = data[name]
        row = {
            "stage": name,
            "what": label,
            "n": len(samples),
            "p50": round(statistics.median(samples), 3) if samples else None,
            "p95": round(p95(samples), 3) if samples else None,
            "target_p95": target,
            "verdict": "NOT MEASURED",
        }
        if samples and target is not None:
            ok = row["p95"] <= target
            row["verdict"] = "PASS" if ok else "FAIL"
            failed = failed or not ok
        elif samples:
            row["verdict"] = "MEASURED (no target)"
        rows.append(row)
    for name, why in NOT_MEASURED.items():
        rows.append({"stage": name, "what": why, "n": 0, "p50": None, "p95": None,
                     "target_p95": None, "verdict": "NOT MEASURED"})

    if as_json:
        # Round-5 cheap fix: echo the dir the user PASSED, not DEFAULT_DIR —
        # --dir other/dir previously reported a location it did not read.
        print(json.dumps({"archive": str(archive), "goals": len(goals),
                          "rows": rows}, indent=2))
        return 1 if failed else 0

    print(f"\nTAHUTI-BENCH speed — {len(goals)} archived goals\n")
    print(f"{'stage':5} {'n':>4} {'p50 s':>8} {'p95 s':>8} {'target':>7}  verdict  what")
    for r in rows:
        p50 = "-" if r["p50"] is None else f"{r['p50']:.3f}"
        p95v = "-" if r["p95"] is None else f"{r['p95']:.3f}"
        tgt = "-" if r["target_p95"] is None else f"{r['target_p95']:.1f}"
        print(f"{r['stage']:5} {r['n']:>4} {p50:>8} {p95v:>8} {tgt:>7}  "
              f"{r['verdict']:<12} {r['what']}")
    print()
    if not goals:
        print("No archived goals yet — run a goal, then re-run this report.")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TAHUTI-BENCH A1-A6 speed report")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="goals archive root")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    return report(load_archives(Path(args.dir).expanduser()), as_json=args.json,
                  archive=Path(args.dir).expanduser())


if __name__ == "__main__":
    sys.exit(main())
