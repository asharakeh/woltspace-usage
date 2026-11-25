# Installing woltspace-usage (for the wolt doing it)

Your human asked you to install, update or remove this app. Follow the steps in
order and stop at the first one that fails: tell your human what failed, in plain
words, and wait. Never guess a path, a port or a name.

`<WOLTS>` is the lodge's data root and `<APP>` is `<WOLTS>/apps/woltspace-usage`.
`<KEEPER>` is the wolt that owns the app and its nightly job: you, unless your
human names another wolt. Run every `app.py` command as written here
(`python3 -E -s <APP>/app.py ...`), with the real paths filled in.

## Install

1. **Find the lodge.** `<WOLTS>` is the `wolts_dir` that `woltspace paths` prints
   (an older lodge without that command: `$WOLTSPACE_WOLTS_DIR`). It holds `.space/`.
2. **Check for collisions.**
   - `<APP>` must not exist yet. If it does and is not this app, stop and ask.
   - Port: 4075 if it is free, else another free port in 4000-5999. A port is
     taken when another app's `woltspace.json` names it
     (`curl -s http://localhost:${WOLTSPACE_PORT:-7777}/apps`) or something
     already listens on it.
3. **Get the app.**
   `git clone https://github.com/asharakeh/woltspace-usage <APP>`
4. **Record the keeper and port.** This fills in the manifest's keeper and port,
   which registers the app with the lodge as a private app (`"public": false`):
   `python3 -E -s <APP>/app.py configure --keeper <KEEPER> --port <PORT>`
5. **Import the lodge's existing usage history, once.**
   `python3 -E -s <APP>/app.py import`
   - A lodge with built-in usage keeps `<WOLTS>/.space/usage/history.json`. The app
     copies it under that file's lock, checks it, and writes its own copy. The
     original is never changed.
   - If it is refused, show your human the reason. Either the history gets fixed
     and you run `import` again, or your human decides to start without it:
     `import --skip`. The app will not save anything until one of these succeeded.
6. **Schedule the nightly save** as `<KEEPER>`'s job, at 23:55 lodge time:

   ```bash
   woltspace wolf add --wolt <KEEPER> --name usage-save --cron '55 23 * * *' <<'WOLF_MSG'
   Run `python3 -E -s <APP>/app.py save` (woltspace-usage nightly save). If it exits non-zero, tell your human what it printed. Do nothing else.
   WOLF_MSG
   ```

   If `<KEEPER>` already has a job named `usage-save` that is not this one, use
   `woltspace-usage-save` instead. Then record the name you used:
   `python3 -E -s <APP>/app.py configure --schedule <NAME>`
   Check it: `woltspace wolf list --wolt <KEEPER>` shows the job at 23:55 and the
   lodge's zone. Then run `python3 -E -s <APP>/app.py save` twice; both must print
   `{"saved": true, ...}`, and the second must not change any day's numbers.
   The wolf catches up a missed run once, within 24 hours.
7. **Install the lodge skill** so every wolt can answer usage questions:
   `python3 -E -s <APP>/app.py skill install`
   If it says `lodge-usage` already exists and is not this app's, keep theirs and
   use `--name lodge-usage-app`. Wolts see the skill from their next session.
8. **Start and check the app.**
   - `curl -s -X POST http://localhost:${WOLTSPACE_PORT:-7777}/apps/woltspace-usage/start`
   - `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<PORT>/` prints `200`,
     and `python3 -E -s <APP>/app.py summary --since 7d` prints totals.
   - Show your human the page: `push-view http://woltspace-usage.localhost:${WOLTSPACE_PORT:-7777}/`.
     The numbers appear after "Counting…". Ask whether they look right.
9. **Offer Claude plan limits (ask first).**
   `python3 -E -s <APP>/app.py limits-setup show`
   - This prints the Claude settings file and the exact JSON it would add, and
     changes nothing. Show both to your human, verbatim, and ask: *"May I add this
     status line so the dashboard can show your Claude plan limits?"*
   - Only on a clear yes: `python3 -E -s <APP>/app.py limits-setup apply --yes`.
     On a no, skip it: usage counting works without it.
   - If `show` says the limits are unavailable (a status line already exists,
     including a disabled one, or a file cannot be read, or this is not a lodge
     session), tell your human that and why, and leave everything as it is.
   - The helper records readings only from this lodge's sessions, and keeps only
     the plan-limit windows and when they were read.
10. **Tell your human** what was installed: the port, the keeper, the job name,
    the skill name, whether the history was imported, and whether Claude limits
    are on. `python3 -E -s <APP>/app.py status` prints all of it.

## Update

Ask your human first. Updates keep the history, keeper, port, job, skill name and
the Claude limits choice.

```bash
cd <APP>
git checkout -- woltspace.json         # the template comes back; configure re-applies it
git pull --ff-only
python3 -E -s <APP>/app.py configure   # keeper and port from the receipt
python3 -E -s <APP>/app.py skill install --name <the skill name in `status`>
curl -s -X POST http://localhost:${WOLTSPACE_PORT:-7777}/apps/woltspace-usage/stop
curl -s -X POST http://localhost:${WOLTSPACE_PORT:-7777}/apps/woltspace-usage/start
```

Then repeat the checks in step 8.

## Uninstall

Ask your human first. Remove only what this app added, in this order, using the
names in `python3 -E -s <APP>/app.py status`:

1. **The nightly job:** `woltspace wolf rm <KEEPER> <job name>`, only if its
   message is the one from step 6.
2. **The skill:** `python3 -E -s <APP>/app.py skill remove --name <skill name>`.
   It is left alone if someone else changed or added files in it.
3. **The Claude settings change:** `python3 -E -s <APP>/app.py limits-setup remove`.
   It takes the status line out only if it is still exactly what was added; a
   status line your human changed since stays.
4. **The registration:** stop the app
   (`curl -s -X POST http://localhost:${WOLTSPACE_PORT:-7777}/apps/woltspace-usage/stop`),
   then remove the checkout: `rm -rf <APP>`.

The history in `<WOLTS>/.space/woltspace-usage/` stays, so a reinstall (or a
restore of a lodge backup) continues where it left off. Tell your human it is
there; deleting it is their call.

## What the app reads and writes

- Reads: every wolt's Claude and Codex transcripts; on a native lodge also the
  operator's shared engine homes, counting a transcript only for the wolt whose
  folder it started in, or whose session the lodge recorded
  (`woltspace session list`). The operator's own sessions are never counted.
- Writes only in `<WOLTS>/.space/woltspace-usage/` (history, the latest Claude
  limits reading, the install receipt, their locks; all owner-only), plus the
  manifest's keeper and port in `<APP>/woltspace.json`, the skill folder and, with
  your human's yes, one `statusLine` entry.
- The dashboard is private to the lodge (`"public": false`). Usage numbers
  describe your human's spending: never put them anywhere public without asking.
