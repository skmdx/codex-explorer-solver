#!/usr/bin/env python3
"""Persistent per-task admission control for this kit's external worker runner.

Counts attempts including failures. Not a provider token cap or a sandbox.
Does not intercept native subagent spawns or arbitrary separate AGY/Codex processes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from typing import Any
from evidence import EvidenceError, compact_json


def initialize(state: Path, root: Path, task: bytes, max_calls: int=2,
               soft_token_limit: int|None=None, allow_unknown: bool=False) -> None:
    root=root.resolve(strict=True);state=state.resolve()
    if state==root or root in state.parents:
        raise EvidenceError('budget state must be outside the repository')
    if not 1<=max_calls<=20 or (soft_token_limit is not None and soft_token_limit<=0):
        raise EvidenceError('invalid budget')
    if not task.strip() or len(task)>16384:
        raise EvidenceError('task must be nonempty and <=16384 bytes')
    state.mkdir(mode=0o700,parents=True,exist_ok=False);os.chmod(state,0o700)
    db=state/'budget.sqlite3'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE policy (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
        c.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, role TEXT NOT NULL, task_sha256 TEXT NOT NULL, status TEXT NOT NULL, started REAL NOT NULL, finished REAL, tokens INTEGER, usage_complete INTEGER, result_status TEXT)')
        c.execute('INSERT INTO policy VALUES (1,?)',(compact_json(dict(repo=str(root),max_calls=max_calls,
                  soft_token_limit=soft_token_limit,allow_unknown=allow_unknown,
                  original_task_sha256=hashlib.sha256(task).hexdigest())),))
    os.chmod(db,0o600)


def connect(state: Path) -> sqlite3.Connection:
    db=state.resolve(strict=True)/'budget.sqlite3'
    if not db.is_file() or db.is_symlink():raise EvidenceError('budget database missing or symlinked')
    c=sqlite3.connect(db,timeout=5,isolation_level=None)
    c.row_factory=sqlite3.Row
    return c


def reserve(state: Path, root: Path, role: str, task: bytes) -> str:
    if role not in {'repo_explorer','repo_reader','repo_deep_explorer'}:
        raise EvidenceError('unknown budget role')
    c=connect(state)
    try:
        c.execute('BEGIN IMMEDIATE')
        policy=json.loads(c.execute('SELECT data FROM policy WHERE id=1').fetchone()[0])
        if policy['repo']!=str(root.resolve(strict=True)):
            raise EvidenceError('budget belongs to a different repository')
        rows=c.execute('SELECT * FROM jobs').fetchall()
        if any(x['status']=='running' for x in rows):raise EvidenceError('one worker is already reserved/running; no automatic stale-lease reset')
        if len(rows)>=policy['max_calls']:raise EvidenceError('task delegation count exhausted (failures count)')
        if rows and not policy['allow_unknown'] and any(x['usage_complete']!=1 for x in rows):
            raise EvidenceError('previous worker usage is unknown; automatic further spending refused')
        if role=='repo_deep_explorer':
            if not rows:raise EvidenceError('deep exploration requires a prior low-cost attempt')
            if any(x['role']==role for x in rows):raise EvidenceError('deep exploration already used')
        observed=sum(x['tokens'] or 0 for x in rows)
        if policy['soft_token_limit'] is not None and observed>=policy['soft_token_limit']:
            raise EvidenceError('observed token soft limit reached; no new worker admitted')
        job=uuid.uuid4().hex
        c.execute('INSERT INTO jobs(id,role,task_sha256,status,started) VALUES(?,?,?,?,?)',
                  (job,role,hashlib.sha256(task).hexdigest(),'running',time.time()))
        c.execute('COMMIT');return job
    except BaseException:
        if c.in_transaction:c.execute('ROLLBACK')
        raise
    finally:c.close()


def finish(state: Path, job: str, metadata: dict[str,Any]) -> None:
    usage=metadata.get('usage') or {};tokens=usage.get('total_tokens')
    if type(tokens) is not int or tokens<0:tokens=None
    complete=bool(metadata.get('usage_complete') and tokens is not None)
    c=connect(state)
    try:
        c.execute('BEGIN IMMEDIATE')
        n=c.execute("UPDATE jobs SET status='finished',finished=?,tokens=?,usage_complete=?,result_status=? WHERE id=? AND status='running'",
                    (time.time(),tokens,int(complete),str(metadata.get('status','unknown')),job)).rowcount
        if n!=1:raise EvidenceError('job is missing or already finalized')
        c.execute('COMMIT')
    except BaseException:
        if c.in_transaction:c.execute('ROLLBACK')
        raise
    finally:c.close()


def status(state: Path) -> dict[str,Any]:
    c=connect(state)
    try:
        policy=json.loads(c.execute('SELECT data FROM policy WHERE id=1').fetchone()[0])
        rows=[dict(x) for x in c.execute('SELECT * FROM jobs ORDER BY started')]
        complete=all(x['status']=='finished' and x['usage_complete']==1 for x in rows)
        known=sum(x['tokens'] or 0 for x in rows)
        return dict(policy=policy,jobs=rows,attempts=len(rows),observed_tokens_lower_bound=known,
                    total_worker_tokens=known if complete else None,worker_usage_complete=complete,
                    includes_parent_usage=False,provider_hard_token_cap=False)
    finally:c.close()


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='cmd',required=True)
    q=s.add_parser('init');q.add_argument('--state-dir',type=Path,required=True);q.add_argument('--repo',type=Path,default=Path.cwd())
    q.add_argument('--task-file',type=Path,required=True);q.add_argument('--max-calls',type=int,default=2)
    q.add_argument('--soft-token-limit',type=int);q.add_argument('--allow-unknown-usage',action='store_true')
    q=s.add_parser('status');q.add_argument('--state-dir',type=Path,required=True)
    q=s.add_parser('mark-abandoned');q.add_argument('--state-dir',type=Path,required=True);q.add_argument('--job-id',required=True)
    a=p.parse_args()
    try:
        if a.cmd=='init':
            with a.task_file.open('rb') as f:task=f.read(16385)
            initialize(a.state_dir,a.repo,task,a.max_calls,a.soft_token_limit,a.allow_unknown_usage)
        elif a.cmd=='mark-abandoned':
            finish(a.state_dir,a.job_id,{'status':'manually_abandoned','usage_complete':False,'usage':None})
        print(compact_json(status(a.state_dir)));return 0
    except (EvidenceError,OSError,sqlite3.Error,ValueError) as exc:
        print(compact_json({'ok':False,'error':str(exc)}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
