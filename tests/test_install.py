"""What install changes, and that update and uninstall touch only that: manifest, skill, nightly save."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from usage_support import claude, emit, request

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path):
    """A copy of the app installed in a lodge with the wolts alpha and beta."""
    root = tmp_path / "wolts"
    for name in ("alpha", "beta"):
        (root / name / "wolt").mkdir(parents=True)
        (root / name / "wolt/wolt.json").write_text("{}")
    (root / ".space").mkdir()
    app = root / "apps" / "woltspace-usage"
    shutil.copytree(ROOT, app, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "tests"))
    return root, app


def run(app, *args, env=None):
    return subprocess.run([sys.executable, "-E", "-s", str(app / "app.py"), *args], capture_output=True, text=True, check=False,
                          env={"PATH": "/usr/bin:/bin", "TZ": "Europe/Berlin", **(env or {})})


def test_configure_resolves_the_keeper_template_and_reapplies_after_an_update(checkout):
    root, app = checkout
    assert json.loads((app / "woltspace.json").read_text())["keeper"] == "{{keeper}}"
    assert run(app, "configure", "--keeper", "alpha", "--port", "4076", "--schedule", "usage-save").returncode == 0
    manifest = json.loads((app / "woltspace.json").read_text())
    assert (manifest["keeper"], manifest["port"], manifest["public"]) == ("alpha", 4076, False)
    shutil.copy(ROOT / "woltspace.json", app / "woltspace.json")  # an update brings the template back
    assert run(app, "configure").returncode == 0  # no flags: the recorded values come back
    assert json.loads((app / "woltspace.json").read_text())["keeper"] == "alpha"
    status = json.loads(run(app, "status").stdout)
    assert (status["keeper"], status["port"], status["schedule"]) == ("alpha", 4076, "usage-save")
    assert status["wolts_dir"] == str(root)


def test_configure_refuses_a_keeper_that_is_not_a_wolt(checkout):
    _, app = checkout
    result = run(app, "configure", "--keeper", "nobody", "--port", "4075")
    assert result.returncode == 2 and "keeper" in result.stderr
    assert json.loads((app / "woltspace.json").read_text())["keeper"] == "{{keeper}}"


def test_the_skill_points_at_the_installed_command_and_uninstalls_cleanly(checkout):
    root, app = checkout
    assert run(app, "skill", "install").returncode == 0
    installed = root / ".space/shared-skills/lodge-usage/SKILL.md"
    text = installed.read_text()
    assert text.startswith("---\nname: lodge-usage\n") and "{{APP}}" not in text
    assert f"{app / 'app.py'} --wolts {root} summary --since 7d" in text
    # The lodge adds its shared-edit notice after the frontmatter; removal still recognizes ours.
    installed.write_text(text.replace("---\n\n", "---\n\n<!-- lodge notice -->\n", 1))
    assert run(app, "skill", "remove").returncode == 0
    assert not installed.parent.exists()


def test_an_existing_unrelated_skill_is_preserved_and_a_distinct_name_used(checkout):
    root, app = checkout
    theirs = root / ".space/shared-skills/lodge-usage/SKILL.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("---\nname: lodge-usage\ndescription: someone else's\n---\nTheirs.\n")
    refused = run(app, "skill", "install")
    assert refused.returncode == 4 and "another --name" in refused.stderr
    assert run(app, "skill", "install", "--name", "lodge-usage-app").returncode == 0
    ours = root / ".space/shared-skills/lodge-usage-app/SKILL.md"
    assert ours.read_text().startswith("---\nname: lodge-usage-app\n")
    assert run(app, "skill", "remove").returncode == 4  # the default name is not ours: left alone
    assert theirs.read_text().endswith("Theirs.\n")
    assert run(app, "skill", "remove", "--name", "lodge-usage-app").returncode == 0
    assert not ours.parent.exists() and theirs.exists()


def test_a_skill_with_files_we_did_not_write_is_not_removed(checkout):
    root, app = checkout
    run(app, "skill", "install")
    extra = root / ".space/shared-skills/lodge-usage/notes.md"
    extra.write_text("a human's note")
    assert run(app, "skill", "remove").returncode == 4 and extra.exists()


@pytest.mark.parametrize("name", ["usage", "lodge-Usage", "lodge--x", "lodge-x/../y", "lodge-" + "a" * 60])
def test_skill_names_follow_the_lodge_rule(checkout, name):
    _, app = checkout
    assert run(app, "skill", "install", "--name", name).returncode == 2


def test_the_nightly_save_is_the_documented_command_and_is_idempotent(checkout, tmp_path):
    root, app = checkout
    guide = (ROOT / "AGENTS.md").read_text()
    assert "python3 -E -s <APP>/app.py save" in guide and "55 23 * * *" in guide
    emit(claude(tmp_path), request("one"), request("two", fresh=9))
    assert run(app, "save").returncode == 3  # the import is decided first
    assert run(app, "import").returncode == 0
    first = run(app, "save")
    history = root / ".space/woltspace-usage/history.json"
    saved = json.loads(history.read_text())
    second = run(app, "save")
    assert first.returncode == second.returncode == 0
    again = json.loads(history.read_text())
    assert again["rows"] == saved["rows"] and again["coverage"] == saved["coverage"]
    assert sum(row["amount"] for row in saved["rows"] if row["metric"] == "input_fresh") == 14
    assert re.fullmatch(r'\{"saved": true, .*\}\n', second.stdout)


def test_the_app_never_writes_inside_its_checkout(checkout, tmp_path):
    _, app = checkout
    emit(claude(tmp_path), request("one"))
    before = sorted(str(path.relative_to(app)) for path in app.rglob("*") if "__pycache__" not in path.parts)
    for args in (("import",), ("save",), ("summary",), ("table", "--by", "day"), ("limits",), ("status",)):
        assert run(app, *args).returncode == 0, args
    after = sorted(str(path.relative_to(app)) for path in app.rglob("*") if "__pycache__" not in path.parts)
    assert after == before
