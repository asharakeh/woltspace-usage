# Notice

Parts of this app are taken from [Woltspace](https://github.com/jerpint/woltspace)
(MIT License, Copyright (c) 2026 jerpint): the lodge usage feature proposed in
[jerpint/woltspace#684](https://github.com/jerpint/woltspace/pull/684). The
files below are identical to that pull request's commit
`3d555ad6761bafa33e9af370ad0f4488a0df31fe` and to the Woltspace build
`bdd88521560ab55e53012a0e8fc3b86f9e69c04d` in the
[asharakeh/woltspace](https://github.com/asharakeh/woltspace) fork:

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
discovery, the page's paths and the Claude limits setup differ, and the test
fixtures use other time zones. Compare any of them with its source to see the
changes. The MIT License in `LICENSE` covers the whole app.
