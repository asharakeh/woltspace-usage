"""Process-local parse records, replayed through the normal counting/dedup readers.

Readers treat events as read-only. Keep only the fields those readers consume,
not conversation content. Parse errors retain their position in the event stream.
The server serializes counts; no cache is used by CLI counts or nightly saves.
"""
from pathlib import Path


def _stamp(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev


def _event(event):
    result = {key: event[key] for key in ('timestamp', 'type', 'cwd', 'message', 'payload') if key in event}
    for key, fields in (('message', ('id', 'model', 'usage')),
                        ('payload', ('type', 'info', 'rate_limits', 'id', 'session_id', 'model', 'cwd'))):
        if isinstance(result.get(key), dict):
            result[key] = {name: result[key][name] for name in fields if name in result[key]}
    return result


class TranscriptCache:
    def __init__(self):
        self.files = {}
        self.used = set()
        self.headers = {}

    def begin(self):
        self.used.clear()

    def end(self):
        self.files = {path: entry for path, entry in self.files.items() if path in self.used}
        self.headers = {path: entry for path, entry in self.headers.items() if path in self.used}

    def header(self, path, parse):
        path = Path(path)
        self.used.add(path)
        try:
            stamp = _stamp(path)
            cached = self.headers.get(path)
            if cached is not None and cached[0] == stamp:
                return cached[1]
            self.headers.pop(path, None)
            events = [_event(event) for event in parse(path)]
            if _stamp(path) == stamp:
                self.headers[path] = stamp, events
            return events
        except OSError:
            self.headers.pop(path, None)
            return parse(path)

    def read(self, path, gap, parse, keeps):
        path = Path(path)
        self.used.add(path)
        try:
            stamp = _stamp(path)
        except OSError:
            self.files.pop(path, None)
            yield from parse(path, gap)
            return
        cached = self.files.get(path)
        if cached is None or cached[0] != stamp:
            self.files.pop(path, None)
            records = []
            for event in parse(path, lambda reason, day: records.append((None, (reason, day)))):
                if keeps(event):
                    records.append((_event(event), None))
            try:
                stable = _stamp(path) == stamp
            except OSError:
                stable = False
            if stable and not any(error and error[0] == 'transcript_unreadable' for _, error in records):
                self.files[path] = stamp, records
        else:
            records = cached[1]
        for event, error in records:
            if error:
                gap(*error)
            else:
                yield event
