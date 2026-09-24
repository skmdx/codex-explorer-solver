#!/usr/bin/env python3
"""Export selected text sources separately from the AGY runtime configuration."""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any
from evidence import EvidenceError, compact_json, source_bytes, source_path, load_handoff

def safe_relative(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise EvidenceError('invalid source path')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts:
        raise EvidenceError('scope must be relative to the repository')
    return str(p)


def git_paths(root: Path, scopes: list[str], include_untracked: bool = False) -> list[str]:
    command = ['git', '-C', str(root), 'ls-files', '-z', '--cached']
    if include_untracked:
        command += ['--others', '--exclude-standard']
    if scopes and '.' not in scopes:
        command += ['--', *(f':(top,glob){scope}' for scope in scopes)]
    env = os.environ.copy(); env['GIT_OPTIONAL_LOCKS'] = '0'
    result = subprocess.run(command, capture_output=True, env=env)
    if result.returncode:
        raise EvidenceError('git ls-files failed: ' + result.stderr.decode('utf-8', errors='replace').strip())
    try:
        return sorted(set(x.decode('utf-8') for x in result.stdout.split(b'\0') if x))
    except UnicodeError as exc:
        raise EvidenceError('non-UTF-8 Git filenames are unsupported') from exc


def export(root: Path, workspace: Path, *, mode: str, paths: list[str],
           scopes: list[str], include_untracked: bool, encodings: dict[str, str]) -> dict:
    """Export current worktree bytes, NOT committed Git blobs. No silent cap truncation."""
    scopes = [safe_relative(scope) for scope in scopes]
    if mode == 'reader':
        if not paths:
            raise EvidenceError('reader requires explicit --path(s)')
        candidates = list(dict.fromkeys(paths))
    else:
        candidates = git_paths(root, scopes, include_untracked)
    entries = []; skipped = []; total = 0
    # The private output directory is created by the runner; workspace is new.
    workspace.mkdir(mode=0o700, exist_ok=False)
    for index, name in enumerate(candidates):
        try:
            if mode != 'reader' and not source_path(root, name).is_relative_to(root.resolve()):
                raise EvidenceError('symlink target is outside the selected repository; select it explicitly with reader --path')
            raw, _, encoding = source_bytes(root, name, encodings.get(name))
        except (EvidenceError, OSError) as exc:
            if mode == 'reader':
                raise EvidenceError(f'reader input rejected ({name}): {exc}') from exc
            skipped.append({'path': name, 'reason': str(exc)[:160]})
            continue
        total += len(raw)
        exported = raw.decode(encoding).encode('utf-8')
        export_path = f'{index}.source' if mode == 'reader' else name
        destination = workspace / export_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream: stream.write(exported)
        os.chmod(destination, 0o400)
        entries.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'byte_count': len(raw),
                        'encoding': encoding, 'export_path': export_path,
                        'export_sha256': hashlib.sha256(exported).hexdigest()})
    if not entries:
        raise EvidenceError('no eligible source files; use --scope, explicitly include untracked, or use reader --path')
    return dict(version=2, mode=mode, scopes=scopes, includes_untracked=include_untracked,
                candidate_count=len(candidates), files=entries, skipped=skipped,
                file_count=len(entries), total_bytes=total,
                scope_is_repository_complete=False)


def reader_prompt(workspace: Path, manifest: dict) -> str:
    result = []
    for entry in manifest['files']:
        raw, lines, _ = source_bytes(workspace, entry['export_path'], 'utf-8')
        result.append({'path': entry['path'], 'source': '\n'.join(f'{i}: {s}' for i,s in enumerate(lines,1))})
    return compact_json(result)


def verify_export(root: Path, workspace: Path, manifest: dict) -> None:
    """Check every supplied input, including files not cited by a negative finding."""
    for entry in manifest['files']:
        for current_root, path, label, encoding, digest in [
                (workspace, entry['export_path'], 'snapshot_modified', 'utf-8', entry['export_sha256']),
                (root, entry['path'], 'stale_source_corpus', entry['encoding'], entry['sha256'])]:
            try:
                raw, _, _ = source_bytes(current_root, path, encoding)
            except (EvidenceError, OSError) as exc:
                raise EvidenceError(f'{label}: {entry["path"]}: {exc}') from exc
            if hashlib.sha256(raw).hexdigest() != digest:
                raise EvidenceError(f'{label}: {entry["path"]}')


def export_navigation(navigation: dict, root: Path, source_root: Path, manifest: dict) -> dict:
    """Map known original locations to exported files, including MCP text payloads."""
    workspace = Path(navigation['root'])
    paths = {}
    for entry in manifest['files']:
        original = root / entry['path']
        target = str(source_root / entry['export_path'])
        paths[str(original)] = target
        paths[original.as_uri()] = target
        if original.is_relative_to(workspace):
            paths[str(original.relative_to(workspace))] = target
    pattern = re.compile(r'(?<![\w./-])(?:' + '|'.join(re.escape(p) for p in sorted(paths, key=len, reverse=True))
                         + r')(?![\w./-])')
    def rewrite(value):
        if isinstance(value, str):
            return pattern.sub(lambda match: paths[match.group()], value)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value
    return {'root': str(source_root), 'queries': rewrite(navigation['queries'])}


def bind_handoff(wire: Any, manifest: dict, source_root: Path) -> dict:
    """Attach host-computed PRE-RUN hashes. A model never invents a digest."""
    if not isinstance(wire, dict): raise EvidenceError('structured_output must be an object')
    if set(wire) != {'references', 'unresolved'}:
        raise EvidenceError('return references and unresolved only')
    data = dict(version=3, status='ready', primary=json.loads(compact_json(wire['references'])),
                related=[], unresolved=wire['unresolved'])
    files = {x['path']: x for x in manifest['files']}
    paths = {str(source_root / x['export_path']): x['path'] for x in manifest['files']}
    for group in ['primary', 'related']:
        references = data[group]
        if not isinstance(references, list): raise EvidenceError('invalid wire reference array')
        for ref in references:
            if not isinstance(ref, dict) or set(ref) != {'path','start','end','symbol','evidence'}:
                raise EvidenceError('wire reference fields are invalid (do not supply sha256)')
            if not isinstance(ref.get('path'), str):
                raise EvidenceError('reference path must be a string')
            ref['path'] = paths.get(ref['path'], ref['path'])
            if ref['path'] not in files:
                raise EvidenceError('reference outside exported source corpus')
            ref['sha256'] = files[ref['path']]['sha256']
            ref['encoding'] = files[ref['path']]['encoding']
    return load_handoff(compact_json(data).encode('utf-8'))
