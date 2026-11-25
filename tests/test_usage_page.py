"""The usage page: served by the app, fed only by fresh counts, with paths relative to the app."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "static/index.html").read_text()


def test_the_page_loads_numbers_only_from_a_fresh_count():
    assert "dashboard.json" not in PAGE
    assert re.findall(r"fetch\('([^']+)'", PAGE) == ["api/data?"]
    assert '<main hidden id="dashboard">' in PAGE
    assert '<script src="recount.js"></script>' in PAGE
    assert (ROOT / "static/recount.js").is_file()


def test_the_page_uses_only_paths_relative_to_the_app():
    """The app is reached at its own port or subdomain, or under /app/<name>/: no absolute links."""
    assert not re.findall(r'(?:href|src)="/', PAGE)
    assert not re.findall(r"fetch\('/", PAGE)
    assert "/api/usage/data" not in PAGE and "/static/" not in PAGE


def test_the_page_follows_the_lodge_zone_not_a_fixed_one():
    assert "America/" not in PAGE and "Europe/" not in PAGE and "setZone(data.timezone)" in PAGE


def test_the_page_has_one_element_per_id():
    ids = re.findall(r'\sid="([^"]+)"', PAGE)
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
