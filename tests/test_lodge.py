"""Lodge discovery through supported interfaces only: the data root, the zone, session records."""

import json
import os
import subprocess
from pathlib import Path

import lodge
import pytest


def make_root(path):
    (path / ".space").mkdir(parents=True)
    return path


def test_the_data_root_comes_from_the_flag_then_the_environment_then_the_install_location(tmp_path):
    flag, env_root = make_root(tmp_path / "flag"), make_root(tmp_path / "env")
    installed = make_root(tmp_path / "lodge")
    checkout = installed / "apps" / "woltspace-usage"
    checkout.mkdir(parents=True)
    environ = {"WOLTSPACE_WOLTS_DIR": str(env_root)}
    assert lodge.wolts_dir(str(flag), environ, checkout) == flag
    assert lodge.wolts_dir(None, environ, checkout) == env_root
    assert lodge.wolts_dir(None, {}, checkout) == installed
    with pytest.raises(lodge.NotALodge):
        lodge.wolts_dir(None, {}, tmp_path / "somewhere-else")


@pytest.mark.parametrize("candidate", ["relative/path", "missing", "no-space"])
def test_a_folder_that_is_not_a_lodge_is_refused(tmp_path, candidate):
    (tmp_path / "no-space").mkdir()
    value = candidate if candidate == "relative/path" else str(tmp_path / candidate)
    with pytest.raises(lodge.NotALodge):
        lodge.wolts_dir(value, {}, tmp_path)


def test_a_linked_data_root_resolves_to_its_real_folder(tmp_path):
    real = make_root(tmp_path / "disk/lodge")
    (tmp_path / "link").symlink_to(real)
    assert lodge.wolts_dir(str(tmp_path / "link"), {}, tmp_path) == real


def test_an_iana_tz_setting_names_the_zone():
    assert lodge.local_zone_name({"TZ": ":Asia/Tokyo"}) == "Asia/Tokyo"
    assert lodge.local_zone_name({"TZ": "Europe/Berlin"}) == "Europe/Berlin"
    assert lodge.local_zone_name({"TZ": "/etc/zone-file"}) != "/etc/zone-file"  # a file path is not a name


def recorder(stdout, code=0):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]["WOLTSPACE_WOLTS_DIR"], kwargs["timeout"]))
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=b"")

    return run, calls


def test_session_records_keep_only_the_engine_id_and_wolt(tmp_path):
    records = [{"name": "a-1", "wolt": "alpha", "harness_session_id": "ABC", "prompt": "private", "chat_id": 1},
               {"name": "b-1", "wolt": "beta", "harness_session_id": None},
               {"name": "c-1", "wolt": None, "harness_session_id": "def"}, [], "x"]
    run, calls = recorder(json.dumps(records).encode())
    assert lodge.session_records(tmp_path, run=run) == {"abc": "alpha"}
    assert calls == [(["woltspace", "session", "list", "--json"], str(tmp_path), lodge.SESSION_LIST_TIMEOUT)]


@pytest.mark.parametrize("stdout, code", [(b"[]", 1), (b"not json", 0), (b'{"a": 1}', 0), (b"", 0)])
def test_unusable_session_records_mean_none(tmp_path, stdout, code):
    run, _ = recorder(stdout, code)
    assert lodge.session_records(tmp_path, run=run) == {}


@pytest.mark.parametrize("error", [FileNotFoundError, subprocess.TimeoutExpired])
def test_a_missing_or_stuck_lodge_cli_means_no_records(tmp_path, error):
    def run(argv, **kwargs):
        raise error(argv, 1) if error is subprocess.TimeoutExpired else error("woltspace")

    assert lodge.session_records(tmp_path, run=run) == {}


def test_the_real_call_goes_through_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "woltspace"
    fake.write_text('#!/bin/sh\nprintf \'[{"wolt": "alpha", "harness_session_id": "%s"}]\' "$*"\n')
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin:/bin")
    assert lodge.session_records(tmp_path) == {"session list --json": "alpha"}


def test_the_receipt_is_owner_only_and_updated_atomically(tmp_path):
    root = make_root(tmp_path / "lodge")
    lodge.update_receipt(root, lambda receipt: receipt.update(keeper="alpha", port=4075))
    lodge.update_receipt(root, lambda receipt: receipt.update(skill="lodge-usage"))
    assert lodge.read_receipt(root) == {"keeper": "alpha", "port": 4075, "skill": "lodge-usage",
                                        "schema_version": 1}
    path = Path(lodge.receipt_path(root))
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
