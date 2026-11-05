"""Static consistency checks between install.sh, systemd/, and requirements.txt.

These exist because the actual outage on 2026-08-28 wasn't a code bug -- it
was install.sh's SERVICES list silently drifting from what's in systemd/
(two services, a-button-double-tap-enter.service and gip-audio-watchdog.service,
were deployed on the live host but absent from both this list and, in the
second case, the systemd/ directory itself), and install.sh's inline pip
package list drifting from requirements.txt (missing evdev and pycairo).
Both drifts meant a clean install.sh run would have reproduced the outage.
These tests fail loudly the next time either list stops matching reality.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _services_in_install_sh():
    text = (REPO_ROOT / "install.sh").read_text()
    match = re.search(r"SERVICES=\((.*?)\)", text, re.DOTALL)
    assert match, "install.sh has no SERVICES=(...) array"
    return set(re.findall(r"([a-z0-9._-]+\.service)", match.group(1)))


def _service_templates_on_disk():
    return {p.name for p in (REPO_ROOT / "systemd").glob("*.service")}


def test_every_service_template_is_referenced_by_install_sh():
    # Most templates are installed via the user-level SERVICES array; a
    # couple (xone-driver-guard.service) are system-level units installed
    # through a separate pkexec code path instead. Either way, the filename
    # must appear *somewhere* in install.sh -- if it appears nowhere, the
    # template is orphaned and install.sh will never deploy it.
    templates = _service_templates_on_disk()
    text = (REPO_ROOT / "install.sh").read_text()
    orphaned = {svc for svc in templates if svc not in text}
    assert (
        not orphaned
    ), f"systemd/ has templates install.sh never references anywhere: {orphaned}"


def test_every_listed_service_has_a_template():
    templates = _service_templates_on_disk()
    listed = _services_in_install_sh()
    missing = listed - templates
    assert (
        not missing
    ), f"install.sh's SERVICES array references files missing from systemd/: {missing}"


def test_readme_nudge_pair_is_installed_and_timer_enabled():
    # The avatar "Want to learn more?" card fires from tahuti-readme-nudge
    # (oneshot service driven by its timer). Both templates must exist in
    # systemd/, and install.sh must deploy the pair and enable the TIMER —
    # a timer only runs when enabled, unlike the launcher-managed services,
    # which are installed disabled on purpose.
    nudge_svc = REPO_ROOT / "systemd" / "tahuti-readme-nudge.service"
    nudge_timer = REPO_ROOT / "systemd" / "tahuti-readme-nudge.timer"
    text = (REPO_ROOT / "install.sh").read_text()
    assert (
        nudge_svc.exists() and nudge_timer.exists()
    ), "tahuti-readme-nudge service/timer templates must exist in systemd/"
    assert (
        "tahuti-readme-nudge.service" in text
    ), "install.sh never deploys tahuti-readme-nudge.service"
    assert (
        "tahuti-readme-nudge.timer" in text
    ), "install.sh never deploys tahuti-readme-nudge.timer"
    assert re.search(
        r"systemctl\s+--user\s+enable\s+--now\s+tahuti-readme-nudge\.timer", text
    ), "install.sh must enable --now tahuti-readme-nudge.timer (timers do not run disabled)"
    # The nudge pair must NOT ride the SERVICES array: that array is disabled
    # wholesale after install, which would undo a timer enable.
    listed = _services_in_install_sh()
    assert "tahuti-readme-nudge.service" not in listed, (
        "tahuti-readme-nudge.service must stay out of SERVICES (the disable "
        "loop would defeat the timer)"
    )


def test_optional_services_are_guarded_and_missing_tahuti_is_reported():
    text = (REPO_ROOT / "install.sh").read_text()
    match = re.search(r"OPTIONAL_SERVICES=\((.*?)\)", text, re.DOTALL)
    assert match, "install.sh must declare optional services separately"
    optional = set(re.findall(r"([a-z0-9._-]+\.service)", match.group(1)))
    assert optional == {
        "ptt-pynput-headless.service",
        "voice-bridge-headless.service",
        "ai-controller-goal-runner.service",
    }
    assert '[[ -f "${REPO_DIR}/systemd/${svc}" ]]' in text
    assert "WARNING: Tahuti goal runner not installed" in text


def test_required_templates_are_validated_before_installation():
    text = (REPO_ROOT / "install.sh").read_text()
    validation = text.index("ERROR: required systemd template missing")
    install = text.index('cp "${INSTALL_DIR}/systemd/${svc}"')
    assert validation < install
    assert '[[ ! -f "${REPO_DIR}/systemd/${svc}" ]]' in text


def test_services_remain_launcher_managed_not_enabled_at_install():
    text = (REPO_ROOT / "install.sh").read_text()
    assert 'systemctl --user disable "${svc}"' in text
    assert 'systemctl --user enable --now "${svc}"' not in text


def test_install_sh_installs_from_requirements_txt_not_a_hardcoded_list():
    text = (REPO_ROOT / "install.sh").read_text()
    assert (
        '-r "${REPO_DIR}/requirements.txt"' in text
        or "-r ${REPO_DIR}/requirements.txt" in text
    ), (
        "install.sh should pip install from requirements.txt, not a separate "
        "hardcoded package list -- the two will drift apart again otherwise"
    )
