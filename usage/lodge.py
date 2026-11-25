"""What the app knows about the lodge it is installed in, through supported interfaces only.

- The data root (`wolts_dir`): `--wolts`, else `WOLTSPACE_WOLTS_DIR`, else the
  folder this checkout is installed under (`<wolts_dir>/apps/<app>`).
- The app's private state: `<wolts_dir>/.space/woltspace-usage/` (history, the
  latest Claude limits reading and the installation receipt), outside the checkout
  and inside the lodge's backups.
- The lodge's local zone, found the way the lodge's scheduler finds it.
- Session metadata from `woltspace session list --json`, read-only.
"""

import fcntl
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

APP = "woltspace-usage"
HERE = Path(__file__).resolve().parent.parent
SESSION_LIST_TIMEOUT = 20


class NotALodge(ValueError):
    """The folder given as the lodge's data root does not look like one."""


class BadReceipt(ValueError):
    """The installation receipt is not what install wrote; nothing relying on it should proceed."""


def wolts_dir(explicit=None, environ=None, checkout=HERE):
    """The lodge's data root, resolved and checked (it holds `.space/`)."""
    environ = os.environ if environ is None else environ
    if explicit:
        candidate = Path(explicit)
    elif environ.get("WOLTSPACE_WOLTS_DIR"):
        candidate = Path(environ["WOLTSPACE_WOLTS_DIR"])
    elif checkout.parent.name == "apps":
        candidate = checkout.parent.parent
    else:
        raise NotALodge("no lodge found: pass --wolts or set WOLTSPACE_WOLTS_DIR")
    candidate = candidate.expanduser()
    if not candidate.is_absolute() or not (candidate / ".space").is_dir():
        raise NotALodge("not a lodge data root (no .space folder)")
    return Path(os.path.realpath(candidate))


def state_dir(root):
    return Path(root) / ".space" / APP


def local_zone_name(environ=None):
    """The lodge machine's zone as an IANA name when it can be found, else its abbreviation."""
    environ = os.environ if environ is None else environ
    tz = (environ.get("TZ") or "").lstrip(":")
    if "/" in tz and not tz.startswith("/"):
        return tz
    target = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in target:
        return target.split("zoneinfo/", 1)[1]
    try:
        name = Path("/etc/timezone").read_text().strip()
        if name:
            return name
    except OSError:
        pass
    return datetime.now().astimezone().tzname() or "local"


def session_records(root, run=subprocess.run):
    """{engine session id: wolt} from `woltspace session list --json`; {} when it is unavailable.

    Only the two fields attribution needs are kept, and only in memory.
    """
    env = dict(os.environ, WOLTSPACE_WOLTS_DIR=str(root))
    try:
        result = run(["woltspace", "session", "list", "--json"], capture_output=True,
                     timeout=SESSION_LIST_TIMEOUT, env=env, stdin=subprocess.DEVNULL)
        records = json.loads(result.stdout) if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if not isinstance(records, list):
        return {}
    found = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        session, wolt = record.get("harness_session_id"), record.get("wolt")
        if isinstance(session, str) and session and isinstance(wolt, str) and wolt:
            found[session.lower()] = wolt
    return found


# ── The installation receipt: what install changed, so update and uninstall touch only that ──

def receipt_path(root):
    return state_dir(root) / "install.json"


def read_receipt(root):
    try:
        data = json.loads(receipt_path(root).read_bytes())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise BadReceipt("unreadable installation receipt")
    return data


def update_receipt(root, change):
    """Apply `change(receipt)` under the receipt's own lock and replace the file atomically."""
    from usage_collector import write_json
    path = receipt_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(path.with_name("install.lock"), os.O_RDWR | os.O_CREAT, 0o600), "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = read_receipt(root)
        change(receipt)
        receipt["schema_version"] = 1
        write_json(path, receipt, 0o600, sync=True)
        return receipt
