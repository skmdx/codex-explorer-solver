#!/usr/bin/env python3
"""Upgrade ONLY unchanged v1/v2/v3 payload files and remove obsolete kit-native agent definitions; preserve user edits by refusing conflicts.

Dry-run by default. Backups required outside the repository on apply.
Run while no other process is modifying the kit. config/AGENTS/hooks are untouched.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parent


def prepare(repo:Path):
    repo=repo.resolve(strict=True)
    if not (repo/'.git').exists():raise ValueError('repository root required')
    legacy=json.loads((ROOT/'legacy-payload-sha256.json').read_text())
    v2path=ROOT/'v2-payload-sha256.json'
    v2=json.loads(v2path.read_text()) if v2path.is_file() else {}
    v3path=ROOT/'v3-payload-sha256.json'
    v3=json.loads(v3path.read_text()) if v3path.is_file() else {}
    changes=[];conflicts=[]
    for src in sorted((ROOT/'payload').rglob('*')):
        if not src.is_file() or '__pycache__' in src.parts:continue
        rel=src.relative_to(ROOT/'payload');dst=repo/rel
        for parent in [dst,*dst.parents]:
            if parent==repo:break
            if parent.is_symlink():raise ValueError(f'symlinked destination: {rel}')
        new=src.read_bytes();old=dst.read_bytes() if dst.exists() else None
        if old==new:continue
        if old is not None and hashlib.sha256(old).hexdigest() not in {legacy.get(rel.as_posix()),v2.get(rel.as_posix()),v3.get(rel.as_posix())}:
            conflicts.append(rel.as_posix());continue
        changes.append((rel,old,new))
    # Retire only known kit-owned Codex agent files. Never delete customized definitions.
    for name in ('repo_explorer.toml','repo_reader.toml','repo_deep_explorer.toml'):
        rel=Path('.codex/agents')/name;dst=repo/rel
        for parent in [dst,*dst.parents]:
            if parent==repo:break
            if parent.is_symlink():raise ValueError(f'symlinked retired destination: {rel}')
        if not dst.exists():continue
        old=dst.read_bytes()
        if hashlib.sha256(old).hexdigest() not in {legacy.get(rel.as_posix()),v2.get(rel.as_posix()),v3.get(rel.as_posix())}:
            conflicts.append(rel.as_posix());continue
        changes.append((rel,old,None))
    if conflicts:raise ValueError('locally modified or unrecognized files; merge manually, nothing changed: '+', '.join(conflicts))
    return repo,changes


def replace(path:Path,data:bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.es-upgrade-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data)
        os.chmod(name,0o644);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def upgrade(repo:Path,apply:bool=False,backup:Path|None=None):
    repo,changes=prepare(repo)
    plan=[dict(path=rel.as_posix(),action='delete' if new is None else 'create' if old is None else 'replace') for rel,old,new in changes]
    if not apply:return plan
    if backup is None:raise ValueError('--backup-dir required with --apply')
    backup=backup.resolve()
    if backup==repo or repo in backup.parents:raise ValueError('backup must be outside the repository')
    backup.mkdir(mode=0o700,parents=True,exist_ok=False);os.chmod(backup,0o700)
    for rel,old,_ in changes:
        if old is not None:
            dst=backup/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(old);os.chmod(dst,0o600)
    (backup/'plan.json').write_text(json.dumps(plan,indent=2)+'\n');os.chmod(backup/'plan.json',0o600)
    done=[]
    try:
        for rel,old,new in changes:
            dst=repo/rel
            now=dst.read_bytes() if dst.exists() else None
            if now!=old:raise ValueError(f'concurrent modification: {rel}')
            if new is None:
                dst.unlink()
            elif old is None:
                dst.parent.mkdir(parents=True,exist_ok=True)
                with dst.open('xb') as f:f.write(new)
                os.chmod(dst,0o644)
            else:replace(dst,new)
            done.append((rel,old,new))
    except BaseException:
        for rel,old,new in reversed(done):
            dst=repo/rel
            if new is None:
                if not dst.exists() and not dst.is_symlink():replace(dst,old)
            elif dst.is_file() and not dst.is_symlink() and dst.read_bytes()==new:
                if old is None:dst.unlink()
                else:replace(dst,old)
        raise
    return plan


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--backup-dir',type=Path)
    a=p.parse_args()
    try:
        print(json.dumps({'applied':a.apply,'changes':upgrade(a.repo,a.apply,a.backup_dir),
                          'config_agents_hooks_modified':False},indent=2));return 0
    except (OSError,ValueError) as exc:print(f'upgrade error: {exc}',file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
