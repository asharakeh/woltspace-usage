#!/usr/bin/env python3
"""woltspace-usage: the lodge's LLM token usage, as a private app and a command.

    app.py serve --port N                 the dashboard (the lodge's Apps entry starts it)
    app.py summary|table|limits [...]     a fresh query, printed (no server needed)
    app.py save                           merge a fresh count into the app's history
    app.py import [--skip]                one-time import of a native lodge history
    app.py configure [--keeper W] [--port N] [--schedule NAME]
    app.py skill install|remove [--name lodge-usage]
    app.py limits-setup show|apply|remove [--wolt W] [--yes]
    app.py status                         what install recorded
    app.py statusline                     Claude's status-line helper (silent)

Every command takes `--wolts DIR` (default: `WOLTSPACE_WOLTS_DIR`, else the lodge
this checkout is installed in). Standard library only; see AGENTS.md to install.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "usage"))

QUERY_OPTIONS = ("by", "since", "until", "wolt", "provider", "model", "metric", "top", "format")
SKILL_MARKER = "<!-- installed by woltspace-usage: remove it with `app.py skill remove` -->"


def parser():
    top = argparse.ArgumentParser(prog="app.py", description="Lodge LLM token usage.")
    top.add_argument("--wolts", help="the lodge's data root")
    commands = top.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the private dashboard")
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    for name in ("summary", "table", "limits"):
        query = commands.add_parser(name, help=f"print a fresh {name}")
        for option in QUERY_OPTIONS:
            if name == "table" or option not in ("by", "top"):
                query.add_argument(f"--{option}")
    commands.add_parser("save", help="merge a fresh count into the app's history (the nightly job)")
    imported = commands.add_parser("import", help="import a native lodge usage history once")
    imported.add_argument("--skip", action="store_true", help="start without the native history")
    configure = commands.add_parser("configure", help="set or re-apply the keeper, port and schedule")
    configure.add_argument("--keeper")
    configure.add_argument("--port", type=int)
    configure.add_argument("--schedule", help="the keeper's nightly save job name")
    skill = commands.add_parser("skill", help="install or remove the lodge skill")
    skill.add_argument("action", choices=("install", "remove"))
    skill.add_argument("--name", default="lodge-usage")
    limits = commands.add_parser("limits-setup", help="the optional Claude limits status line")
    limits.add_argument("action", choices=("show", "apply", "remove"))
    limits.add_argument("--wolt", help="the wolt whose Claude settings to use (default: the keeper)")
    limits.add_argument("--yes", action="store_true", help="the human agreed to the shown change")
    commands.add_parser("status", help="print what install recorded")
    commands.add_parser("statusline", help=argparse.SUPPRESS)
    return top


def fail(message, code=2):
    print(f"woltspace-usage: {message}", file=sys.stderr)
    return code


def query(root, args):
    import usage_collector
    import usage_query
    options = {key: value for key in QUERY_OPTIONS if (value := getattr(args, key, None)) is not None}
    try:
        request = usage_query.validate(args.command, options, "table")
    except usage_query.BadQuery as error:
        return fail(str(error))
    try:
        data = usage_collector.count(root)
    except usage_collector.HistoryZoneMismatch:
        return fail("history was saved in another zone; not re-bucketed", 1)
    except Exception as error:  # noqa: BLE001 - say that the count failed, by type, never its details
        return fail(f"count failed ({type(error).__name__})", 1)
    try:
        result = usage_query.execute(data, request)
    except usage_query.BadQuery as error:
        return fail(str(error))
    body, _ = usage_query.format_result(result, request.output_format)
    sys.stdout.write(body)
    return 0


def save(root):
    import lodge
    import usage_collector
    if not lodge.read_receipt(root).get("import"):
        return fail("run `app.py import` first (the native history import is undecided)", 3)
    report = usage_collector.save(root)
    print(json.dumps({"saved": True, "last_collected": report["last_collected"],
                      "history_starts": report["history_starts"], "timezone": report["timezone"]}))
    return 0


def configure(root, args):
    import lodge
    manifest_path = HERE / "woltspace.json"
    receipt = lodge.update_receipt(root, lambda r: r.update(
        {key: value for key, value in (("keeper", args.keeper), ("port", args.port),
                                       ("schedule", args.schedule)) if value is not None}))
    if not receipt.get("keeper") or not receipt.get("port"):
        return fail("configure needs --keeper and --port the first time")
    if not (Path(root) / receipt["keeper"] / "wolt" / "wolt.json").is_file():
        return fail("the keeper is not a wolt of this lodge")
    manifest = json.loads(manifest_path.read_text())
    manifest.update(keeper=receipt["keeper"], port=receipt["port"])
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: receipt.get(key) for key in ("keeper", "port", "schedule")}))
    return 0


def skill(root, args):
    import re
    import shlex
    import shutil

    import lodge
    name = args.name
    if not re.fullmatch(r"lodge-[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
        return fail("a lodge skill name is lodge-<lowercase words joined by dashes>")
    folder = Path(root) / ".space" / "shared-skills" / name
    if args.action == "install":
        if folder.exists() or folder.is_symlink():
            ours = lodge.read_receipt(root).get("skill") == name and SKILL_MARKER in _text(folder / "SKILL.md")
            if not ours:
                return fail(f"{name} already exists and is not this app's; choose another --name", 4)
        command = shlex.join([sys.executable, "-E", "-s", str(HERE / "app.py"), "--wolts", str(root)])
        text = (HERE / "skills/lodge-usage/SKILL.md").read_text()
        text = text.replace("name: lodge-usage\n", f"name: {name}\n", 1).replace("{{APP}}", command)
        head, _, body = text.partition("\n---\n")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(f"{head}\n---\n\n{SKILL_MARKER}\n{body}")
        lodge.update_receipt(root, lambda r: r.__setitem__("skill", name))
        print(json.dumps({"skill": name, "path": str(folder / "SKILL.md")}))
        return 0
    receipt = lodge.read_receipt(root)
    if receipt.get("skill") != name:
        return fail(f"{name} was not installed by this app; left alone", 4)
    if folder.is_dir() and not folder.is_symlink():
        if [path.name for path in folder.iterdir()] == ["SKILL.md"] and SKILL_MARKER in _text(folder / "SKILL.md"):
            shutil.rmtree(folder)
        else:
            return fail(f"{name} holds files this app did not write; left alone", 4)
    lodge.update_receipt(root, lambda r: r.pop("skill", None))
    print(json.dumps({"skill_removed": name}))
    return 0


def _text(path):
    try:
        return path.read_text()
    except (OSError, ValueError):
        return ""


def limits_setup(root, args):
    import limits_setup
    import lodge
    try:
        if args.action == "remove":
            print(json.dumps({"claude_limits": limits_setup.remove(root)}))
            return 0
        wolt = args.wolt or lodge.read_receipt(root).get("keeper")
        if not wolt:
            return fail("name the wolt with --wolt (no keeper recorded yet)")
        if args.action == "show":
            print(json.dumps(limits_setup.proposal(root, wolt), indent=2))
            return 0
        if not args.yes:
            return fail("apply only after the human agreed to the shown change (--yes)")
        print(json.dumps(limits_setup.apply(root, wolt), indent=2))
        return 0
    except limits_setup.NotEligible as error:
        return fail(f"Claude limits unavailable: {error}", 5)


def main(argv=None):
    args = parser().parse_args(argv)
    import lodge
    if args.command == "statusline":  # Claude runs this on every update: silent, always 0
        try:
            root = lodge.wolts_dir(args.wolts)
        except lodge.NotALodge:
            return 0
        import claude_statusline
        claude_statusline.main(root)  # catches its own input and I/O errors
        return 0
    try:
        root = lodge.wolts_dir(args.wolts)
    except lodge.NotALodge as error:
        return fail(str(error))
    if args.command == "serve":
        import server
        server.serve(root, args.host, args.port)
        return 0
    if args.command in ("summary", "table", "limits"):
        return query(root, args)
    if args.command == "save":
        return save(root)
    if args.command == "import":
        import history_import
        try:
            print(json.dumps(history_import.run(root, skip=args.skip)))
        except history_import.ImportRefused as error:
            return fail(f"import refused: {error}", 6)
        return 0
    if args.command == "configure":
        return configure(root, args)
    if args.command == "skill":
        return skill(root, args)
    if args.command == "limits-setup":
        return limits_setup(root, args)
    receipt = lodge.read_receipt(root)
    print(json.dumps({"wolts_dir": str(root), "state": str(lodge.state_dir(root)), **receipt}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
