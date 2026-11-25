"""Importing a lodge's native usage history once, consistently, without touching the source."""

import fcntl
import hashlib
import json
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import history_import
import lodge
import pytest
import usage_collector
from usage_support import ZONE, claude, emit, request

ROOT = Path(__file__).resolve().parents[1]


def native(root, **history):
    """A native history as the lodge saves it (0600 in a 0700 folder, with its lock)."""
    data = {"schema_version": 1, "timezone": "Asia/Tokyo", "rows": [
        {"day": "2026-10-30", "provider": "claude", "wolt": "alpha", "session_id": "s1",
         "model": "claude-example", "metric": "output", "amount": 40},
        {"day": "2026-10-31", "provider": "codex", "wolt": "beta", "session_id": "s2",
         "model": "codex-example", "metric": "input_fresh", "amount": 9},
    ], "coverage": [{"day": "2026-10-30", "provider": "claude", "wolt": "alpha", "sources": ["a" * 64]}],
        "codex_limits": None}
    data.update(history)
    path = history_import.native_history(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    path.chmod(0o600)
    path.with_name("history.json.lock").touch(mode=0o600)
    return path, data


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "wolts"
    (root / ".space").mkdir(parents=True)
    return root


def run_app(root, *args):
    return subprocess.run([sys.executable, "-E", "-s", str(ROOT / "app.py"), "--wolts", str(root), *args],
                          capture_output=True, text=True, check=False, env={"PATH": "/usr/bin:/bin", "TZ": "Europe/Berlin"})


def test_a_native_history_is_copied_exactly_and_the_source_is_preserved(root):
    source, _ = native(root)
    before = (source.read_bytes(), source.stat().st_mtime_ns, stat.S_IMODE(source.stat().st_mode))
    decision = history_import.run(root)
    destination = usage_collector.history_path(root)
    assert destination.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert (source.read_bytes(), source.stat().st_mtime_ns, stat.S_IMODE(source.stat().st_mode)) == before
    assert decision == {"status": "imported", "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "rows": 2, "days": 2, "timezone": "Asia/Tokyo",
                        "first_day": "2026-10-30", "last_day": "2026-10-31"}
    assert lodge.read_receipt(root)["import"] == decision
    assert sorted(p.name for p in destination.parent.iterdir()) == [
        "history.json", "history.json.lock", "install.json", "install.lock"]


def test_the_import_keeps_day_boundaries_and_high_water_rows_on_the_next_save(root, tmp_path):
    native(root)
    history_import.run(root)
    emit(claude(tmp_path), request("one"))  # a fresh day, counted in the imported zone
    report = usage_collector.save(root, zone=ZONE, bases={})
    assert report["timezone"] == "Asia/Tokyo"
    saved = json.loads(usage_collector.history_path(root).read_text())
    amounts = {(row["day"], row["wolt"], row["metric"]): row["amount"] for row in saved["rows"]}
    assert amounts[("2026-10-30", "alpha", "output")] == 40  # its transcripts are gone: kept, flagged
    assert amounts[("2026-10-31", "beta", "input_fresh")] == 9
    assert any(gap["reason"] == "retained_partial_day" and gap["day"] == "2026-10-30"
               for gap in report["coverage_gaps"])


def test_a_repeat_install_keeps_the_app_s_own_history(root):
    native(root)
    first = history_import.run(root)
    destination = usage_collector.history_path(root)
    destination.write_text(json.dumps({"schema_version": 1, "timezone": "Asia/Tokyo", "rows": []}))
    kept = destination.read_bytes()
    native(root, rows=[])  # the native file moved on meanwhile
    assert history_import.run(root) == first
    assert destination.read_bytes() == kept


@pytest.mark.parametrize("change", [
    {"schema_version": 2}, {"timezone": None}, {"timezone": "Not/AZone"}, {"timezone": "../../etc"},
    {"rows": {}}, {"rows": [{"day": "2026-13-01"}]}, {"codex_limits": []},
    {"coverage": [{"day": "2026-10-30", "provider": "other", "wolt": "alpha", "sources": []}]},
])
def test_an_invalid_native_history_is_refused_and_the_writer_stays_off(root, change):
    source, _ = native(root, **change)
    before = source.read_bytes()
    with pytest.raises(history_import.ImportRefused):
        history_import.run(root)
    assert source.read_bytes() == before
    assert not usage_collector.history_path(root).exists()
    assert "import" not in lodge.read_receipt(root)
    saved = run_app(root, "save")
    assert saved.returncode == 3 and "import" in saved.stderr


@pytest.mark.parametrize("bad", [
    {"amount": -1}, {"amount": 1.5}, {"amount": True}, {"metric": "tokens"}, {"provider": "other"},
    {"day": "2026-1-1"}, {"wolt": ""}, {"extra": 1},
])
def test_each_bad_row_is_refused(root, bad):
    row = {"day": "2026-10-30", "provider": "claude", "wolt": "alpha", "session_id": "s1",
           "model": "m", "metric": "output", "amount": 1, **bad}
    native(root, rows=[row])
    with pytest.raises(history_import.ImportRefused):
        history_import.run(root)


def test_a_repeated_row_is_refused(root):
    _, data = native(root)
    native(root, rows=data["rows"] + data["rows"][:1])
    with pytest.raises(history_import.ImportRefused, match="repeats"):
        history_import.run(root)


def test_a_different_app_history_must_be_reconciled_first(root):
    native(root)
    destination = usage_collector.history_path(root)
    destination.parent.mkdir(mode=0o700, parents=True)
    destination.write_text(json.dumps({"schema_version": 1, "timezone": "Asia/Tokyo", "rows": []}))
    before = destination.read_bytes()
    with pytest.raises(history_import.ImportRefused, match="reconcile"):
        history_import.run(root)
    assert destination.read_bytes() == before and "import" not in lodge.read_receipt(root)
    retried = run_app(root, "import", "--skip")  # the human chose to keep the app's history as it is
    assert retried.returncode == 0 and json.loads(retried.stdout) == {"status": "skipped_by_human"}
    assert destination.read_bytes() == before


def test_no_native_history_means_a_fresh_start(root):
    printed = run_app(root, "import")
    assert printed.returncode == 0 and json.loads(printed.stdout) == {"status": "no_native_history"}
    assert run_app(root, "save").returncode == 0


def test_the_snapshot_waits_for_a_native_save_in_progress(root):
    source, data = native(root)
    lock = source.with_name("history.json.lock").open("rb")
    fcntl.flock(lock, fcntl.LOCK_EX)  # the lodge's own nightly save is writing
    finished = []
    worker = threading.Thread(target=lambda: finished.append(history_import.run(root)))
    worker.start()
    time.sleep(0.3)
    assert not finished and not usage_collector.history_path(root).exists()
    rewritten = dict(data, rows=data["rows"][:1])
    source.write_text(json.dumps(rewritten))  # the save completes with new content
    lock.close()
    worker.join(10)
    assert finished and finished[0]["rows"] == 1
    assert json.loads(usage_collector.history_path(root).read_text()) == rewritten
