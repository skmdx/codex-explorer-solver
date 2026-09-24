#!/usr/bin/env python3
"""Deterministic, bounded source inspection. No model calls, no semantic summaries.

A bounded result never proves repository-wide absence. Byte limits are NOT tokens.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from evidence import EvidenceError, compact_json, source_bytes

MAX_PATHS = 12
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 6144
READER_MAX_BYTES = 64 * 1024
DIRECT_BYTES = 12000


def corpus(root: Path, paths: list[str], limit: int = MAX_SCAN_BYTES) -> list[dict[str, Any]]:
    if not paths or len(paths) > MAX_PATHS or len(set(paths)) != len(paths):
        raise EvidenceError(f"provide 1..{MAX_PATHS} distinct, explicit source paths")
    items = []
    total = 0
    for path in paths:  # Preserve caller priority, not lexicographic pseudo-ranking.
        raw, lines = source_bytes(root, path)
        total += len(raw)
        if total > limit:
            raise EvidenceError(f"selected corpus exceeds {limit} bytes; narrow it (not truncated)")
        items.append(dict(path=path, sha256=hashlib.sha256(raw).hexdigest(),
                          byte_count=len(raw), line_count=len(lines), lines=lines))
    return items


def inspect(root: Path, paths: list[str], purpose: str) -> dict[str, Any]:
    items = corpus(root, paths)
    count = sum(item['byte_count'] for item in items)
    # This is a conservative routing hint, not an economic optimizer.
    if purpose in {'edit', 'reason', 'generate'}:
        route = 'solver_exact_source'
    elif count <= DIRECT_BYTES:
        route = 'direct_or_literal_search'
    elif count <= READER_MAX_BYTES:
        route = 'literal_search_then_reader_if_semantic_lookup_needed'
    else:
        route = 'narrow_corpus_before_reader'
    return {'route': route, 'purpose': purpose, 'source_bytes': count,
            'bytes_are_not_tokens': True,
            'files': [{k:v for k,v in item.items() if k != 'lines'} for item in items]}


def search(root: Path, paths: list[str], literal: str, *, offset: int = 0,
           max_results: int = 20, expected_snapshot: str | None = None) -> dict[str, Any]:
    if not literal or len(literal.encode('utf-8')) > 1000 or '\n' in literal:
        raise EvidenceError('literal must be nonempty, <=1000 UTF-8 bytes, and contain no LF')
    if not 1 <= max_results <= 50 or not 0 <= offset <= 1000000:
        raise EvidenceError('invalid pagination bounds')
    items = corpus(root, paths)
    manifest = [{k:v for k,v in item.items() if k != 'lines'} for item in items]
    fingerprint = hashlib.sha256(compact_json({'manifest':manifest,'literal':literal}).encode()).hexdigest()
    if expected_snapshot is not None and fingerprint != expected_snapshot:
        raise EvidenceError('stale_search_snapshot: restart the search; offsets may have shifted')
    # Deliberately return references only. No long/minified match lines enter context.
    hits = []
    total = 0
    byte_limited = False
    for item in items:
        for n, line in enumerate(item['lines'], 1):
            if literal not in line:
                continue
            if total >= offset and len(hits) < max_results and not byte_limited:
                hit = dict(path=item['path'], start=n, end=n, sha256=item['sha256'])
                if len(compact_json(hits + [hit]).encode()) > MAX_RESULT_BYTES - 1200:
                    byte_limited = True
                else:
                    hits.append(hit)
            total += 1
    if offset > total:
        raise EvidenceError('offset exceeds total matches in this snapshot')
    next_offset = offset + len(hits) if offset + len(hits) < total else None
    return {'kind':'literal_search', 'snapshot_sha256':fingerprint,
            'scope':'only_explicit_paths', 'scanned_files':len(items),
            'source_bytes_scanned':sum(x['byte_count'] for x in items),
            'match_unit':'lines_containing_literal', 'total_matches_in_scope':total,
            'offset':offset, 'next_offset':next_offset, 'complete_page':next_offset is None,
            'repository_wide_absence_proven':False, 'locations':hits}


def reader_snapshot(root: Path, paths: list[str]) -> tuple[str, list[dict[str, Any]]]:
    items = corpus(root, paths, READER_MAX_BYTES)
    manifest = [{k:v for k,v in item.items() if k != 'lines'} for item in items]
    # Built on disk/in-process; the parent need not read or paste this corpus.
    sources = [dict(path=x['path'], sha256=x['sha256'],
                    source='\n'.join(f'{i}: {s}' for i,s in enumerate(x['lines'],1))) for x in items]
    text = compact_json({'kind':'untrusted_source_snapshot','files':sources})
    if len(text.encode()) > READER_MAX_BYTES * 2:
        raise EvidenceError('numbered/escaped snapshot too large; narrow selected files')
    return text, manifest


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest='command', required=True)
    for name in ('inspect','search'):
        q = s.add_parser(name)
        q.add_argument('--root', type=Path, default=Path.cwd())
        q.add_argument('--path', action='append', required=True)
        if name == 'inspect':
            q.add_argument('--purpose', choices=['lookup','edit','reason','generate'], default='lookup')
        else:
            q.add_argument('--literal', required=True)
            q.add_argument('--offset', type=int, default=0)
            q.add_argument('--max-results', type=int, default=20)
            q.add_argument('--expect-snapshot')
    a = p.parse_args()
    try:
        result = inspect(a.root,a.path,a.purpose) if a.command == 'inspect' else search(
            a.root,a.path,a.literal,offset=a.offset,max_results=a.max_results,
            expected_snapshot=a.expect_snapshot)
        print(compact_json(result)); return 0
    except (EvidenceError,OSError) as exc:
        print(compact_json({'ok':False,'error':str(exc)}),file=sys.stderr); return 2
if __name__ == '__main__':
    raise SystemExit(main())
