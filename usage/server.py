"""The private dashboard: the page, a fresh count per request, and read-only queries.

Standard library only. The server answers exactly these paths and reads nothing
from disk by request path, so history, the receipt and the code are never served:

  GET /, /index.html   the page (numbers appear only after a fresh count)
  GET /recount.js      the page's recount timer
  GET /api/data        one fresh count: the dashboard JSON, or with `view=compact`
                       the page's filtered projection
  GET /api/usage       a query (`command=summary|table|limits`, the CLI's options)
                       as JSON, CSV or text

Requests must name a loopback host (see loopback_host). A count reads transcripts and saved history and writes nothing. One count runs at
a time; requests that arrive during it share it, and a later request counts again.
Errors are fixed codes that never echo the request or the reason a count failed.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qsl

import lodge
import usage_collector
import usage_query
import usage_view

PAGES = {
    "/": ("static/index.html", "text/html; charset=utf-8"),
    "/index.html": ("static/index.html", "text/html; charset=utf-8"),
    "/recount.js": ("static/recount.js", "text/javascript; charset=utf-8"),
}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
    ),
}


LOOPBACK_NAMES = {"localhost", "127.0.0.1", "[::1]"}


def loopback_host(header):
    """Whether a Host header names this machine's loopback, as the lodge's proxy sends it.

    The lodge reaches the app as `localhost:<port>` (it rewrites the Host header) or
    directly on 127.0.0.1. Any other name is refused, so a web page that rebinds
    its own domain to 127.0.0.1 cannot read the numbers.
    """
    if not header or any(ch.isspace() for ch in header):
        return False
    name = header.rsplit(":", 1)[0] if not header.endswith("]") else header
    if header.startswith("[") and "]:" in header:
        name = header.split("]:", 1)[0] + "]"
    name = name.lower()
    return name in LOOPBACK_NAMES or (name.endswith(".localhost") and len(name) > len(".localhost"))


class QueryParams:
    """The small part of a query-string mapping the compact view validates against."""

    def __init__(self, raw):
        self.items = parse_qsl(raw, keep_blank_values=True)
        self.values = dict(self.items)

    def __contains__(self, key):
        return key in self.values

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def multi_items(self):
        return list(self.items)


class SingleFlight:
    """Run `work` at most once at a time; callers that arrive during a run share its result.

    A call that arrives after a run finished starts a new run, so no result is
    ever reused from an earlier, finished count.
    """

    def __init__(self, work):
        self.work = work
        self.lock = Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="count")
        self.flight = None

    def __call__(self):
        with self.lock:
            if self.flight is None or self.flight.done():
                self.flight = self.pool.submit(self.work)
            flight = self.flight
        return flight.result()  # the count's own error, re-raised for every caller


def make_handler(count):
    class Handler(BaseHTTPRequestHandler):
        server_version = lodge.APP
        sys_version = ""

        def send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def fail(self, status, code):
            self.send(status, json.dumps({"error": code}).encode(), "application/json")

        def counted(self):
            try:
                return count()
            except usage_collector.HistoryZoneMismatch:
                print(f"{lodge.APP}: history was saved in another zone; not re-bucketed",
                      file=sys.stderr, flush=True)
                self.fail(503, "history_zone_mismatch")
            except Exception as error:  # noqa: BLE001 - any failure is a fixed 503; the log gets its type only
                print(f"{lodge.APP}: count failed: {type(error).__name__}", file=sys.stderr, flush=True)
                self.fail(503, "count_failed")
            return None

        def do_GET(self):
            if not loopback_host(self.headers.get("Host")):
                return self.fail(421, "unknown_host")
            path, _, raw_query = self.path.partition("?")
            if path == "/api/data":
                try:
                    options = usage_view.filters(QueryParams(raw_query))
                except ValueError as error:
                    return self.fail(400, str(error))
                data = self.counted()
                if data is None:
                    return None
                body = usage_view.project(data, options) if options else data
                return self.send(200, json.dumps(body, allow_nan=False, separators=(",", ":")).encode(),
                                 "application/json")
            if path == "/api/usage":
                try:
                    request = usage_query.http_options(raw_query)
                except usage_query.BadQuery as error:
                    return self.fail(400, str(error))
                data = self.counted()
                if data is None:
                    return None
                try:
                    result = usage_query.execute(data, request)
                except usage_query.BadQuery as error:
                    return self.fail(400, str(error))
                body, content_type = usage_query.format_result(result, request.output_format)
                return self.send(200, body.encode(), content_type)
            if path in PAGES:
                name, content_type = PAGES[path]
                return self.send(200, (lodge.HERE / name).read_bytes(), content_type)
            return self.fail(404, "not_found")

        def do_HEAD(self):
            if not loopback_host(self.headers.get("Host")):
                return self.fail(421, "unknown_host")
            path = self.path.split("?", 1)[0]
            if path in PAGES:
                return self.do_GET()
            if path in ("/api/data", "/api/usage"):
                return self.fail(405, "use_get")
            return self.fail(404, "not_found")

        def log_message(self, format, *args):  # noqa: A002 - the base class's own parameter name
            pass  # no request log: paths and clients stay out of the app log

    return Handler


def serve(root, host, port):
    counting = SingleFlight(lambda: usage_collector.count(root))
    server = ThreadingHTTPServer((host, port), make_handler(counting))
    server.daemon_threads = True
    print(f"{lodge.APP}: serving on {host}:{port}", file=sys.stderr, flush=True)
    server.serve_forever()
