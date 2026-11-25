"""The Claude limits status-line helper: a silent writer of the latest plan-limit reading.

Claude Code runs it with the status-line JSON on stdin. It prints nothing and always
exits 0. It keeps only validated five-hour and seven-day windows and the time it
read them, and discards everything else it is given.
"""

from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
import sys
import time

import lodge
from usage_collector import claude_limits_path, limit_window, timestamp, write_json

WINDOWS = {"five_hour": 300, "seven_day": 10080}
INPUT_LIMIT = 128 * 1024
LOCK_SECONDS = 0.2
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate settings key")
        result[key] = value
    return result


def _settings(path, *, missing=True):
    try:
        data = json.loads(path.read_bytes(), object_pairs_hook=_object)
    except FileNotFoundError:
        if missing and not path.is_symlink():
            return {}
        raise
    if not isinstance(data, dict):
        raise ValueError("settings must be an object")
    return data


def recording(root, environ=None):
    """Whether this status-line call may record a reading for the lodge at `root`.

    Only after the human agreed to the settings change (the receipt holds it), and
    only from a lodge session of this lodge: a shared user configuration also runs
    the helper in the operator's own Claude sessions, and those are never recorded.
    """
    env = os.environ if environ is None else environ
    try:
        if not lodge.read_receipt(root).get("claude_limits"):
            return False
    except (OSError, ValueError, RecursionError):
        return False
    lodge_root = env.get("WOLTSPACE_WOLTS_DIR")
    if not env.get("WOLTSPACE_WOLT_SESSION") or not lodge_root:
        return False
    return os.path.realpath(lodge_root) == os.path.realpath(root)


def publish(root, read_at, data, environ=None):
    """Publish strictly newer readings while holding a stable, bounded lock."""
    if not isinstance(data, dict) or not isinstance(data.get("rate_limits"), dict):
        return
    windows = {}
    for key, minutes in WINDOWS.items():
        window = limit_window(data["rate_limits"].get(key), "used_percentage", minutes)
        if window and window["used_percent"] <= 100 and type(window["resets_at"]) is int:
            windows[key] = {"used_percentage": window["used_percent"], "resets_at": window["resets_at"]}
    if not windows or not recording(root, environ):
        return
    path = claude_limits_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        return
    deadline = time.monotonic() + LOCK_SECONDS
    fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.01)
        if not recording(root, environ):
            return
        try:
            current = _settings(path)
        except (ValueError, RecursionError):
            current = {}
        observed = timestamp(current.get("read_at"))
        # A delayed writer's own timestamp cannot establish clock skew.
        too_far_ahead = datetime.now(timezone.utc) + MAX_CLOCK_SKEW
        if observed is None or read_at > observed or observed > too_far_ahead:
            write_json(path, {"read_at": read_at.isoformat(), "rate_limits": windows}, 0o600, sync=True)


def main(root):
    read_at = datetime.now(timezone.utc)
    try:
        raw = sys.stdin.buffer.read(INPUT_LIMIT + 1)
        if len(raw) <= INPUT_LIMIT:
            publish(root, read_at, json.loads(raw))
    except (OSError, ValueError, RecursionError, IndexError, OverflowError):
        pass
