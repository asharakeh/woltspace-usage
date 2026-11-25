"""The app's dashboard server and command: fresh, single-flight, read-only, private."""

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from usage_support import claude, count, emit, host, request, save, wolt

ROOT = Path(__file__).resolve().parents[1]
import server  # noqa: E402
import usage_collector  # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy


class Running:
    """The app's handler on a loopback port, counting through `work`."""

    def __init__(self, work):
        self.work = work
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(server.SingleFlight(self.count)))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def count(self):
        return self.work()

    def get(self, path, method="GET"):
        url = f"http://127.0.0.1:{self.httpd.server_address[1]}{path}"
        try:
            with OPENER.open(urllib.request.Request(url, method=method), timeout=20) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def json(self, path):
        status, _, body = self.get(path)
        return status, json.loads(body)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def lodge(tmp_path):
    transcript = emit(claude(tmp_path), request("one"))
    return tmp_path, transcript


@pytest.fixture
def app(lodge):
    tmp_path, _ = lodge
    running = Running(lambda: count(tmp_path, bases=host(tmp_path)))
    yield running
    running.close()


def test_data_is_counted_fresh_for_every_request_and_never_cached(lodge, app):
    tmp_path, transcript = lodge
    save(tmp_path)
    history = [(path.read_bytes(), path.stat().st_mtime_ns)
               for path in (tmp_path / "history.json", tmp_path / "history.json.lock")]
    status, headers, body = app.get("/api/data")
    first = json.loads(body)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert first["periods"]["all_time"]["totals"]["input_fresh"] == 5
    emit(transcript, request("one"), request("two", fresh=7))
    _, second = app.json("/api/data")
    assert second["periods"]["all_time"]["totals"]["input_fresh"] == 12
    assert second["last_collected"] > first["last_collected"]
    assert [(path.read_bytes(), path.stat().st_mtime_ns)
            for path in (tmp_path / "history.json", tmp_path / "history.json.lock")] == history


def test_concurrent_requests_share_one_count_and_a_later_one_counts_again():
    calls, release = [], threading.Event()

    def slow_count():
        calls.append(1)
        release.wait(10)
        return {"count": len(calls)}

    flight = server.SingleFlight(slow_count)
    results = []
    threads = [threading.Thread(target=lambda: results.append(flight())) for _ in range(5)]
    for thread in threads:
        thread.start()
    while not calls:
        threading.Event().wait(0.01)
    threading.Event().wait(0.1)  # the other four are waiting on the one flight
    release.set()
    for thread in threads:
        thread.join(10)
    assert results == [{"count": 1}] * 5 and flight() == {"count": 2}


def test_a_bad_query_is_refused_before_any_count(tmp_path):
    def unexpected():
        raise AssertionError("an invalid query started a count")

    running = Running(unexpected)
    try:
        for query, error in (("by=bogus", "invalid by"), ("wolt=..%2Falpha", "invalid wolt"),
                             ("by=wolt&by=day", "repeated parameter"), ("secret=1", "unknown parameter")):
            status, headers, body = running.get(f"/api/usage?{query}")
            assert status == 400 and json.loads(body) == {"error": error}
            assert headers["Cache-Control"] == "no-store"
        for query in ("period=-1", "metric=nope", "provider=other", "calendar_wolt=../bad",
                      "period=7&period=90", "secret=1"):
            assert running.get(f"/api/data?view=compact&{query}")[0] == 400
    finally:
        running.close()


def test_failures_are_fixed_codes_without_details(capfd):
    def broken():
        raise OSError("/private/path/history.json")

    def other_zone():
        raise usage_collector.HistoryZoneMismatch("history days are in Zone/A, the lodge is in Zone/B")

    for failing, code in ((broken, "count_failed"), (other_zone, "history_zone_mismatch")):
        running = Running(failing)
        try:
            for path in ("/api/data", "/api/usage?by=wolt"):
                status, body = running.json(path)
                assert status == 503 and body == {"error": code}
        finally:
            running.close()
    log = capfd.readouterr().err
    assert "/private" not in log and "Zone/" not in log and "OSError" in log


def test_queries_answer_in_each_format(app):
    for fmt, media in (("json", "application/json"), ("table", "text/plain"), ("csv", "text/csv")):
        status, headers, _ = app.get(f"/api/usage?by=wolt&format={fmt}")
        assert status == 200 and headers["Content-Type"].startswith(media)
        assert headers["Cache-Control"] == "no-store"
    assert app.json("/api/usage?by=wolt")[1]["rows"][0]["wolt"] == "alpha"
    assert app.json("/api/usage?wolt=nobody")[1] == {"error": "unknown or ambiguous wolt"}


