"""2026-11-05: Tahuti must use what it knows (city, folders) and open the real
folder; the secret-path blocklist must cover the credential stores the gate
review named."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tahuti_goal_runner as runner  # noqa: E402
import tahuti_operator as op  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    for d in ("Desktop/Projects", "projects", "Downloads", ".ssh", ".gnupg", "Desktop/Projects/keychain"):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.setattr(op, "HOME", tmp_path)
    monkeypatch.setattr(op, "PROFILE", tmp_path / ".tahuti" / "operator_profile.md")
    return tmp_path


# -- folders ------------------------------------------------------------------
def test_projects_resolves_to_a_real_folder(home):
    folder, _ = op.resolve_folder("my projects folder")
    assert folder == (home / "Desktop" / "Projects").resolve()


def test_on_my_desktop_wins_when_two_projects_folders_exist(home):
    folder, note = op.resolve_folder("projects folder that's on my desktop")
    assert folder == (home / "Desktop" / "Projects").resolve()
    assert "projects" in note.lower()          # says it also saw the other one


def test_a_missing_folder_is_an_honest_error_not_a_guess(home):
    folder, note = op.resolve_folder("my taxes folder")
    assert folder is None and "taxes" in note


def test_nothing_outside_home_and_no_secret_folders(home):
    assert op.resolve_folder("/etc")[0] is None
    folder, _ = op.resolve_folder(".gnupg", is_secret=lambda p: ".gnupg" in str(p))
    assert folder is None


def test_open_folder_tool_opens_the_folder_itself(home, monkeypatch):
    monkeypatch.setattr(runner.tahuti_operator, "HOME", home)
    launched = []
    monkeypatch.setattr(runner, "_launch_visible",
                        lambda argv, tag: launched.append(argv) or {"ok": True, "opened": tag, "pid": 1})
    res = runner._open_folder_tool("projects")
    assert res["ok"] and res["folder"].endswith("Desktop/Projects")
    assert launched == [["xdg-open", res["folder"]]]


def test_open_folder_tool_refuses_a_secret_location(home, monkeypatch):
    monkeypatch.setattr(runner.tahuti_operator, "HOME", home)
    monkeypatch.setattr(runner, "_launch_visible", lambda *a, **k: pytest.fail("must not open"))
    monkeypatch.setattr(runner, "_is_secret_path", lambda p: ".ssh" in str(p))
    assert runner._open_folder_tool("~/.ssh")["ok"] is False


def test_the_model_is_given_the_open_folder_tool():
    assert "open_folder" in {t["name"] for t in runner.OPEN_TOOLS}


# -- profile / weather -------------------------------------------------------
def test_profile_is_seeded_and_names_the_city(home):
    assert "Indianapolis" in op.profile_prompt()
    assert (home / ".tahuti" / "operator_profile.md").exists()
    assert op.city().startswith("Indianapolis")


def test_weather_question_detection():
    assert op.is_weather_question("What's the weather today?")
    assert not op.is_weather_question("open my projects folder")


def test_weather_uses_the_known_city_and_never_asks(home):
    seen = []
    line = op.weather_line(fetch=lambda u: seen.append(u) or "Sunny, +61°F, feels like +58°F, wind 5mph, humidity 40%")
    assert "Indianapolis" in seen[0] and "Indianapolis" in line and "?" not in line


@pytest.mark.parametrize("bad", ["", "<html>oops</html>", "Unknown location; please try"])
def test_weather_failure_is_none_not_a_guess(home, bad):
    assert op.weather_line(fetch=lambda u: bad) is None


# -- secret paths (gate review) ----------------------------------------------
@pytest.mark.parametrize("rel", [".gnupg/private-keys-v1.d/x", ".config/gcloud/credentials.db",
                                 ".config/azure/a", ".docker/config.json", ".npmrc", ".pypirc",
                                 ".netrc", ".git-credentials", ".kube/config", ".config/gh/hosts.yml",
                                 ".config/google-chrome/Default/Cookies"])
def test_credential_stores_are_blocked(rel):
    assert runner._is_secret_path(Path.home() / rel)


def test_ordinary_files_are_not_blocked():
    assert not runner._is_secret_path(Path.home() / "Desktop" / "Projects" / "notes.md")
