"""One-time import of a lodge's native usage history into the app's own history.

A Woltspace lodge that already counts usage keeps its history at
`<wolts_dir>/.space/usage/history.json`, saved nightly under a sibling lock. The
app never writes that file. Before its first save the app takes a consistent
copy under that lock (shared), validates it, seeds its own empty history from it
atomically, reads the result back and compares it with the copy. The source is
left exactly as it was.

The outcome is recorded in the installation receipt. `app.py save` refuses to run
until one is recorded, so a failed or conflicting import is reconciled before the
app's writer starts. A repeat install keeps whatever the app already saved.
"""

import fcntl
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import lodge
from usage_collector import (
    DIMENSIONS,
    HISTORY_SCHEMA,
    METRICS,
    READERS,
    codex_reading,
    history_path,
)


class ImportRefused(ValueError):
    """The native history cannot be imported as it is. Messages are safe to print."""


def native_history(root):
    return Path(root) / ".space" / "usage" / "history.json"


def snapshot(source):
    """The source's bytes, read under a shared hold of its save lock; None when there is no source."""
    try:
        lock = source.with_name(source.name + ".lock").open("rb")
    except FileNotFoundError:
        lock = None
    try:
        if lock:
            fcntl.flock(lock, fcntl.LOCK_SH)  # waits while the lodge's own save holds it
        try:
            return source.read_bytes()
        except FileNotFoundError:
            return None
    finally:
        if lock:
            lock.close()


def _valid_day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate(data):
    """Per-day totals of a well-formed history; ImportRefused naming the first problem otherwise."""
    if not isinstance(data, dict) or data.get("schema_version") != HISTORY_SCHEMA:
        raise ImportRefused("unrecognized history schema")
    rows, coverage = data.get("rows", []), data.get("coverage", [])
    try:
        ZoneInfo(data.get("timezone"))
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise ImportRefused("history has no valid recorded zone") from None
    if not isinstance(rows, list) or not isinstance(coverage, list):
        raise ImportRefused("history rows or coverage are not lists")
    totals, keys = {}, set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {*DIMENSIONS, "amount"}:
            raise ImportRefused("a history row has unexpected fields")
        if not all(isinstance(row[name], str) and row[name] for name in DIMENSIONS):
            raise ImportRefused("a history row has an empty or non-text field")
        if (not _valid_day(row["day"]) or row["provider"] not in READERS
                or row["metric"] not in METRICS):
            raise ImportRefused("a history row has an invalid day, provider or metric")
        if type(row["amount"]) is not int or row["amount"] < 0:
            raise ImportRefused("a history row has an invalid amount")
        key = tuple(row[name] for name in DIMENSIONS)
        if key in keys:
            raise ImportRefused("history repeats a row")
        keys.add(key)
        totals[row["day"], row["metric"]] = totals.get((row["day"], row["metric"]), 0) + row["amount"]
    for entry in coverage:
        if (not isinstance(entry, dict) or not _valid_day(entry.get("day"))
                or entry.get("provider") not in READERS or not isinstance(entry.get("wolt"), str)
                or not isinstance(entry.get("sources"), list)
                or not all(isinstance(source, str) for source in entry["sources"])):
            raise ImportRefused("a coverage entry is malformed")
    if data.get("codex_limits") is not None and codex_reading(data["codex_limits"]) is None:
        raise ImportRefused("saved Codex limits are malformed")
    return totals


def _seed(destination, raw):
    """Create `destination` from `raw` (0600, synced) only if it does not exist yet."""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.import-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)  # fails if the destination exists: never clobbers
    finally:
        temporary.unlink(missing_ok=True)


def run(root, skip=False):
    """Import once and record the outcome. Returns the recorded decision."""
    root = Path(root)
    decided = lodge.read_receipt(root).get("import")
    if decided:
        return decided  # a repeat install keeps the app's own history
    destination = history_path(root)
    lock_path = destination.with_name(destination.name + ".lock")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600), "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # the app's own save lock: no app save meanwhile
        raw = snapshot(native_history(root))
        if raw is None:
            decision = {"status": "no_native_history"}
        elif skip:
            decision = {"status": "skipped_by_human"}
        else:
            decision = _import(raw, destination)
    lodge.update_receipt(root, lambda receipt: receipt.__setitem__("import", decision))
    return decision


def _import(raw, destination):
    try:
        data = json.loads(raw)
    except ValueError:
        raise ImportRefused("native history is not valid JSON") from None
    totals = validate(data)
    created = False
    if destination.exists():
        if json.loads(destination.read_bytes()) != data:
            raise ImportRefused("the app already has a different history; reconcile it first")
    else:
        _seed(destination, raw)
        created = True
    copied = json.loads(destination.read_bytes())
    if copied != data or validate(copied) != totals:
        if created:
            destination.unlink()
        raise ImportRefused("the imported history does not match the snapshot")
    days = sorted({day for day, _ in totals})
    return {
        "status": "imported", "source_sha256": hashlib.sha256(raw).hexdigest(),
        "rows": len(data.get("rows", [])), "days": len(days), "timezone": data["timezone"],
        "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
    }