def test_only_the_page_and_its_endpoints_are_served(lodge, app):
    tmp_path, _ = lodge
    save(tmp_path)
    page = (ROOT / "static/index.html").read_bytes()
    for path in ("/", "/index.html"):
        status, headers, body = app.get(path)
        assert status == 200 and body == page and headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert app.get("/recount.js")[2] == (ROOT / "static/recount.js").read_bytes()
    for path in ("/history.json", "/static/index.html", "/app.py", "/usage/lodge.py", "/../history.json",
                 "/%2e%2e/history.json", "/api/data/../../app.py", "/.space/woltspace-usage/install.json"):
        status, body = app.json(path)
        assert status == 404 and body == {"error": "not_found"}
    assert app.get("/api/data", method="HEAD")[0] == 405


def test_operator_sessions_never_reach_the_api(tmp_path):
    alpha = wolt(tmp_path, "alpha")
    bases = host(tmp_path)
    emit(bases["claude"] / "-home-op/op.jsonl", request("op1", cwd=tmp_path / "home", model="op-model"))
    emit(bases["claude"] / "-wolts-alpha/a.jsonl", request("a1", cwd=alpha))
    running = Running(lambda: count(tmp_path, bases=bases))
    try:
        for path in ("/api/data", "/api/data?view=compact", "/api/usage?by=session,model&format=csv",
                     "/api/usage?command=limits"):
            body = running.get(path)[2].decode()
            assert "op-model" not in body and "-home-op" not in body
    finally:
        running.close()


def test_compact_scopes_and_totals(lodge):
    tmp_path, _ = lodge
    emit(claude(tmp_path, name="beta"), request("two", fresh=9))
    now = datetime.fromisoformat("2026-11-24T12:00:00+00:00")
    running = Running(lambda: count(tmp_path, now=now, bases=host(tmp_path)))
    try:
        data = running.json("/api/data")[1]
        status, headers, body = running.get("/api/data?view=compact&period=0&calendar_wolt=beta&metric=input_fresh")
        assert status == 200 and headers["Cache-Control"] == "no-store"
        compact = json.loads(body)
        assert "activity" not in compact and len(compact["top"]) == 2
        assert compact["calendar"]["days"][0]["sessions"] == 1
        assert compact["sessions"] == 2 and compact["top"][0]["wolt"] == "beta"
        assert compact["totals"] == data["periods"]["all_time"]["totals"]
    finally:
        running.close()


def test_the_command_and_the_dashboard_agree(tmp_path):
    """`app.py` prints what the endpoint returns, from the same kind of fresh count."""
    root = tmp_path / "wolts"
    (root / ".space").mkdir(parents=True)
    emit(claude(tmp_path), request("one"), request("two", fresh=9))
    emit(claude(tmp_path, name="beta"), request("three", fresh=4))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "TZ": "Europe/Berlin"}

    def printed(*args):
        return json.loads(subprocess.run(
            [sys.executable, "-E", "-s", str(ROOT / "app.py"), "--wolts", str(root), *args, "--format", "json"],
            capture_output=True, text=True, env=env, check=True).stdout)

    running = Running(lambda: usage_collector.count(root, bases=host(tmp_path)))
    try:
        table, summary = running.json("/api/usage?by=wolt")[1], running.json("/api/usage?command=summary")[1]
    finally:
        running.close()
    assert printed("table", "--by", "wolt")["rows"] == table["rows"]
    assert printed("summary")["totals"] == summary["totals"] == {
        "input_fresh": 18, "cache_read": 9, "cache_write": 6, "output": 21, "sessions": 2}
    assert {row["wolt"]: row["input_fresh"] for row in table["rows"]} == {"alpha": 14, "beta": 4}


def test_the_server_binds_to_loopback_unless_told_otherwise():
    import app
    args = app.parser().parse_args(["serve", "--port", "4075"])
    assert args.host == "127.0.0.1"
    manifest = json.loads((ROOT / "woltspace.json").read_text())
    assert manifest["public"] is False and manifest["name"] == "woltspace-usage"
    assert manifest["start"] == "exec python3 -E -s app.py serve --port $PORT"


def test_only_loopback_host_names_are_answered(app):
    port = app.httpd.server_address[1]
    url = f"http://127.0.0.1:{port}"
    for name, status in (("localhost:7777", 200), (f"127.0.0.1:{port}", 200), ("woltspace-usage.localhost:7777", 200),
                         ("attacker.example", 421), ("localhost.attacker.example:7777", 421), ("", 421)):
        request = urllib.request.Request(url + "/api/data", headers={"Host": name})
        try:
            with OPENER.open(request, timeout=20) as response:
                got, body = response.status, response.read()
        except urllib.error.HTTPError as error:
            got, body = error.code, error.read()
        assert got == status, name
        if status == 421:
            assert json.loads(body) == {"error": "unknown_host"} and b"input_fresh" not in body
