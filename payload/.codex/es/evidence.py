#!/usr/bin/env python3
"""Decode source files and verify cited ranges against their original bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from charset_normalizer import from_bytes

TOP_KEYS = {"version", "status", "primary", "related", "unresolved"}
SITE_KEYS = {"path", "start", "end", "sha256", "encoding", "symbol", "evidence"}
STATUSES = {"ready", "partial", "not_found", "blocked"}


class EvidenceError(ValueError):
    """A reference cannot safely be accepted as current source evidence."""


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_handoff(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda x: (_ for _ in ()).throw(
                              EvidenceError(f"invalid JSON constant: {x}")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvidenceError(f"invalid JSON: {exc}") from exc
    validate_shape(data)
    data['status'] = collection_status(data)
    return data


def _string(value: Any, name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise EvidenceError(f"invalid {name}: expected {'a string' if empty else 'a nonempty string'}")
    return value


def validate_shape(data: Any) -> None:
    if not isinstance(data, dict) or set(data) != TOP_KEYS:
        raise EvidenceError(f"handoff fields must be exactly {sorted(TOP_KEYS)}")
    if type(data["version"]) is not int or data["version"] != 3:
        raise EvidenceError("unsupported handoff version")
    if not isinstance(data["status"], str) or data["status"] not in STATUSES:
        raise EvidenceError("invalid status")
    for category in ("primary", "related"):
        locations = data[category]
        if not isinstance(locations, list):
            raise EvidenceError(f"{category} must be an array")
        for item in locations:
            if not isinstance(item, dict) or set(item) != SITE_KEYS:
                raise EvidenceError(f"location fields must be exactly {sorted(SITE_KEYS)}")
            _string(item["path"], "path")
            _string(item["symbol"], "symbol", empty=True)
            _string(item["evidence"], "evidence")
            _string(item["encoding"], "encoding")
            digest = item["sha256"]
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise EvidenceError("sha256 must be the 64-character lowercase file digest")
            start, end = item["start"], item["end"]
            if type(start) is not int or type(end) is not int:
                raise EvidenceError("line numbers must be integers, not booleans")
            if start < 1 or end < start:
                raise EvidenceError("range must be 1-based and inclusive")
    unresolved = data["unresolved"]
    if not isinstance(unresolved, list):
        raise EvidenceError("unresolved must be an array")
    for value in unresolved:
        _string(value, "unresolved")


def collection_status(data: dict[str, Any]) -> str:
    if data['primary'] or data['related']:
        return 'partial' if data['unresolved'] else 'ready'
    return 'blocked' if data['status'] == 'blocked' else 'not_found'


def source_path(root: Path, relative: str) -> Path:
    _string(relative, "path")
    resolved = (root / relative).resolve(strict=True)
    if not resolved.is_file():
        raise EvidenceError(f"not a regular file: {relative}")
    return resolved


def source_bytes(root: Path, relative: str, encoding: str | None = None) -> tuple[bytes, list[str], str]:
    p = source_path(root, relative)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise EvidenceError(f"cannot read {relative}: {exc}") from exc
    if encoding is None:
        try:
            raw.decode('utf-8')
            encoding = 'utf-8'
        except UnicodeDecodeError:
            match = from_bytes(raw).best()
            if match is None:
                raise EvidenceError(f"text encoding could not be detected: {relative}")
            encoding = match.encoding
    try:
        text = raw.decode(encoding)
    except (UnicodeError, LookupError) as exc:
        raise EvidenceError(f"cannot decode {relative} as {encoding}: {exc}; specify its source encoding") from exc
    if "\x00" in text:
        raise EvidenceError("binary source is unsupported")
    # Split on LF, not Unicode splitlines(), so numbers agree with common source tools.
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    if not text:
        lines = []
    # CRLF is normalized only for display; SHA-256 always covers the original bytes.
    return raw, [line[:-1] if line.endswith("\r") else line for line in lines], encoding


def read_range(root: Path, relative: str, start: int, end: int,
               expected: str | None = None, *, encoding: str | None = None) -> dict[str, Any]:
    raw, lines, encoding = source_bytes(root, relative, encoding)
    return dict(_render_range(relative, lines, hashlib.sha256(raw).hexdigest(), start, end, expected),
                encoding=encoding)


def _render_range(relative: str, lines: list[str], digest: str, start: int, end: int,
                  expected: str | None) -> dict[str, Any]:
    _check_range(relative, lines, digest, start, end, expected)
    return {"path": relative, "start": start, "end": end, "sha256": digest,
            "source": "\n".join(f"{i}: {lines[i-1]}" for i in range(start, end + 1))}


def _check_range(relative: str, lines: list[str], digest: str, start: int, end: int,
                 expected: str | None) -> None:
    if type(start) is not int or type(end) is not int or not (1 <= start <= end):
        raise EvidenceError("invalid 1-based inclusive range")
    if expected is not None and digest != expected:
        raise EvidenceError(f"stale_source: {relative}; re-localize the symbol in this file")
    if end > len(lines):
        raise EvidenceError(f"range exceeds {len(lines)} lines in {relative}")


def _exact_lines(text: str) -> list[str]:
    lines = text.split('\n')
    return [line+'\n' for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])


def verify_handoff(root: Path, data: dict[str, Any], *, include_source: bool = False) -> dict[str, Any]:
    validate_shape(data)
    sources: dict[tuple[str, str], tuple[list[str], str, list[str]]] = {}
    result: dict[str, Any] = {"ok": True, "status": collection_status(data),
        "locations": len(data["primary"]) + len(data["related"]),
        "semantic_relevance_verified": False}
    for category in ("primary", "related"):
        excerpts = []
        for item in data[category]:
            path = item["path"]
            key = (path, item["encoding"])
            if key not in sources:
                raw, lines, _ = source_bytes(root, *key)
                exact_lines = _exact_lines(raw.decode(item['encoding'])) if include_source else []
                sources[key] = (lines, hashlib.sha256(raw).hexdigest(), exact_lines)
            lines, digest, exact_lines = sources[key]
            _check_range(path, lines, digest, item["start"], item["end"], item["sha256"])
            if include_source:
                excerpts.append(dict(item, source=''.join(exact_lines[item['start']-1:item['end']])))
        if include_source:
            result[category] = excerpts
    if include_source:
        result.update(unresolved=data["unresolved"])
    return result


def evidence_blocks(data: dict[str, Any], read_ranges: list[dict] | None = None) -> list[dict]:
    """Merge overlapping excerpts and subtract already delivered lines of this version."""
    files: dict[tuple[str, str, str], dict[int, str]] = {}
    for category in ("primary", "related"):
        for item in data[category]:
            key = (item['path'], item['sha256'], item['encoding'])
            entry = files.setdefault(key, {})
            entry.update(enumerate(_exact_lines(item['source']), item['start']))
    blocks = []
    for (path, digest, encoding), entry in files.items():
        known = [r for r in read_ranges or [] if (r['path'], r['sha256'], r['encoding']) == (path, digest, encoding)]
        block = None
        for number, line in sorted(entry.items()):
            if any(r['start'] <= number <= r['end'] for r in known):
                continue
            if block is None or number != block['end'] + 1:
                block = dict(path=path, sha256=digest, encoding=encoding,
                             start=number, end=number, source=[line])
                blocks.append(block)
            else:
                block['end'] = number
                block['source'].append(line)
    for block in blocks:
        block['source'] = ''.join(block['source'])
    return blocks


def format_evidence(data: dict[str, Any], blocks: list[dict] | None = None) -> str:
    """Keep range labels outside unchanged, contiguous source blocks."""
    if blocks is None:
        blocks = evidence_blocks(data)
    facts = dict.fromkeys(f"{item['path']}:{item['start']}-{item['end']} {item['symbol']}: {item['evidence']}"
                         for category in ('primary', 'related') for item in data[category])
    sections = list(facts)
    sections.extend(f"Source {block['path']}:{block['start']}-{block['end']}\n{block['source']}" for block in blocks)
    if data.get('unresolved'):
        sections.append('Unresolved:\n' + '\n'.join(data['unresolved']))
    return '\n\n'.join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    read = sub.add_parser("read", help="read exact source with a machine-computed SHA-256")
    read.add_argument("--root", type=Path, default=Path.cwd())
    read.add_argument("--path", required=True)
    read.add_argument("--start", type=int, required=True)
    read.add_argument("--end", type=int, required=True)
    read.add_argument("--expect-sha256")
    read.add_argument("--encoding", help="override automatic source encoding detection")
    for action, help_text in (("check", "validate a report without printing source"),
                              ("show", "validate all references and return exact numbered source")):
        report = sub.add_parser(action, help=help_text)
        report.add_argument("--root", type=Path, default=Path.cwd())
        report.add_argument("--handoff", required=True, help="JSON file, or - for stdin")
    args = parser.parse_args()
    try:
        if args.action == "read":
            result = read_range(args.root, args.path, args.start, args.end, args.expect_sha256, encoding=args.encoding)
        else:
            if args.handoff == "-":
                raw = sys.stdin.buffer.read()
            else:
                with Path(args.handoff).open("rb") as stream:
                    raw = stream.read()
            result = verify_handoff(args.root, load_handoff(raw), include_source=args.action == "show")
        print(compact_json(result))
        return 0
    except (EvidenceError, OSError) as exc:
        print(compact_json({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
