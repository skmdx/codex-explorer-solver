#!/usr/bin/env python3
"""Small, fail-closed source-reference validator. Python 3.11+, standard library.

This is a data-validation helper, NOT a sandbox or a relevance judge.
Only read explicitly requested, UTF-8 source ranges in a trusted repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

MAX_HANDOFF_BYTES = 6144
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_RANGE_LINES = 160
MAX_TOTAL_LINES = 480
MAX_READ_BYTES = 16000
TOP_KEYS = {"version", "status", "stop_reason", "primary", "related", "unresolved"}
SITE_KEYS = {"path", "start", "end", "sha256", "symbol", "evidence"}
STATUSES = {"ready", "partial", "not_found", "blocked"}
STOP_REASONS = {"evidence_ready", "budget", "no_progress", "no_match", "environment"}


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
    # Both raw and canonical sizes are bounded; whitespace cannot hide large output.
    if len(raw) > MAX_HANDOFF_BYTES:
        raise EvidenceError(f"handoff exceeds {MAX_HANDOFF_BYTES} UTF-8 bytes")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda x: (_ for _ in ()).throw(
                              EvidenceError(f"invalid JSON constant: {x}")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvidenceError(f"invalid JSON: {exc}") from exc
    validate_shape(data)
    return data


def _string(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum:
        raise EvidenceError(f"invalid {name}: expected string of length <= {maximum}")
    if any(ord(c) < 32 for c in value):
        raise EvidenceError(f"control character in {name}")
    return value


def validate_shape(data: Any) -> None:
    if not isinstance(data, dict) or set(data) != TOP_KEYS:
        raise EvidenceError(f"handoff fields must be exactly {sorted(TOP_KEYS)}")
    if type(data["version"]) is not int or data["version"] != 1:
        raise EvidenceError("unsupported handoff version")
    if not isinstance(data["status"], str) or data["status"] not in STATUSES:
        raise EvidenceError("invalid status")
    if not isinstance(data["stop_reason"], str) or data["stop_reason"] not in STOP_REASONS:
        raise EvidenceError("invalid stop_reason")
    total_lines = 0
    seen: set[tuple[str, int, int]] = set()
    spans: dict[str, list[tuple[int, int, str]]] = {}
    for category, limit in (("primary", 3), ("related", 2)):
        locations = data[category]
        if not isinstance(locations, list) or len(locations) > limit:
            raise EvidenceError(f"{category} must contain at most {limit} locations")
        for item in locations:
            if not isinstance(item, dict) or set(item) != SITE_KEYS:
                raise EvidenceError(f"location fields must be exactly {sorted(SITE_KEYS)}")
            path = _string(item["path"], "path", 400)
            _string(item["symbol"], "symbol", 160, empty=True)
            _string(item["evidence"], "evidence", 220)
            digest = item["sha256"]
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise EvidenceError("sha256 must be the 64-character lowercase file digest")
            start, end = item["start"], item["end"]
            if type(start) is not int or type(end) is not int:
                raise EvidenceError("line numbers must be integers, not booleans")
            if start < 1 or end < start or end - start + 1 > MAX_RANGE_LINES:
                raise EvidenceError(f"range must be 1-based, inclusive, <= {MAX_RANGE_LINES} lines")
            key = (path, start, end)
            if key in seen:
                raise EvidenceError("duplicate location")
            seen.add(key)
            for old_start, old_end, old_digest in spans.setdefault(path, []):
                if old_digest != digest:
                    raise EvidenceError(f"inconsistent file digests: {path}")
                if max(start, old_start) <= min(end, old_end):
                    raise EvidenceError(f"overlapping ranges: {path}; merge them")
            spans[path].append((start, end, digest))
            total_lines += end - start + 1
    if total_lines > MAX_TOTAL_LINES:
        raise EvidenceError(f"handoff ranges exceed {MAX_TOTAL_LINES} total lines")
    unresolved = data["unresolved"]
    if not isinstance(unresolved, list) or len(unresolved) > 3:
        raise EvidenceError("unresolved must be an array with at most 3 entries")
    for value in unresolved:
        _string(value, "unresolved", 200)
    if data["status"] == "ready":
        if not data["primary"] or data["stop_reason"] != "evidence_ready" or unresolved:
            raise EvidenceError("ready requires primary evidence, evidence_ready, and no unresolved blocker")
    elif data["status"] == "partial":
        if not data["primary"] or not unresolved or data["stop_reason"] not in {"budget", "no_progress"}:
            raise EvidenceError("partial requires a primary, unresolved issue, and budget/no_progress")
    elif data["status"] == "not_found":
        if data["primary"] or data["related"] or not unresolved or data["stop_reason"] not in {"budget", "no_progress", "no_match"}:
            raise EvidenceError("not_found requires empty locations and a missing-anchor explanation")
    elif data["status"] == "blocked":
        if data["primary"] or data["related"] or not unresolved or data["stop_reason"] != "environment":
            raise EvidenceError("blocked requires empty locations and an environment explanation")
    if len(compact_json(data).encode("utf-8")) > MAX_HANDOFF_BYTES:
        raise EvidenceError("canonical handoff exceeds byte budget")


def source_path(root: Path, relative: str) -> Path:
    _string(relative, "path", 400)
    p = PurePosixPath(relative)
    if "\\" in relative or p.is_absolute() or ".." in p.parts or ":" in relative:
        raise EvidenceError("use a repository-relative POSIX path without '..'")
    if str(p) != relative or relative in {"", "."}:
        raise EvidenceError("path must be normalized")
    if any(part in {".git", ".ssh", ".aws"} for part in p.parts):
        raise EvidenceError("repository metadata or credential directory is excluded")
    if p.name.startswith(".env") or p.name in {"id_rsa", "id_ed25519"} or p.suffix in {".pem", ".key"}:
        raise EvidenceError("credential-like file is excluded by this helper")
    current = root.resolve(strict=True)
    for component in p.parts:
        current = current / component
        if current.is_symlink():
            raise EvidenceError("symlinked source is excluded; use the real repository path")
    try:
        resolved = (root.resolve(strict=True) / relative).resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError) as exc:
        raise EvidenceError(f"missing path or path outside repository: {relative}") from exc
    if not resolved.is_file():
        raise EvidenceError(f"not a regular file: {relative}")
    return resolved


def source_bytes(root: Path, relative: str) -> tuple[bytes, list[str]]:
    p = source_path(root, relative)
    try:
        with p.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise EvidenceError(f"cannot read {relative}: {exc}") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise EvidenceError(f"file exceeds {MAX_FILE_BYTES} bytes; use a narrower artifact")
    if b"\x00" in raw:
        raise EvidenceError("binary source is unsupported")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("only UTF-8 source is supported; do not silently transcode") from exc
    # Split on LF, not Unicode splitlines(), so numbers agree with common source tools.
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    if not text:
        lines = []
    # CRLF is normalized only for display; SHA-256 always covers the original bytes.
    return raw, [line[:-1] if line.endswith("\r") else line for line in lines]


def read_range(root: Path, relative: str, start: int, end: int,
               expected: str | None = None) -> dict[str, Any]:
    if type(start) is not int or type(end) is not int or not (1 <= start <= end):
        raise EvidenceError("invalid 1-based inclusive range")
    if end - start + 1 > MAX_RANGE_LINES:
        raise EvidenceError(f"read at most {MAX_RANGE_LINES} lines per request")
    raw, lines = source_bytes(root, relative)
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        raise EvidenceError(f"stale_source: {relative}; re-localize the symbol in this file")
    if end > len(lines):
        raise EvidenceError(f"range exceeds {len(lines)} lines in {relative}")
    result = {"path": relative, "start": start, "end": end, "sha256": digest,
              "source": "\n".join(f"{i}: {lines[i-1]}" for i in range(start, end + 1))}
    if len(compact_json(result).encode("utf-8")) > MAX_READ_BYTES:
        raise EvidenceError(f"source output exceeds {MAX_READ_BYTES} bytes; narrow the range (not truncated)")
    return result


def verify_handoff(root: Path, data: dict[str, Any]) -> dict[str, Any]:
    validate_shape(data)
    for item in data["primary"] + data["related"]:
        read_range(root, item["path"], item["start"], item["end"], item["sha256"])
    return {"ok": True, "status": data["status"],
            "locations": len(data["primary"]) + len(data["related"]),
            "semantic_relevance_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    read = sub.add_parser("read", help="read exact source with a machine-computed SHA-256")
    read.add_argument("--root", type=Path, default=Path.cwd())
    read.add_argument("--path", required=True)
    read.add_argument("--start", type=int, required=True)
    read.add_argument("--end", type=int, required=True)
    read.add_argument("--expect-sha256")
    check = sub.add_parser("check", help="validate a report without printing source")
    check.add_argument("--root", type=Path, default=Path.cwd())
    check.add_argument("--handoff", required=True, help="JSON file, or - for stdin")
    args = parser.parse_args()
    try:
        if args.action == "read":
            result = read_range(args.root, args.path, args.start, args.end, args.expect_sha256)
        else:
            if args.handoff == "-":
                raw = sys.stdin.buffer.read(MAX_HANDOFF_BYTES + 1)
            else:
                with Path(args.handoff).open("rb") as stream:
                    raw = stream.read(MAX_HANDOFF_BYTES + 1)
            result = verify_handoff(args.root, load_handoff(raw))
        print(compact_json(result))
        return 0
    except (EvidenceError, OSError) as exc:
        print(compact_json({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
