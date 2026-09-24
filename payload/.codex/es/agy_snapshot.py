#!/usr/bin/env python3
"""Bounded disposable source export; no code executes and no model is called.

The export is NOT an OS sandbox. It omits user/project agent configurations and
credential-like paths; filename filtering is NOT a complete secret detector.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any
from evidence import EvidenceError, compact_json, source_bytes, source_path, load_handoff

EXCLUDED_DIRS = {'.git', '.agents', '.codex', '.gemini', '.agent', '.claude',
                 '.ssh', '.aws', 'node_modules', '.venv', '__pycache__'}
EXCLUDED_NAMES = {'AGENTS.md', 'GEMINI.md', 'CLAUDE.md', '.npmrc', '.pypirc',
                  'credentials.json', 'credentials', 'id_rsa', 'id_ed25519'}


def safe_relative(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 400:
        raise EvidenceError('invalid source path')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p) != name or name == '.' \
            or '\\' in name or ':' in name or any(ord(c) < 32 for c in name):
        raise EvidenceError('source path must be normalized repository-relative POSIX')
    return name


def exclusion(name: str) -> str | None:
    p = PurePosixPath(safe_relative(name))
    if {x.lower() for x in p.parts} & {x.lower() for x in EXCLUDED_DIRS} or p.name.lower() in {x.lower() for x in EXCLUDED_NAMES}:
        return 'configuration_or_generated_or_credential_path'
    if p.name.startswith('.env') or p.suffix.lower() in {'.pem', '.key', '.p12', '.pfx', '.keystore'}:
        return 'credential_like_path'
    return None


def git_paths(root: Path, include_untracked: bool = False) -> list[str]:
    command = ['git', '-C', str(root), 'ls-files', '-z', '--cached']
    if include_untracked:
        command += ['--others', '--exclude-standard']
    env = os.environ.copy(); env['GIT_OPTIONAL_LOCKS'] = '0'
    result = subprocess.run(command, capture_output=True, timeout=20, env=env)
    if result.returncode:
        raise EvidenceError('git ls-files failed; initialize/open a real Git repository')
    if len(result.stdout) > 16*1024*1024:
        raise EvidenceError('Git file listing too large; use a smaller worktree')
    try:
        return sorted(set(x.decode('utf-8') for x in result.stdout.split(b'\0') if x))
    except UnicodeError as exc:
        raise EvidenceError('non-UTF-8 Git filenames are unsupported') from exc


def in_scope(name: str, scopes: list[str]) -> bool:
    return not scopes or any(name == s or name.startswith(s + '/') for s in scopes)


def export(root: Path, workspace: Path, *, mode: str, paths: list[str],
           scopes: list[str], include_untracked: bool, limits: dict[str, Any]) -> dict:
    """Export current worktree bytes, NOT committed Git blobs. No silent cap truncation."""
    for scope in scopes: safe_relative(scope)
    if mode == 'reader':
        if not paths:
            raise EvidenceError('reader requires explicit --path(s)')
        if len(paths) != len(set(paths)):
            raise EvidenceError('duplicate reader input')
        if len(paths) > limits['max_reader_files']:
            raise EvidenceError('too many reader input files')
        candidates = paths
    else:
        candidates = [x for x in git_paths(root, include_untracked) if in_scope(x, scopes)]
    entries = []; skipped = []; total = 0
    # The private output directory is created by the runner; workspace is new.
    workspace.mkdir(mode=0o700, exist_ok=False)
    for name in candidates:
        try:
            reason = exclusion(name)
            if reason: raise EvidenceError(reason)
            path = source_path(root, name)
            if path.stat().st_size > limits['max_file_bytes']:
                raise EvidenceError('file_exceeds_snapshot_file_limit')
            raw, _ = source_bytes(root, name)
            if len(raw) > limits['max_file_bytes']:
                raise EvidenceError('file_exceeds_snapshot_file_limit')
        except (EvidenceError, OSError) as exc:
            if mode == 'reader':
                raise EvidenceError(f'reader input rejected ({name}): {exc}') from exc
            skipped.append({'path': name, 'reason': str(exc)[:160]})
            continue
        if len(entries) >= limits['max_snapshot_files']:
            raise EvidenceError('snapshot file cap exceeded; narrow --scope (nothing is silently truncated)')
        total += len(raw)
        byte_limit = min(limits['max_snapshot_bytes'], limits['max_reader_bytes']) if mode == 'reader' else limits['max_snapshot_bytes']
        if total > byte_limit:
            raise EvidenceError('snapshot byte cap exceeded; narrow --scope or reader inputs')
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream: stream.write(raw)
        os.chmod(destination, 0o400)
        entries.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'byte_count': len(raw)})
    if not entries:
        raise EvidenceError('no eligible source files; use --scope, explicitly include untracked, or use reader --path')
    return dict(version=1, mode=mode, scopes=scopes, includes_untracked=include_untracked,
                candidate_count=len(candidates), files=entries, skipped=skipped,
                file_count=len(entries), total_bytes=total,
                scope_is_repository_complete=False, secret_filter_is_exhaustive=False)


def reader_prompt(workspace: Path, manifest: dict) -> str:
    result = []
    for entry in manifest['files']:
        raw, lines = source_bytes(workspace, entry['path'])
        result.append({'path': entry['path'], 'source': '\n'.join(f'{i}: {s}' for i,s in enumerate(lines,1))})
    return compact_json(result)


def verify_export(root: Path, workspace: Path, manifest: dict) -> None:
    """Check every supplied input, including files not cited by a negative finding."""
    for entry in manifest['files']:
        for current_root, label in [(workspace, 'snapshot_modified'), (root, 'stale_source_corpus')]:
            try:
                raw, _ = source_bytes(current_root, entry['path'])
            except (EvidenceError, OSError) as exc:
                raise EvidenceError(f'{label}: {entry["path"]}: {exc}') from exc
            if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise EvidenceError(f'{label}: {entry["path"]}')


def bind_handoff(wire: Any, manifest: dict) -> dict:
    """Attach host-computed PRE-RUN hashes. A model never invents a digest."""
    if not isinstance(wire, dict): raise EvidenceError('structured_output must be an object')
    # JSON roundtrip avoids modifying the original saved result and bounds strange types.
    data = json.loads(compact_json(wire))
    files = {x['path']: x['sha256'] for x in manifest['files']}
    for group in ['primary', 'related']:
        if not isinstance(data.get(group), list): raise EvidenceError('invalid wire reference array')
        for ref in data[group]:
            if not isinstance(ref, dict) or set(ref) != {'path','start','end','symbol','evidence'}:
                raise EvidenceError('wire reference fields are invalid (do not supply sha256)')
            if not isinstance(ref.get('path'), str) or ref['path'] not in files:
                raise EvidenceError('reference outside exported source corpus')
            ref['sha256'] = files[ref['path']]
    return load_handoff(compact_json(data).encode('utf-8'))
