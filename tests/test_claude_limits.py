"""The optional Claude limits feed: consent first, back off on any existing status line, undo only our own."""

import json
import os
import shlex
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import claude_statusline
import limits_setup
import lodge
import pytest
import usage_support  # noqa: F401
from usage_collector import claude_limits, claude_limits_path

ROOT = Path(__file__).resolve().parents[1]
READING = {"ignored": "private", "rate_limits": {
    "five_hour": {"used_percentage": 12, "resets_at": 1795000000, "unknown": "private"},
    "seven_day": {"used_percentage": 101, "resets_at": 1795400000},
}}


@pytest.fixture
def lodge_root(tmp_path):
    """A lodge with the wolt alpha, an empty operator home and no managed settings."""
    root = tmp_path / "wolts"
    (root / ".space").mkdir(parents=True)
    (root / "alpha/wolt").mkdir(parents=True)
    (root / "alpha/wolt/wolt.json").write_text("{}")
    (tmp_path / "home").mkdir()
    return root


def lodge_env(tmp_path, root, **extra):
    """The environment of a lodge session of this lodge (what Claude hands its status line)."""
    return {"HOME": str(tmp_path / "home"), "WOLTSPACE_WOLTS_DIR": str(root),
            "WOLTSPACE_WOLT_SESSION": "alpha-session", "WOLTSPACE_ISOLATION": "host", **extra}


def consented(root, command="recorded"):
    lodge.update_receipt(root, lambda receipt: receipt.__setitem__("claude_limits", {
        "settings_file": "unused", "statusLine": {"type": "command", "command": command}}))


# ── The helper: silent, validated, newest wins, and only for this lodge's sessions ──

def test_the_helper_keeps_only_valid_windows_and_stays_silent(tmp_path, lodge_root):
    consented(lodge_root)
    env = {"PATH": "/usr/bin:/bin", **lodge_env(tmp_path, lodge_root)}
    helper = [sys.executable, "-E", "-s", str(ROOT / "app.py"), "--wolts", str(lodge_root), "statusline"]
    snapshot = claude_limits_path(lodge_root)
    for raw in (json.dumps(READING).encode(), b"", b"not json", b"[1]", b"x" * 131073):
        result = subprocess.run(helper, input=raw, capture_output=True, env=env, check=False)
        assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
    contents = snapshot.read_bytes()
    assert b"private" not in contents and b"seven_day" not in contents
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
    assert stat.S_IMODE(snapshot.parent.stat().st_mode) == 0o700
    observed = datetime.fromisoformat(claude_limits(snapshot)["observed_at"])
    assert observed.tzinfo == UTC
    newer = json.loads(json.dumps(READING))
    newer["rate_limits"]["five_hour"]["used_percentage"] = 50
    for when in (datetime(2000, 1, 1, tzinfo=UTC), observed):  # older or equal: kept out
        claude_statusline.publish(lodge_root, when, newer, environ=lodge_env(tmp_path, lodge_root))
        assert snapshot.read_bytes() == contents


@pytest.mark.parametrize("change", ["no session", "another lodge", "no consent", "no lodge root"])
def test_the_helper_records_nothing_outside_a_consented_lodge_session(tmp_path, lodge_root, change):
    env = lodge_env(tmp_path, lodge_root)
    if change == "no consent":
        pass
    else:
        consented(lodge_root)
        if change == "no session":
            del env["WOLTSPACE_WOLT_SESSION"]  # the operator's own Claude, sharing the user settings
        elif change == "another lodge":
            other = tmp_path / "other"
            (other / ".space").mkdir(parents=True)
            env["WOLTSPACE_WOLTS_DIR"] = str(other)
        else:
            del env["WOLTSPACE_WOLTS_DIR"]
    claude_statusline.publish(lodge_root, datetime.now(UTC), READING, environ=env)
    assert not claude_limits_path(lodge_root).exists()


