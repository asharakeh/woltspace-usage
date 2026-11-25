# woltspace-usage 🦉

How many tokens your lodge uses: per wolt, session, provider and model, per day,
with your Claude and Codex plan limits. A private
[Woltspace](https://github.com/jerpint/woltspace) app with a dashboard, a command
and a nightly save, counted fresh from the engines' own transcripts.

## Setup

**Ask your wolt to install it.** Say: *"Install the woltspace-usage app from
https://github.com/asharakeh/woltspace-usage"*. Your wolt follows
[AGENTS.md](AGENTS.md): it puts the app in your lodge's Apps list, imports any
usage history your lodge already kept, schedules the nightly save and adds a skill
so every wolt can answer usage questions. Claude plan limits are enabled by
default when the settings are eligible. Your wolt reports the exact change. To
turn off the built-in Claude feed, ask your wolt to run `app.py limits-setup remove`.

Updating and removing work the same way: ask your wolt.

## What you get

- **A dashboard** in the lodge's Apps list: totals, a daily chart per provider,
  the top wolts, models and sessions, and a year-long activity calendar. It counts
  fresh when you open it and every 2 minutes while you look at it, and never shows
  numbers older than 150 seconds.
- **A command** for wolts and humans: `app.py summary`, `app.py table --by
  day,provider --since 14d`, `app.py limits`, as a table, JSON or CSV.
- **History that outlives the transcripts.** The engines delete old transcripts;
  a nightly save at 23:55 lodge time keeps each day's numbers in
  `<wolts_dir>/.space/woltspace-usage/`, which your lodge's backups include.
- **Plan limits.** Codex limits come from its transcripts. Claude limits need one
  status-line entry in Claude's settings. Install adds it when the settings
  are eligible and preserves any existing status line, including a disabled one.
  Uninstall removes the app's entry if it is still unchanged. On a container lodge the entry goes
  into the keeper wolt's settings, so the reading updates when the keeper uses
  Claude; the dashboard shows when it was read.

## Good to know

- Days are calendar days in your lodge's time zone, fixed when history starts.
- A day whose transcripts were deleted before a save, or could not be read, is
  marked incomplete and keeps the highest numbers seen for it.
- On a native lodge, a session you start yourself in a wolt's folder counts for
  that wolt. Your own sessions elsewhere are never read past their first lines.
- Usage numbers describe your spending. The dashboard is private to your lodge
  (`"public": false`); nothing is sent anywhere.

## For contributors

Standard library only (Python 3.11+). The page is `static/`, the counting is
`usage/`, the entry point is `app.py`. Tests: `pytest` with the `test` extra
(`uv sync --extra test && uv run pytest`), plus `node --test tests/*.mjs` for the
page's recount timer. Tests use neutral fixtures only.

Parts of the counting, the page and the tests come from Woltspace itself; see
[NOTICE.md](NOTICE.md). MIT License.
