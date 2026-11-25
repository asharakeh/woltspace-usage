"""The optional Claude limits setup: one consented `statusLine` entry in a Claude user settings file.

`show` resolves the settings file the keeper wolt's lodge sessions use and prints
the exact change, writing nothing. `apply` makes that change after the human said
yes, and records it in the installation receipt. `remove` takes it out again only
while it is still exactly what was added.

The change is never made when the effective settings already carry a
`statusLine` (a custom one, or one explicitly disabled with null), when any
settings file is unreadable, when the file is a link or belongs to someone else,
or when the helper could not tell lodge sessions from other Claude sessions.
"""

import json
import os
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

import lodge
from claude_statusline import _settings

MANAGED = {
    "linux": Path("/etc/claude-code"),
    "darwin": Path("/Library/Application Support/ClaudeCode"),
}


class NotEligible(ValueError):
    """The change cannot be made safely. Messages are safe to print."""


def helper_command(root, app=lodge.HERE / "app.py", python=sys.executable):
    return shlex.join([python, "-E", "-s", str(app), "--wolts", str(root), "statusline"])


def proposal(root, wolt, environ=None, managed=None):
    """{"settings_file", "change"} for the keeper's lodge sessions; NotEligible with the reason otherwise."""
    env = os.environ if environ is None else environ
    managed = MANAGED.get(sys.platform) if managed is None else managed
    root = Path(root)
    wolt_dir = root / wolt
    if not (wolt_dir / "wolt" / "wolt.json").is_file():
        raise NotEligible(f"{wolt} is not a wolt of this lodge")
    lodge_root = env.get("WOLTSPACE_WOLTS_DIR")
    if (not env.get("WOLTSPACE_WOLT_SESSION") or not lodge_root
            or os.path.realpath(lodge_root) != os.path.realpath(root)):
        raise NotEligible("run this from a lodge session of this lodge, so lodge sessions can be told apart")
    if env.get("WOLTSPACE_ISOLATION", "host") == "host":
        home = Path(env.get("HOME") or Path.home())
    else:  # a container lodge: each wolt with its own .claude folder is its own engine home
        home = wolt_dir if (wolt_dir / ".claude").is_dir() else Path(env.get("HOME") or Path.home())
    engine = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    if not engine.is_absolute():
        raise NotEligible("the Claude settings folder is not an absolute path")
    target = engine / "settings.json"
    project = wolt_dir / ".claude"
    files = [target, project / "settings.json", project / "settings.local.json"]
    if managed is not None:
        files.append(managed / "managed-settings.json")
        try:
            files.extend(sorted(path for path in (managed / "managed-settings.d").iterdir()
                                if path.suffix == ".json"))
        except FileNotFoundError:
            pass
    for path in files:
        try:
            if "statusLine" in _settings(path):
                raise NotEligible(f"{path} already sets a status line; it stays as it is")
        except (OSError, ValueError, RecursionError) as error:
            if isinstance(error, NotEligible):
                raise
            raise NotEligible(f"{path} could not be read; nothing is changed") from None
    _owned(target)
    return {"settings_file": str(target),
            "change": {"statusLine": {"type": "command", "command": helper_command(root)}}}


def _owned(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise NotEligible(f"{path} is a link or not a file; nothing is changed")
    if path.exists() and path.stat().st_uid != os.getuid():
        raise NotEligible(f"{path} belongs to another user; nothing is changed")


def _replace(path, before, settings):
    """Write `settings` over `path` only if it still holds exactly `before` (None: absent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if before is not None else 0o600
    temporary = path.with_name(f".{path.name}.woltspace-usage-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(settings, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            current = path.read_bytes()
        except FileNotFoundError:
            current = None
        if current != before:
            raise NotEligible(f"{path} changed meanwhile; nothing is changed, try again")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path):
    try:
        before = path.read_bytes()
    except FileNotFoundError:
        return None, {}
    try:
        return before, _settings(path, missing=False)
    except (OSError, ValueError, RecursionError):
        raise NotEligible(f"{path} could not be read; nothing is changed") from None


def apply(root, wolt, environ=None, managed=None):
    """Make the proposed change (after the human's yes) and record it. Returns the proposal."""
    if lodge.read_receipt(root).get("claude_limits"):
        raise NotEligible("the Claude limits change is already recorded; remove it first")
    planned = proposal(root, wolt, environ, managed)
    path = Path(planned["settings_file"])
    before, settings = _read(path)
    if "statusLine" in settings:
        raise NotEligible(f"{path} already sets a status line; it stays as it is")
    _replace(path, before, {**settings, **planned["change"]})
    lodge.update_receipt(root, lambda receipt: receipt.__setitem__("claude_limits", {
        "settings_file": str(path), "statusLine": planned["change"]["statusLine"],
        "applied_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }))
    return planned


def remove(root):
    """Take the recorded entry out if it is unchanged. Returns "removed", "kept" or "none"."""
    recorded = lodge.read_receipt(root).get("claude_limits")
    if not recorded:
        return "none"
    path = Path(recorded["settings_file"])
    outcome = "kept"
    if not path.is_symlink():
        before, settings = _read(path)
        if settings.get("statusLine") == recorded["statusLine"]:
            del settings["statusLine"]
            _replace(path, before, settings)
            outcome = "removed"
    lodge.update_receipt(root, lambda receipt: receipt.pop("claude_limits", None))
    return outcome
