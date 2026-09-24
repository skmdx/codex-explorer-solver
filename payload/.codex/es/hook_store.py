#!/usr/bin/env python3
"""Private, bounded storage for exact hook-observed JSON and numeric audit data.

Standard library only. Not a security sandbox. No network and no model calls.
Archive before replacement; archive failure must leave the original output alone.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
from typing import Any, Iterator

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_STORE_BYTES = 128 * 1024 * 1024
ID_PATTERN = re.compile(r'^[0-9a-f]{64}$')


class StoreError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                      sort_keys=True, allow_nan=False).encode('utf-8')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text_id(value: str) -> str:
    return digest(value.encode('utf-8'))


def no_symlinks(path: Path) -> Path:
    """Reject existing symlink components before resolving, including the leaf."""
    path = Path(os.path.abspath(path.expanduser()))
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise StoreError('symlinked state/config path is not supported')
    return path


def private_dir(path: Path) -> Path:
    path = no_symlinks(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.stat()
    if not stat.S_ISDIR(st.st_mode):
        raise StoreError('state path must be a directory')
    if hasattr(os, 'getuid') and st.st_uid != os.getuid():
        raise StoreError('state directory is owned by a different user')
    os.chmod(path, 0o700)
    return path


def safe_read(path: Path, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    path = no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise StoreError('expected a bounded regular file')
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise StoreError('file grew beyond size limit')
    return raw


def atomic_private(path: Path, raw: bytes) -> None:
    path = no_symlinks(path)
    fd, temporary = tempfile.mkstemp(prefix='.es-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        # Make a stored reference durable before returning a shortened observation.
        if os.name == 'posix':
            dfd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, state_dir: Path, root: Path, *, max_bytes: int = DEFAULT_STORE_BYTES):
        if type(max_bytes) is not int or max_bytes < 1:
            raise StoreError('invalid store byte budget')
        self.root = root.resolve(strict=True)
        self.path = no_symlinks(state_dir)
        if self.path == self.root or self.root in self.path.parents:
            raise StoreError('state-dir must be outside the repository')
        self.path = private_dir(self.path)
        self.blobs = private_dir(self.path / 'blobs')
        self.dbpath = no_symlinks(self.path / 'ledger.sqlite3')
        for suffix in ('-wal', '-shm', '-journal'):
            no_symlinks(Path(str(self.dbpath) + suffix))
        self.max_bytes = max_bytes
        # Create SQLite privately, rather than depending on the caller's umask.
        try:
            fd = os.open(self.dbpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, 'O_NOFOLLOW', 0), 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        if not stat.S_ISREG(self.dbpath.stat().st_mode):
            raise StoreError('ledger must be a regular file')
        os.chmod(self.dbpath, 0o600)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS blobs
                    (id TEXT PRIMARY KEY, bytes INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS observations
                    (scope TEXT NOT NULL, call TEXT NOT NULL, role TEXT NOT NULL,
                     artifact TEXT, bytes INTEGER NOT NULL, feedback_bytes INTEGER NOT NULL,
                     action TEXT NOT NULL, created REAL NOT NULL,
                     PRIMARY KEY(scope, call));
                CREATE TABLE IF NOT EXISTS admissions
                    (scope TEXT NOT NULL, turn TEXT NOT NULL, call TEXT NOT NULL,
                     args_sha TEXT NOT NULL, allowed INTEGER NOT NULL, created REAL NOT NULL,
                     PRIMARY KEY(scope, turn, call));
                CREATE TABLE IF NOT EXISTS checkpoints
                    (scope TEXT PRIMARY KEY, generation INTEGER NOT NULL, created REAL NOT NULL,
                     refs TEXT NOT NULL DEFAULT '[]');
            ''')
            db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('repository', str(self.root)))
            if db.execute('SELECT value FROM meta WHERE key=?', ('repository',)).fetchone()[0] != str(self.root):
                raise StoreError('state directory belongs to another repository')

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.dbpath, timeout=1.0)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def archive(self, response: Any) -> str:
        raw = canonical(response)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise StoreError('hook response exceeds archive limit')
        aid = digest(raw)
        target = self.blobs / (aid + '.json')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT bytes FROM blobs WHERE id=?', (aid,)).fetchone()
            if found:
                if safe_read(target) != raw:
                    raise StoreError('existing archive failed integrity check')
                return aid
            # Include unindexed files from interrupted previous writes in the budget.
            disk_bytes = sum(p.lstat().st_size for p in self.blobs.iterdir())
            extra = 0 if target.is_file() and not target.is_symlink() else len(raw)
            if disk_bytes + extra > self.max_bytes:
                raise StoreError('archive budget exhausted; original result must pass through')
            if target.exists():
                if safe_read(target) != raw:
                    raise StoreError('archive collision or corruption')
            else:
                atomic_private(target, raw)
            db.execute('INSERT INTO blobs VALUES (?,?,?)', (aid, len(raw), time.time()))
        return aid

    def load(self, aid: str) -> Any:
        if not isinstance(aid, str) or not ID_PATTERN.fullmatch(aid):
            raise StoreError('artifact id must be a SHA-256 digest')
        raw = safe_read(self.blobs / (aid + '.json'))
        if digest(raw) != aid:
            raise StoreError('archive hash mismatch')
        return json.loads(raw)

    def observe(self, session: str, call: str, role: str, size: int, action: str,
                artifact: str | None = None, feedback_bytes: int = 0) -> None:
        # Session/call identifiers are hashed; do not log prompts, commands or source.
        with self.connect() as db:
            db.execute('''INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)
                          ON CONFLICT(scope,call) DO UPDATE SET role=excluded.role,
                          artifact=excluded.artifact,bytes=excluded.bytes,
                          feedback_bytes=excluded.feedback_bytes,action=excluded.action''',
                       (text_id(session), text_id(call), role, artifact, size,
                        feedback_bytes, action, time.time()))

    def admit(self, session: str, turn: str, call: str, args: Any, limit: int) -> bool:
        if not all(isinstance(x, str) and x for x in (session, turn, call)):
            raise StoreError('native budget requires session_id, turn_id and tool_use_id')
        if type(limit) is not int or limit < 1:
            raise StoreError('invalid native spawn limit')
        scope, tid, cid, ah = text_id(session), text_id(turn), text_id(call), digest(canonical(args))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT args_sha,allowed FROM admissions WHERE scope=? AND turn=? AND call=?',
                             (scope, tid, cid)).fetchone()
            if row:
                # A replayed call cannot silently change its arguments under the same id.
                if row[0] != ah:
                    raise StoreError('same tool-use id with different spawn arguments')
                return bool(row[1])
            count = db.execute('SELECT COUNT(*) FROM admissions WHERE scope=? AND turn=? AND allowed=1',
                               (scope, tid)).fetchone()[0]
            allowed = count < limit
            db.execute('INSERT INTO admissions VALUES (?,?,?,?,?,?)',
                       (scope, tid, cid, ah, int(allowed), time.time()))
            return allowed

    def checkpoint(self, session: str) -> None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT artifact,role,bytes,created FROM observations WHERE scope=? "
                              "AND artifact IS NOT NULL ORDER BY created DESC LIMIT 3",
                              (text_id(session),)).fetchall()
            refs = [dict(id=r[0], kind=r[1], observed_json_bytes=r[2], created=r[3]) for r in rows]
            db.execute('''INSERT INTO checkpoints VALUES (?,1,?,?) ON CONFLICT(scope)
                          DO UPDATE SET generation=generation+1,created=excluded.created,refs=excluded.refs''',
                       (text_id(session), time.time(), canonical(refs).decode('utf-8')))

    def checkpoint_refs(self, session: str) -> list[dict[str, Any]] | None:
        with self.connect() as db:
            row = db.execute('SELECT refs FROM checkpoints WHERE scope=?', (text_id(session),)).fetchone()
        return json.loads(row[0]) if row else None

    def recent(self, session: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise StoreError('limit must be 1..20')
        query = 'SELECT artifact,role,bytes,created FROM observations WHERE artifact IS NOT NULL'
        args: tuple = ()
        if session:
            query += ' AND scope=?'
            args = (text_id(session),)
        query += ' ORDER BY created DESC LIMIT ?'
        with self.connect() as db:
            rows = db.execute(query, (*args, limit)).fetchall()
        return [dict(id=r[0], kind=r[1], observed_json_bytes=r[2], created=r[3]) for r in rows]

    def report(self) -> dict[str, Any]:
        with self.connect() as db:
            actions = [dict(action=r[0], observations=r[1], observed_json_bytes=r[2],
                            feedback_json_bytes=r[3]) for r in db.execute(
                'SELECT action,COUNT(*),SUM(bytes),SUM(feedback_bytes) FROM observations GROUP BY action')]
            n, size = db.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM blobs').fetchone()
            allowed, denied = db.execute('SELECT COALESCE(SUM(allowed),0),COALESCE(SUM(1-allowed),0) FROM admissions').fetchone()
        return dict(actions=actions, archived_objects=n, archived_json_bytes=size,
                    within_native_admission_limit=allowed, over_native_admission_limit=denied,
                    token_savings=None, billing_savings=None,
                    note='Local hook byte accounting only; not actual model input or usage. '
                         'Admission counts are policy decisions, not actual starts; audit mode does not deny. '
                         'Code-mode scripts may re-emit original output. Ledger size is not included in blob quota.')
