# Notice

Parts of this app are taken from [Woltspace](https://github.com/jerpint/woltspace)
(MIT License, Copyright (c) 2026 jerpint), as proposed in
[jerpint/woltspace#684](https://github.com/jerpint/woltspace/pull/684) at commit
`3d555ad6761bafa33e9af370ad0f4488a0df31fe`:

| Here | Woltspace |
|------|-----------|
| `usage/usage_collector.py` | `container/lib/usage_collector.py` |
| `usage/usage_claude.py` | `container/lib/usage_claude.py` |
| `usage/usage_codex.py` | `container/lib/usage_codex.py` |
| `usage/usage_query.py` | `container/lib/usage_query.py` |
| `usage/usage_view.py` | `server/usage_view.py` |
| `usage/claude_statusline.py` | `container/lib/claude_statusline.py` |
| `static/index.html` | `public/usage.html` |
| `static/recount.js` | `public/static/usage-recount.js` |
| `skills/lodge-usage/SKILL.md` | `container/skills/usage/SKILL.md` |
| `tests/` (the lifted tests) | `test/test_usage_*.py`, `test/test_claude_statusline.py`, `test/usage-recount.test.mjs`, `test/usage_support.py` |

They are changed here to run as a standalone app: the state folder, the lodge
discovery and the Claude limits setup differ. `git log` shows each change. The
MIT License in `LICENSE` covers the whole app.
