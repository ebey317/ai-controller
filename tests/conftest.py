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


# CI / fresh machine: Sensei (~/master-ai-cli) is not beside this repo. The slash
# parser needs its command registry, so fall back to a frozen copy for TESTS only.
# On the operator's machine the real registry is used.
def _use_frozen_registry_when_sensei_absent():
    scripts = os.path.join(_ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import pathlib

    import sensei_slash

    if not sensei_slash.REGISTRY_PATH.exists():
        sensei_slash.REGISTRY_PATH = pathlib.Path(_ROOT) / "tests" / "fixtures" / "command_registry.py"


_use_frozen_registry_when_sensei_absent()
