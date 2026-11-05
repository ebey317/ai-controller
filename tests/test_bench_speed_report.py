"""TAHUTI-BENCH speed report: the arithmetic, and the honesty of "not measured"."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bench_speed_report as rep  # noqa: E402


def _goal(root, sub, name, stages):
    d = root / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"stages": stages}))


def test_stage_deltas_come_from_the_archive(tmp_path):
    _goal(tmp_path, "done", "a.json", {"goal_time": 100.0, "claimed": 101.0,
                                       "routed": 101.05, "answer": 103.0, "spoken": 103.2})
    data = rep.collect(rep.load_archives(tmp_path))
    assert data["A1"] == [1.0]
    assert data["A2"] == [0.05000000000001137] or abs(data["A2"][0] - 0.05) < 1e-6
    assert data["A4"] == [1.9500000000000455] or abs(data["A4"][0] - 1.95) < 1e-6
    assert abs(data["A5"][0] - 0.2) < 1e-6


def test_a_goal_without_stages_is_skipped_not_zeroed(tmp_path):
    _goal(tmp_path, "done", "old.json", {})          # pre-T6 archive
    _goal(tmp_path, "failed", "bad.json", {"claimed": 50.0, "routed": 40.0})  # backwards
    data = rep.collect(rep.load_archives(tmp_path))
    assert all(v == [] for v in data.values())


def test_p95_is_the_95th_percentile_not_the_max(tmp_path):
    base = 1000.0
    stages = [{"goal_time": base, "claimed": base + i + 1, "routed": base + i + 1,
               "answer": base + i + 1, "spoken": base + i + 1} for i in range(100)]
    for i, s in enumerate(stages):
        _goal(tmp_path, "done", f"{i:03}.json", s)
    data = rep.collect(rep.load_archives(tmp_path))
    assert len(data["A1"]) == 100
    assert rep.p95(data["A1"]) < max(data["A1"])      # not just the max


def test_unmeasured_stages_are_labelled_not_invented():
    assert set(rep.NOT_MEASURED) == {"A3", "A6"}
    assert "log" in rep.NOT_MEASURED["A3"]
