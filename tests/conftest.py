"""Pytest bootstrap: make both import conventions work.

Working convention in this repo is bare imports with an explicit
sys.path insert per test module (see test_tahuti_goal_runner.py).
Newer files use ``from scripts.x import ...`` (repo-root convention).
This conftest puts the repo root on sys.path so both styles collect.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
