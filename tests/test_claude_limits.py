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
CLAUDE_ID = "00000000-0000-0000-0000-0000000000c1"  # what the lodge recorded for alpha-session
READING = {"ignored": "private", "session_id": CLAUDE_ID, "rate_limits": {
    "five_hour": {"used_percentage": 12, "resets_at": 1795000000, "unknown": "private"},
    "seven_day": {"used_percentage": 101, "resets_at": 1795400000},
}}


@pytest.fixture
def lodge_root(tmp_path, monkeypatch):
    """A lodge with the wolt alpha, an empty operator home, no managed settings, and a
    `woltspace` on PATH whose session records hold alpha-session with CLAUDE_ID."""
    root = tmp_path / "wolts"
    (root / ".space").mkdir(parents=True)
    (root / "alpha/wolt").mkdir(parents=True)
    (root / "alpha/wolt/wolt.json").write_text("{}")
    (tmp_path / "home").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    records = [{"name": "alpha-session", "wolt": "alpha", "harness_session_id": CLAUDE_ID.upper()},
               {"name": "alpha-other", "wolt": "alpha", "harness_session_id": "00000000-0000-0000-0000-0000000000c2"}]
    (tmp_path / "records.json").write_text(json.dumps(records))
    fake = bin_dir / "woltspace"
    fake.write_text(f"#!/bin/sh\necho \"$*\" >> {tmp_path / 'cli-calls'}\ncat {tmp_path / 'records.json'}\n")
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin:/bin")
    return root


def cli_calls(tmp_path):
    try:
        return (tmp_path / "cli-calls").read_text().splitlines()
    except FileNotFoundError:
        return []


def lodge_env(tmp_path, root, **extra):
    """The environment of a lodge session of this lodge (what Claude hands its status line)."""
    return {"HOME": str(tmp_path / "home"), "WOLTSPACE_WOLTS_DIR": str(root), "WOLTSPACE_WOLT_NAME": "alpha",
            "WOLTSPACE_WOLT_SESSION": "alpha-session", "WOLTSPACE_ISOLATION": "host", **extra}


def consented(root, command="recorded"):
    lodge.update_receipt(root, lambda receipt: receipt.__setitem__("claude_limits", {
        "settings_file": "unused", "statusLine": {"type": "command", "command": command}}))


# ── The helper: silent, validated, newest wins, and only for this lodge's sessions ──

def test_the_helper_keeps_only_valid_windows_and_stays_silent(tmp_path, lodge_root):
    consented(lodge_root)
    env = {"PATH": os.environ["PATH"], **lodge_env(tmp_path, lodge_root)}
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


@pytest.mark.parametrize("claude_id", [
    "00000000-0000-0000-0000-0000000000c2",  # another lodge session's id
    "00000000-0000-0000-0000-00000000dead",  # a Claude started inside the session, inheriting its env
    None, 7,
])
def test_the_helper_records_only_the_registered_claude_session(tmp_path, lodge_root, claude_id):
    consented(lodge_root)
    reading = dict(READING, session_id=claude_id)
    claude_statusline.publish(lodge_root, datetime.now(UTC), reading, environ=lodge_env(tmp_path, lodge_root))
    assert not claude_limits_path(lodge_root).exists()
    claude_statusline.publish(lodge_root, datetime.now(UTC), READING, environ=lodge_env(tmp_path, lodge_root))
    assert claude_limits_path(lodge_root).exists()


def test_without_the_lodge_s_records_nothing_is_recorded(tmp_path, lodge_root, monkeypatch):
    consented(lodge_root)
    monkeypatch.setenv("PATH", "/nonexistent")  # no `woltspace` to ask
    claude_statusline.publish(lodge_root, datetime.now(UTC), READING, environ=lodge_env(tmp_path, lodge_root))
    assert not claude_limits_path(lodge_root).exists()


def test_a_verified_session_is_not_looked_up_again(tmp_path, lodge_root):
    consented(lodge_root)
    env = lodge_env(tmp_path, lodge_root)
    for minutes in (3, 2, 1):
        claude_statusline.publish(lodge_root, datetime.now(UTC) - timedelta(minutes=minutes), READING, environ=env)
    assert cli_calls(tmp_path) == ["session list --json --wolt alpha"]
    cache = lodge.state_dir(lodge_root) / "claude-sessions.json"
    assert json.loads(cache.read_text()) == {"verified": {"alpha-session": CLAUDE_ID}, "refused": {}}
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600


