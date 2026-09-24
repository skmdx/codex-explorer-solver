#!/usr/bin/env python3
"""Plan/install/update/remove only this kit's hooks, retaining unrelated JSON entries.

Dry-run is the default. Apply requires a new external backup directory. Does not
change config.toml, AGENTS.md, feature flags or trust/permission settings.
Close Codex and other config writers before applying. POSIX/WSL command quoting.
"""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import sys
from typing import Any
from hook_store import StoreError, canonical, digest, no_symlinks, private_dir, atomic_private, safe_read

MARKER = 'codex-es-v3'
MANIFEST = '.codex/es/hooks-installed.json'


def generated_groups(root: Path, state: Path, mode: str, native_limit: int) -> dict[str, list]:
    script = root / '.codex/es/hook_runtime.py'
    if not script.is_file():
        raise StoreError('install or upgrade the v3 payload first')
    command = shlex.join([sys.executable, str(script), '--root', str(root),
                         '--state-dir', str(state), '--mode', mode,
                         '--native-spawn-limit', str(native_limit)])
    def group(event, matcher, context_limit=None):
        handler: dict[str, Any] = {'type': 'command', 'command': command, 'timeout': 5,
                                   'statusMessage': MARKER + ':' + event}
        if context_limit is not None:
            handler['additionalContextLimit'] = context_limit
        return {'matcher': matcher, 'hooks': [handler]}
    result = {'PostToolUse': [group('post', '^Bash$')],
              'PreCompact': [group('checkpoint', '^(manual|auto)$')],
              'SessionStart': [group('restore', '^(compact|resume)$', 350)]}
    if native_limit:
        result['PreToolUse'] = [group('spawn-budget', r'^(Agent|(?:.*\.)?spawn_agent)$')]
    return result


def load_json(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, val in pairs:
            if key in result: raise StoreError('duplicate JSON keys in hook config')
            result[key] = val
        return result
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(StoreError('non-finite JSON')))
    if not isinstance(value, dict): raise StoreError('expected JSON object')
    return value


def merge(existing: dict, previous: dict[str, list], desired: dict[str, list]) -> dict:
    result = copy.deepcopy(existing)
    hooks = result.setdefault('hooks', {})
    if not isinstance(hooks, dict): raise StoreError('hooks must be an object')
    # Refuse ambiguous or edited ownership; never remove an entry by substring alone.
    for event, groups in previous.items():
        if not isinstance(hooks.get(event), list): raise StoreError('installed hooks were changed; merge manually')
        for group in groups:
            count = sum(item == group for item in hooks[event])
            if count != 1: raise StoreError('owned hook was edited, removed or duplicated; merge manually')
            hooks[event].remove(group)
    for event, groups in hooks.items():
        if not isinstance(groups, list): raise StoreError('each hook event must contain an array')
        for group in groups:
            if not isinstance(group, dict): raise StoreError('invalid matcher group')
            handlers = group.get('hooks', [])
            if not isinstance(handlers, list): raise StoreError('hooks handler list must be an array')
            for handler in handlers:
                if not isinstance(handler, dict): raise StoreError('invalid hook handler')
                if 'hook_runtime.py' in str(handler.get('command', '')) or str(handler.get('statusMessage', '')).startswith(MARKER + ':'):
                    raise StoreError('unowned/edited v3 hook found; merge manually instead of double-registering')
    for event, groups in desired.items():
        hooks.setdefault(event, []).extend(copy.deepcopy(groups))
    # Preserve empty arrays, top-level fields and unrelated groups as JSON values.
    return result


