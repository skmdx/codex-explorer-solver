#!/usr/bin/env python3
"""Deterministic source inspection with paginated search. No model calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from evidence import EvidenceError, compact_json, source_bytes

DIRECT_BYTES = 12000


def corpus(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    if not paths:
        raise EvidenceError("provide explicit source paths")
    items = []
    for path in dict.fromkeys(paths):  # Preserve caller priority.
        raw, lines, _ = source_bytes(root, path)
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
    else:
        route = 'literal_search_then_reader_if_semantic_lookup_needed'
    return {'route': route, 'purpose': purpose, 'source_bytes': count,
            'bytes_are_not_tokens': True,
            'files': [{k:v for k,v in item.items() if k != 'lines'} for item in items]}


def search(root: Path, paths: list[str], literal: str, *, offset: int = 0,
           max_results: int = 20, expected_snapshot: str | None = None) -> dict[str, Any]:
    if not literal or '\n' in literal:
        raise EvidenceError('literal must be nonempty and contain no LF')
    if max_results < 1 or offset < 0:
        raise EvidenceError('invalid pagination bounds')
    items = corpus(root, paths)
    manifest = [{k:v for k,v in item.items() if k != 'lines'} for item in items]
    fingerprint = hashlib.sha256(compact_json({'manifest':manifest,'literal':literal}).encode()).hexdigest()
    if expected_snapshot is not None and fingerprint != expected_snapshot:
        raise EvidenceError('stale_search_snapshot: restart the search; offsets may have shifted')
    # Deliberately return references only. No long/minified match lines enter context.
    hits = []
    total = 0
    for item in items:
        for n, line in enumerate(item['lines'], 1):
            if literal not in line:
                continue
            if total >= offset and len(hits) < max_results:
                hits.append(dict(path=item['path'], start=n, end=n, sha256=item['sha256']))
            total += 1
    next_offset = offset + len(hits) if offset + len(hits) < total else None
    return {'kind':'literal_search', 'snapshot_sha256':fingerprint,
            'scope':'only_explicit_paths', 'scanned_files':len(items),
            'source_bytes_scanned':sum(x['byte_count'] for x in items),
            'match_unit':'lines_containing_literal', 'total_matches_in_scope':total,
            'offset':offset, 'next_offset':next_offset, 'complete_page':next_offset is None,
            'repository_wide_absence_proven':False, 'locations':hits}


def reader_snapshot(root: Path, paths: list[str]) -> tuple[str, list[dict[str, Any]]]:
    items = corpus(root, paths)
    manifest = [{k:v for k,v in item.items() if k != 'lines'} for item in items]
    # Built on disk/in-process; the parent need not read or paste this corpus.
    sources = [dict(path=x['path'], sha256=x['sha256'],
                    source='\n'.join(f'{i}: {s}' for i,s in enumerate(x['lines'],1))) for x in items]
    text = compact_json({'kind':'untrusted_source_snapshot','files':sources})
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