def test_a_far_future_snapshot_can_be_replaced_after_a_clock_step(tmp_path, lodge_root):
    consented(lodge_root)
    env = lodge_env(tmp_path, lodge_root)
    now = datetime.now(UTC)

    def publish(at, percent):
        claude_statusline.publish(lodge_root, at, {"rate_limits": {
            "five_hour": {"used_percentage": percent, "resets_at": 1795000000},
        }}, environ=env)

    snapshot = claude_limits_path(lodge_root)
    publish(now + timedelta(minutes=10), 12)
    publish(now, 25)
    assert claude_limits(snapshot)["observed_at"] == now.isoformat()
    publish(now - timedelta(minutes=1), 40)
    assert claude_limits(snapshot)["windows"]["five_hour"]["used_percent"] == 25


# ── The setup: shown first, made only on a yes, never over an existing status line ──

def test_show_names_the_file_and_the_exact_change_and_writes_nothing(tmp_path, lodge_root):
    env = lodge_env(tmp_path, lodge_root)
    planned = limits_setup.proposal(lodge_root, "alpha", env, managed=tmp_path / "managed")
    assert planned["settings_file"] == str(tmp_path / "home/.claude/settings.json")
    command = planned["change"]["statusLine"]["command"]
    assert planned["change"] == {"statusLine": {"type": "command", "command": command}}
    assert shlex.split(command)[-3:] == ["--wolts", str(lodge_root), "statusline"]
    assert shlex.split(command)[3] == str(ROOT / "app.py")
    assert not (tmp_path / "home/.claude").exists() and not lodge.receipt_path(lodge_root).exists()


def test_a_container_wolt_with_its_own_engine_home_is_its_own_target(tmp_path, lodge_root):
    (lodge_root / "alpha/.claude").mkdir()
    env = lodge_env(tmp_path, lodge_root, WOLTSPACE_ISOLATION="external")
    planned = limits_setup.proposal(lodge_root, "alpha", env, managed=tmp_path / "managed")
    assert planned["settings_file"] == str(lodge_root / "alpha/.claude/settings.json")
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "config")
    planned = limits_setup.proposal(lodge_root, "alpha", env, managed=tmp_path / "managed")
    assert planned["settings_file"] == str(tmp_path / "config/settings.json")


def test_apply_adds_only_the_status_line_and_remove_takes_it_out(tmp_path, lodge_root):
    settings = tmp_path / "home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "keep-me", "permissions": {"allow": ["x"]}}))
    settings.chmod(0o640)
    env = lodge_env(tmp_path, lodge_root)
    planned = limits_setup.apply(lodge_root, "alpha", env, managed=tmp_path / "managed")
    written = json.loads(settings.read_text())
    assert written == {"model": "keep-me", "permissions": {"allow": ["x"]}, **planned["change"]}
    assert stat.S_IMODE(settings.stat().st_mode) == 0o640
    recorded = lodge.read_receipt(lodge_root)["claude_limits"]
    assert recorded["settings_file"] == str(settings) and recorded["statusLine"] == planned["change"]["statusLine"]
    with pytest.raises(limits_setup.NotEligible, match="already recorded"):
        limits_setup.apply(lodge_root, "alpha", env, managed=tmp_path / "managed")
    assert limits_setup.remove(lodge_root) == "removed"
    assert json.loads(settings.read_text()) == {"model": "keep-me", "permissions": {"allow": ["x"]}}
    assert "claude_limits" not in lodge.read_receipt(lodge_root)
    assert limits_setup.remove(lodge_root) == "none"


def test_apply_creates_a_missing_settings_file_owner_only(tmp_path, lodge_root):
    planned = limits_setup.apply(lodge_root, "alpha", lodge_env(tmp_path, lodge_root), managed=tmp_path / "m")
    settings = Path(planned["settings_file"])
    assert json.loads(settings.read_text()) == planned["change"]
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600


def test_remove_after_a_human_edit_keeps_the_human_s_status_line(tmp_path, lodge_root):
    planned = limits_setup.apply(lodge_root, "alpha", lodge_env(tmp_path, lodge_root), managed=tmp_path / "m")
    settings = Path(planned["settings_file"])
    edited = {"statusLine": {"type": "command", "command": "my-own-line"}, "theme": "dark"}
    settings.write_text(json.dumps(edited))
    assert limits_setup.remove(lodge_root) == "kept"
    assert json.loads(settings.read_text()) == edited
    assert "claude_limits" not in lodge.read_receipt(lodge_root)


