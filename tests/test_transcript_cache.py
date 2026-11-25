"""Server-only parsed transcripts: reuse, invalidate, prune, preserve cold-count semantics."""
from datetime import UTC, datetime
import json
import usage_collector as uc
from usage_support import SID, ZONE, claude, codex, emit, host, no_host, request, token

NOW = datetime(2026, 11, 25, tzinfo=UTC)


def counted(tmp, cache, bases=None):
    return uc.count(tmp/'wolts', tmp/'history.json', tmp/'limits.json', zone=ZONE,
                    now=NOW, bases=bases or no_host(tmp), _cache=cache)


def test_unchanged_transcript_is_parsed_once(tmp_path, monkeypatch):
    path = emit(claude(tmp_path), request('one'))
    reads, original = [], uc.jsonl
    def parse(path, gap):
        reads.append(path)
        yield from original(path, gap)
    monkeypatch.setattr(uc, 'jsonl', parse)
    cache = uc.TranscriptCache()
    first = counted(tmp_path, cache)
    assert counted(tmp_path, cache) == first and reads == [path]


def test_appended_transcript_is_reread(tmp_path, monkeypatch):
    path = emit(claude(tmp_path), request('one'))
    cache = uc.TranscriptCache()
    counted(tmp_path, cache)
    reads, original = [], uc.jsonl
    def parse(path, gap):
        reads.append(path)
        yield from original(path, gap)
    monkeypatch.setattr(uc, 'jsonl', parse)
    with path.open('a') as out:
        out.write(json.dumps(request('two'))+'\n')
    warm = counted(tmp_path, cache)
    assert reads == [path]
    assert warm == counted(tmp_path, None)
    assert warm['periods']['all_time']['totals']['output'] == 14


def test_removed_transcript_is_dropped(tmp_path):
    first = emit(claude(tmp_path), request('one'))
    emit(claude(tmp_path, suffix='-other'), request('two'))
    cache = uc.TranscriptCache()
    counted(tmp_path, cache)
    first.unlink()
    warm = counted(tmp_path, cache)
    assert first not in cache.files
    assert warm == counted(tmp_path, None)
    assert warm['periods']['all_time']['totals']['output'] == 7


def test_warm_equals_cold_with_shared_copies_dedup_errors_and_limits(tmp_path, monkeypatch):
    main = emit(claude(tmp_path), request('same', fresh=2))
    emit(main.with_suffix('')/'subagents/a.jsonl', request('same', fresh=8), request('other'))
    shared = host(tmp_path)
    # Same relative path, shared/own Claude copies: maxima must still be applied anew.
    emit(shared['claude']/main.relative_to(tmp_path/'wolts/alpha/.claude/projects'),
         request('same', fresh=12, cwd=tmp_path/'wolts/alpha'))
    delta = {'input_tokens':10, 'cached_input_tokens':4, 'output_tokens':8,
             'reasoning_output_tokens':0, 'total_tokens':18}
    cx = emit(codex(tmp_path), {'type':'session_meta','payload':{'id':SID, 'model':'codex-a'}},
              token(delta, delta, limits={'primary':{'used_percent':20,'window_minutes':300,'resets_at':2000000000}}))
    emit(shared['codex']/cx.relative_to(tmp_path/'wolts/alpha/.codex/sessions'),
         {'type':'session_meta','payload':{'id':SID,'model':'codex-a','cwd':str(tmp_path/'wolts/alpha')}}, token(delta,delta, limits={'primary':{'used_percent':20,'window_minutes':300,'resets_at':2000000000}}))
    with main.open('a') as out:
        out.write('{bad}\n'+json.dumps({'message':['bad shape']})+'\n{later bad}\n')
    cache = uc.TranscriptCache()
    cold = counted(tmp_path, None, shared)
    assert counted(tmp_path, cache, shared) == cold
    def no_parse(*args, **kwargs):
        raise AssertionError("unchanged body/header must not be parsed again")
    monkeypatch.setattr(uc, "jsonl", no_parse)
    monkeypatch.setattr(uc, "first_events", no_parse)
    assert counted(tmp_path, cache, shared) == cold  # seen/metrics must not accumulate in cached events
    assert cold['periods']['all_time']['totals']['input_fresh'] == 23
    assert cold['limits']['codex']['windows']['primary']['used_percent'] == 20


def test_cache_retains_reader_events_and_ordered_errors_only(tmp_path):
    ca = emit(claude(tmp_path), {'type':'user', 'message':{'content':'dummy text'}},
              request('one'), {'message':['invalid shape']}, request('unreachable'))
    cx = emit(codex(tmp_path), {'type':'session_meta', 'payload':{'id':SID}},
              {'type':'response_item', 'payload':{'type':'message', 'content':['dummy text']}},
              {'type':'turn_context', 'payload':{'model':'codex-a'}},
              token({'input_tokens':3}, {'input_tokens':3}),
              {'type':'event_msg', 'payload':{'type':'other'}},
              {'type':'event_msg', 'payload':['invalid shape']})
    cache = uc.TranscriptCache()
    cold = counted(tmp_path, None)
    assert counted(tmp_path, cache) == counted(tmp_path, cache) == cold
    assert len(cache.files[ca][1]) == 3  # usage, invalid shape, later usage
    assert len(cache.files[cx][1]) == 4  # metadata, model, usage, invalid shape


def test_empty_identity_header_is_reused(tmp_path, monkeypatch):
    path = emit(codex(tmp_path))
    cache = uc.TranscriptCache()
    first = counted(tmp_path, cache)
    def no_parse(*args, **kwargs):
        raise AssertionError('empty unchanged header must be cached')
    monkeypatch.setattr(uc, 'first_events', no_parse)
    assert counted(tmp_path, cache) == first
    assert cache.headers[path][1] == []
