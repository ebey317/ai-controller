"""Regressions found by installing a fresh clone into an empty HOME (2026-11-11)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _reqs():
    return {
        ln.split("==")[0].strip().lower()
        for ln in (ROOT / "requirements.txt").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    }


def test_requirements_cover_imports_that_broke_a_fresh_install():
    # voice_bridge uses Form/File uploads (needs python-multipart);
    # voice_fx imports soundfile for the default Kokoro voice.
    assert {"python-multipart", "soundfile"} <= _reqs()


def test_shipped_controller_profile_has_no_personal_paths():
    amgp = (ROOT / "profiles" / "dont delete .gamecontroller.amgp").read_text()
    assert "/home/elijah" not in amgp
    assert "__AI_CONTROLLER_DIR__" in amgp  # install.sh substitutes this


def test_installer_does_not_list_a_service_twice():
    text = (ROOT / "install.sh").read_text()
    assert '[[ " ${SERVICES[*]} " == *" ${svc} "* ]] || SERVICES+=' in text