def test_a_far_future_snapshot_can_be_replaced_after_a_clock_step(tmp_path, lodge_root):
    consented(lodge_root)
    env = lodge_env(tmp_path, lodge_root)
    now = datetime.now(UTC)

    def publish(at, percent):
        claude_statusline.publish(lodge_root, at, {"session_id": CLAUDE_ID, "rate_limits": {
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


def test_apply_and_remove_leave_every_other_byte_of_the_file_alone(tmp_path, lodge_root):
    settings = tmp_path / "home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    original = '{\n    "model": "keep-me",\n    "note": "café",\n    "permissions": {"allow": ["x"]}\n}\n'
    settings.write_text(original)
    limits_setup.apply(lodge_root, "alpha", lodge_env(tmp_path, lodge_root), managed=tmp_path / "managed")
    applied = settings.read_text()
    assert applied.startswith('{\n    "model": "keep-me",\n    "note": "café",\n    "permissions": {"allow": ["x"]},\n')
    assert json.loads(applied)["statusLine"]["type"] == "command"
    assert limits_setup.remove(lodge_root) == "removed"
    assert settings.read_text() == original


def test_the_intent_is_recorded_before_the_settings_file_is_touched(tmp_path, lodge_root, monkeypatch):
    seen = []
    real_replace = limits_setup._replace

    def replace(path, before, text):
        seen.append(lodge.read_receipt(lodge_root).get("claude_limits", {}).get("state"))
        return real_replace(path, before, text)

    monkeypatch.setattr(limits_setup, "_replace", replace)
    limits_setup.apply(lodge_root, "alpha", lodge_env(tmp_path, lodge_root), managed=tmp_path / "managed")
    assert seen == ["applying"]
    assert lodge.read_receipt(lodge_root)["claude_limits"]["state"] == "applied"


def test_a_failed_settings_write_leaves_no_recorded_change(tmp_path, lodge_root, monkeypatch):
    def failing(path, before, text):
        raise OSError("disk full")

    monkeypatch.setattr(limits_setup, "_replace", failing)
    with pytest.raises(OSError):
        limits_setup.apply(lodge_root, "alpha", lodge_env(tmp_path, lodge_root), managed=tmp_path / "managed")
    assert "claude_limits" not in lodge.read_receipt(lodge_root)
    assert not (tmp_path / "home/.claude/settings.json").exists()


def test_an_unrecorded_claude_is_refused_without_asking_on_every_update(tmp_path, lodge_root):
    other = "00000000-0000-0000-0000-00000000dead"
    clock = [1000.0]

    def ask():
        return claude_statusline._registered(lodge_root, "alpha-session", "alpha", other, subprocess.run,
                                             now=lambda: clock[0])

    assert [ask(), ask(), ask()] == [False, False, False]
    assert len(cli_calls(tmp_path)) == 1
    clock[0] += claude_statusline.REFUSAL_SECONDS + 1  # a refusal is remembered only briefly
    assert ask() is False and len(cli_calls(tmp_path)) == 2


def test_overlapping_applies_never_orphan_the_change(tmp_path, lodge_root, monkeypatch):
    """Interleaving: the first apply is held after reading the settings, before recording
    anything, while a second apply runs."""
    import threading
    env, managed = lodge_env(tmp_path, lodge_root), tmp_path / "managed"
    real_member, entered, release = limits_setup._with_member, threading.Event(), threading.Event()
    calls = []

    def held_member(before, settings, status_line):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            release.wait(10)
        return real_member(before, settings, status_line)

    monkeypatch.setattr(limits_setup, "_with_member", held_member)
    outcomes = {}

    def run(label):
        try:
            limits_setup.apply(lodge_root, "alpha", env, managed=managed)
            outcomes[label] = "applied"
        except limits_setup.NotEligible as error:
            outcomes[label] = str(error)

    first = threading.Thread(target=run, args=("first",))
    first.start()
    assert entered.wait(10)
    second = threading.Thread(target=run, args=("second",))
    second.start()
    second.join(0.5)  # it waits for the first instead of racing it
    release.set()
    first.join(10)
    second.join(10)
    assert outcomes["first"] == "applied" and "already recorded" in outcomes["second"]
    settings = tmp_path / "home/.claude/settings.json"
    assert "statusLine" in json.loads(settings.read_text())
    assert lodge.read_receipt(lodge_root)["claude_limits"]["state"] == "applied"
    assert limits_setup.remove(lodge_root) == "removed"
    assert "statusLine" not in json.loads(settings.read_text())


def test_a_failed_attempt_never_clears_another_attempt_s_entry(lodge_root):
    entry = {"settings_file": "x", "statusLine": {}, "state": "applied", "attempt": "winner"}
    lodge.update_receipt(lodge_root, lambda receipt: receipt.__setitem__("claude_limits", entry))
    limits_setup._drop_attempt(lodge_root, "loser")
    assert lodge.read_receipt(lodge_root)["claude_limits"] == entry
    limits_setup._drop_attempt(lodge_root, "winner")
    assert "claude_limits" not in lodge.read_receipt(lodge_root)