def configure(repo: Path, state_dir: Path | None = None, *, mode='audit', native_limit=0,
              remove=False, apply=False, backup: Path | None = None) -> dict:
    if os.name != 'posix': raise StoreError('hook launcher supports Linux/macOS/WSL; native Windows not validated')
    if mode not in {'audit', 'enforce'} or type(native_limit) is not int or not 0 <= native_limit <= 100:
        raise StoreError('invalid mode or native limit')
    root = no_symlinks(repo).resolve(strict=True)
    if not (root / '.git').exists(): raise StoreError('repository root required')
    hp = no_symlinks(root / '.codex/hooks.json')
    mp = no_symlinks(root / MANIFEST)
    old_h = safe_read(hp, 1024*1024) if hp.exists() else None
    old_m = safe_read(mp, 1024*1024) if mp.exists() else None
    existing = load_json(old_h) if old_h else {'hooks': {}}
    manifest = load_json(old_m) if old_m else {}
    previous = manifest.get('groups', {})
    if not isinstance(previous, dict): raise StoreError('invalid ownership manifest')
    if remove:
        if not previous: raise StoreError('no v3 ownership manifest; refusing unowned removal')
        desired = {}
    else:
        if state_dir is None: raise StoreError('--state-dir is required')
        state = no_symlinks(state_dir)
        if state == root or root in state.parents: raise StoreError('state-dir must be outside the repository')
        desired = generated_groups(root, state, mode, native_limit)
    merged = merge(existing, previous, desired)
    new_h = (json.dumps(merged, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    new_m = None if remove else (json.dumps({'version': 1, 'groups': desired,
                'hook_runtime_sha256': digest(safe_read(root / '.codex/es/hook_runtime.py'))},
                ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    changed = (old_h != new_h or old_m != new_m)
    warnings = ['Review/trust the installed hook definitions with /hooks. No trust bypass is configured.',
                'Other hook sources still run concurrently; their decisions are not replaced by these hooks.']
    if 'read_guard.py' in str(existing):
        warnings.append('A v2 read guard is retained. A deny-mode guard may still reject raw reads; disable it separately if unwanted.')
    cp = root / '.codex/config.toml'
    if cp.is_file():
        import tomllib
        if 'hooks' in tomllib.loads(cp.read_text()):
            warnings.append('Inline [hooks] also exists in config.toml; Codex merges both forms. It was not modified.')
    plan = dict(applied=apply, changed=changed, removing=remove,
                files=['.codex/hooks.json', MANIFEST], hooks=merged, warnings=warnings)
    if not apply or not changed: return plan
    if backup is None: raise StoreError('--backup-dir required with --apply')
    bp = no_symlinks(backup)
    if bp == root or root in bp.parents: raise StoreError('backup must be outside the repository')
    if bp.exists(): raise StoreError('backup directory must not already exist')
    private_dir(bp)
    for name, raw in (('hooks.json', old_h), ('hooks-installed.json', old_m)):
        if raw is not None: atomic_private(bp / name, raw)
    atomic_private(bp / 'original-state.json', canonical({'hooks_existed': old_h is not None,
                                                          'manifest_existed': old_m is not None}))
    # Optimistic concurrency checks. Applying while another process writes is unsupported.
    if (safe_read(hp, 1024*1024) if hp.exists() else None) != old_h or \
       (safe_read(mp, 1024*1024) if mp.exists() else None) != old_m:
        raise StoreError('config changed during planning; nothing applied')
    hp.parent.mkdir(parents=True, exist_ok=True); mp.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_private(hp, new_h)
        if new_m is None: mp.unlink(missing_ok=True)
        else: atomic_private(mp, new_m)
    except BaseException:
        # Best-effort rollback only if no third party changed the new hooks.
        if hp.exists() and safe_read(hp, 1024*1024) == new_h:
            if old_h is None: hp.unlink()
            else: atomic_private(hp, old_h)
        raise
    return plan


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--state-dir', type=Path)
    p.add_argument('--mode', choices=['audit', 'enforce'], default='audit')
    p.add_argument('--native-spawn-limit', type=int, default=0)
    p.add_argument('--remove', action='store_true')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--backup-dir', type=Path)
    a = p.parse_args()
    try:
        print(json.dumps(configure(a.repo, a.state_dir, mode=a.mode, native_limit=a.native_spawn_limit,
                                   remove=a.remove, apply=a.apply, backup=a.backup_dir),
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr); return 2

if __name__ == '__main__':
    raise SystemExit(main())