@pytest.mark.parametrize("where", ["user", "project", "project local", "managed", "managed drop-in"])
@pytest.mark.parametrize("value", [{"type": "command", "command": "custom"}, None])
def test_any_existing_status_line_wins_and_nothing_is_written(tmp_path, lodge_root, where, value):
    managed = tmp_path / "managed"
    path = {
        "user": tmp_path / "home/.claude/settings.json",
        "project": lodge_root / "alpha/.claude/settings.json",
        "project local": lodge_root / "alpha/.claude/settings.local.json",
        "managed": managed / "managed-settings.json",
        "managed drop-in": managed / "managed-settings.d/10-lodge.json",
    }[where]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"statusLine": value}))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(limits_setup.NotEligible, match="already sets a status line"):
        limits_setup.apply(lodge_root, "alpha", lodge_env(tmp_path, lodge_root), managed=managed)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before
    assert "claude_limits" not in lodge.read_receipt(lodge_root)


@pytest.mark.parametrize("problem", ["malformed", "duplicate keys", "not an object", "link", "not a lodge session",
                                     "another lodge", "not a wolt"])
def test_uncertain_settings_or_scope_leave_everything_untouched(tmp_path, lodge_root, problem):
    settings = tmp_path / "home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    env, wolt = lodge_env(tmp_path, lodge_root), "alpha"
    if problem == "malformed":
        settings.write_text("{")
    elif problem == "duplicate keys":
        settings.write_text('{"model": "a", "model": "b"}')
    elif problem == "not an object":
        settings.write_text("[]")
    elif problem == "link":
        (tmp_path / "dotfiles.json").write_text("{}")
        settings.symlink_to(tmp_path / "dotfiles.json")
    elif problem == "not a lodge session":
        del env["WOLTSPACE_WOLT_SESSION"]
    elif problem == "another lodge":
        env["WOLTSPACE_WOLTS_DIR"] = str(tmp_path)
    else:
        wolt = "nobody"
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(limits_setup.NotEligible):
        limits_setup.apply(lodge_root, wolt, env, managed=tmp_path / "managed")
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before
    assert "claude_limits" not in lodge.read_receipt(lodge_root)


def test_a_concurrent_edit_aborts_the_change(tmp_path, lodge_root, monkeypatch):
    settings = tmp_path / "home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"model": "a"}')
    real_open = os.open

    def edit_while_writing(path, *args, **kwargs):
        if ".woltspace-usage-" in str(path):
            settings.write_text('{"model": "edited meanwhile"}')
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(limits_setup.os, "open", edit_while_writing)
    with pytest.raises(limits_setup.NotEligible, match="changed meanwhile"):
        limits_setup.apply(lodge_root, "alpha", lodge_env(tmp_path, lodge_root), managed=tmp_path / "managed")
    assert json.loads(settings.read_text()) == {"model": "edited meanwhile"}
    assert "claude_limits" not in lodge.read_receipt(lodge_root)
    assert sorted(p.name for p in settings.parent.iterdir()) == ["settings.json"]


def test_the_command_asks_for_the_human_s_yes(tmp_path, lodge_root):
    env = {"PATH": "/usr/bin:/bin", **lodge_env(tmp_path, lodge_root)}
    app = [sys.executable, "-E", "-s", str(ROOT / "app.py"), "--wolts", str(lodge_root), "limits-setup"]
    shown = subprocess.run([*app, "show", "--wolt", "alpha"], capture_output=True, text=True, env=env, check=False)
    assert shown.returncode == 0 and json.loads(shown.stdout)["change"]["statusLine"]["type"] == "command"
    refused = subprocess.run([*app, "apply", "--wolt", "alpha"], capture_output=True, text=True, env=env, check=False)
    assert refused.returncode == 2 and "--yes" in refused.stderr
    assert not (tmp_path / "home/.claude/settings.json").exists()
