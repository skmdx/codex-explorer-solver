#!/usr/bin/env python3
"""Inspect archived hook output without re-running commands or flooding the context."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from hook_store import Store, StoreError, canonical
from hook_runtime import response_parts

MAX_OUTPUT_BYTES = 8192


def streams_for(store: Store, aid: str) -> dict[str, str]:
    parts = response_parts(store.load(aid))
    if parts is None:
        raise StoreError('unsupported archived response')
    return parts[0]


def read_lines(store: Store, aid: str, field: str, start: int, end: int) -> dict:
    streams = streams_for(store, aid)
    if field not in streams:
        raise StoreError('unknown field; run info first')
    lines = streams[field].splitlines(keepends=True)
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines) or end-start >= 160:
        raise StoreError('range must be 1-based, inclusive, within the log and at most 160 lines')
    result = dict(artifact_id=aid, field=field, start=start, end=end, source=''.join(lines[start-1:end]),
                  historical_tool_output=True, not_current_repository_source=True)
    if len(canonical(result)) > MAX_OUTPUT_BYTES:
        raise StoreError('selected lines exceed output budget; select fewer lines or use slice for a giant line')
    return result


def byte_slice(store: Store, aid: str, field: str, offset: int, length: int) -> dict:
    streams = streams_for(store, aid)
    if field not in streams:
        raise StoreError('unknown field')
    raw = streams[field].encode('utf-8')
    if type(offset) is not int or type(length) is not int or offset < 0 or not 1 <= length <= 3072 or offset >= len(raw):
        raise StoreError('slice needs a valid offset and length in 1..3072')
    try:
        text = raw[offset:offset+length].decode('utf-8')
    except UnicodeError:
        raise StoreError('slice splits a UTF-8 character; adjust offset/length') from None
    return dict(artifact_id=aid, field=field, start_byte=offset,
                end_byte_exclusive=min(offset+length, len(raw)), source=text,
                partial=offset != 0 or offset+length < len(raw))


def search(store: Store, aid: str, field: str, literal: str, limit: int) -> dict:
    streams = streams_for(store, aid)
    if field not in streams or not isinstance(literal, str) or not literal or len(literal)>256 or not 1<=limit<=100:
        raise StoreError('provide a valid field, nonempty literal <=256 characters and limit in 1..100')
    found = [n for n, line in enumerate(streams[field].splitlines(), 1) if literal in line]
    return dict(artifact_id=aid, field=field, line_numbers=found[:limit], total_matching_lines=len(found),
                partial=len(found)>limit, scope='this saved tool output only', source_text_returned=False)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path.cwd())
    p.add_argument('--state-dir', type=Path, required=True)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('report')
    ls = sub.add_parser('list'); ls.add_argument('--limit', type=int, default=5)
    for name in ('info', 'read', 'slice', 'search'):
        sp = sub.add_parser(name); sp.add_argument('--id', required=True)
        if name != 'info': sp.add_argument('--field', required=True)
        if name == 'read':
            sp.add_argument('--start', type=int, required=True); sp.add_argument('--end', type=int, required=True)
        if name == 'slice':
            sp.add_argument('--offset', type=int, required=True); sp.add_argument('--length', type=int, required=True)
        if name == 'search':
            sp.add_argument('--literal', required=True); sp.add_argument('--limit', type=int, default=20)
    a = p.parse_args()
    try:
        store = Store(a.state_dir, a.root)
        if a.cmd == 'report': result = store.report()
        elif a.cmd == 'list': result = {'recent': store.recent(limit=a.limit)}
        elif a.cmd == 'read': result = read_lines(store, a.id, a.field, a.start, a.end)
        elif a.cmd == 'slice': result = byte_slice(store, a.id, a.field, a.offset, a.length)
        elif a.cmd == 'search': result = search(store, a.id, a.field, a.literal, a.limit)
        else:
            response = store.load(a.id); parts = response_parts(response)
            if parts is None: raise StoreError('unsupported archive')
            streams, status = parts
            result = dict(artifact_id=a.id, status=status,
                          fields={k: {'bytes': len(v.encode('utf-8')), 'lines': len(v.splitlines())} for k,v in streams.items()},
                          scope='exact hook-observed JSON, upstream completeness unknown')
        raw = canonical(result)
        if len(raw) > MAX_OUTPUT_BYTES: raise StoreError('result exceeds output budget')
        print(raw.decode('utf-8')); return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr); return 2

if __name__ == '__main__':
    raise SystemExit(main())
