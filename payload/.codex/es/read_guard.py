#!/usr/bin/env python3
"""Optional Codex PreToolUse large-read guard. Default AUDIT, explicit deny opt-in.

Deliberately recognizes only a small grammar. Unknown shell/MCP forms pass.
This is a cost guardrail, NOT an access-control or security boundary.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
from typing import Any
from evidence import EvidenceError, compact_json, source_bytes

MESSAGE=('Large raw read exceeds the local byte budget. Use gateway.py search with an exact '
         'literal, evidence.py read with a narrow range, or a bounded repo_reader question. '
         'For necessary edits/reasoning read exact adjacent ranges; do not replace source with a summary.')


def recognized_read(event: dict[str,Any], root: Path) -> tuple[int|None,str]:
    if event.get('hook_event_name')!='PreToolUse':return None,'not_pretool'
    name=event.get('tool_name');args=event.get('tool_input')
    if not isinstance(args,dict):return None,'unknown_arguments'
    cwd=Path(event.get('cwd',str(root))).resolve(strict=True)
    def get(path: Any) -> tuple[bytes,list[str]]:
        if not isinstance(path,str):raise EvidenceError('invalid path')
        absolute=(cwd/path).resolve(strict=True) if not Path(path).is_absolute() else Path(path).resolve(strict=True)
        relative=absolute.relative_to(root.resolve(strict=True)).as_posix()
        return source_bytes(root,relative)[:2]
    if name=='Read':
        raw,lines=get(args.get('file_path'))
        offset=args.get('offset',1);limit=args.get('limit')
        if type(offset) is not int or offset<1:return None,'unknown_offset'
        if limit is None:return len(raw),'unbounded_read'
        if type(limit) is not int or limit<1:return None,'unknown_limit'
        return len('\n'.join(lines[offset-1:offset-1+limit]).encode('utf-8')),'bounded_read'
    if name not in {'Bash','exec_command'}:return None,'unrecognized_tool'
    command=args.get('command') if name=='Bash' else args.get('cmd')
    if not isinstance(command,str) or len(command)>16000:return None,'unknown_command'
    # Refuse to *classify*, never execute, shell expansions/compound expressions.
    # A pipeline is UNKNOWN, not assumed safe merely because it contains grep/head.
    if any(x in command for x in ('$','`','\n','\r')):return None,'compound_or_expansion'
    lex=shlex.shlex(command,posix=True,punctuation_chars=';&|<>()');lex.whitespace_split=True;lex.commenters=''
    words=list(lex)
    if not words:return None,'empty'
    if any(re.fullmatch(r'[;&|<>()]+',x) for x in words):return None,'compound_or_pipeline'
    op=words[0];args_=words[1:]
    if op not in {'cat','head','tail','sed'}:return None,'unrecognized_command'
    if op=='cat':
        if args_[:1]==['--']:args_=args_[1:]
        if not args_ or len(args_)>12 or any(x.startswith('-') or any(y in x for y in '*?[]') for x in args_):
            return None,'unknown_cat_form'
        return sum(len(get(x)[0]) for x in args_),'whole_file_cat'
    if op in {'head','tail'}:
        n=10;unit='lines'
        if args_[:1] in (['-n'],['-c']):
            if len(args_)<3 or re.fullmatch(r'[0-9]+',args_[1]) is None:return None,'unknown_count'
            n=int(args_[1]);unit='bytes' if args_[0]=='-c' else 'lines';args_=args_[2:]
        if args_[:1]==['--']:args_=args_[1:]
        if len(args_)!=1 or args_[0].startswith('-'):return None,'unknown_head_tail_form'
        raw,lines=get(args_[0])
        if n==0:return 0,'empty_read'
        if unit=='bytes':return min(n,len(raw)),'bounded_bytes'
        selected=lines[:n] if op=='head' else lines[-n:]
        return len('\n'.join(selected).encode()),'bounded_lines'
    if op=='sed' and len(args_)==3 and args_[0]=='-n':
        m=re.fullmatch(r'(\d+),(\d+)p',args_[1])
        if m:
            start,end=map(int,m.groups())
            if start<1 or end<start:return None,'invalid_range'
            _,lines=get(args_[2]);return len('\n'.join(lines[start-1:end]).encode()),'sed_range'
    return None,'unrecognized_sed_form'


def decision(event: dict[str,Any], root: Path, limit: int=12000, mode: str='audit') -> tuple[dict[str,Any],dict[str,Any]]:
    if limit<1 or mode not in {'audit','deny'}:raise EvidenceError('invalid guard policy')
    try:count,kind=recognized_read(event,root)
    except (EvidenceError,OSError,ValueError,TypeError,RuntimeError):count,kind=None,'uninspectable'
    excessive=count is not None and count>limit
    record=dict(at=time.time(),tool=event.get('tool_name'),classification=kind,
                inspected_bytes=count,limit_bytes=limit,would_deny=excessive,mode=mode,
                source_and_command_logged=False)
    if excessive and mode=='deny':
        return {'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny',
                                     'permissionDecisionReason':MESSAGE}},record
    # No allow decision (which could grant permission), no extra context on every call.
    return {},record


def append_audit(path: Path, record: dict[str,Any]) -> None:
    import fcntl
    if path.is_symlink():raise EvidenceError('audit file cannot be a symlink')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        os.fchmod(fd,0o600);fcntl.flock(fd,fcntl.LOCK_EX)
        os.write(fd,(compact_json(record)+'\n').encode())
    finally:os.close(fd)


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--mode',choices=['audit','deny'],default='audit');p.add_argument('--max-bytes',type=int,default=12000)
    p.add_argument('--audit-file',type=Path);a=p.parse_args()
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise EvidenceError('hook input too large')
        e=json.loads(raw)
        if not isinstance(e,dict):raise EvidenceError('hook input must be object')
        result,record=decision(e,a.root,a.max_bytes,a.mode)
        if a.audit_file:
            try:append_audit(a.audit_file,record)
            except (OSError,EvidenceError):pass  # Audit failure must not accidentally approve.
        print(compact_json(result));return 0
    except (OSError,ValueError,UnicodeError):
        # Unknown/malformed hook inputs fail open: never claim a security guarantee.
        print('{}');return 0
if __name__=='__main__':raise SystemExit(main())
