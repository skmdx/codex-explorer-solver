#!/usr/bin/env python3
"""Run an explicitly supplied command; retain raw logs, emit bounded exact tails.

NOT a sandbox: the command has your normal permissions and may edit project files.
No shell interpretation is used unless YOU explicitly invoke a shell.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from evidence import EvidenceError, compact_json
from locate import stop_process_group, write_private


def tail(path: Path, budget: int = 1536) -> dict[str, Any]:
    size = path.stat().st_size
    with path.open('rb') as stream:
        stream.seek(max(0,size-budget))
        raw = stream.read(budget)
    # Never pretend replacement characters are original evidence.
    try:
        text = raw.decode('utf-8')
        encoding = 'utf-8'
    except UnicodeDecodeError:
        text = None
        encoding = 'non_utf8_or_split_boundary; use raw log'
    return {'path':str(path),'total_bytes':size,'preview_start_byte':max(0,size-budget),
            'preview_end_byte_exclusive':size,'preview_is_partial':size>budget,
            'preview_encoding':encoding,'tail':text}


def digest_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def capture(root: Path, out: Path, command: list[str], timeout: float=300,
            log_limit: int=16*1024*1024) -> tuple[dict[str,Any],int]:
    if os.name != 'posix': raise EvidenceError('capture requires Linux/macOS/WSL')
    if not command or not math.isfinite(timeout) or timeout<=0 or log_limit<1024:
        raise EvidenceError('provide a command and positive finite timeout/log limit')
    root=root.resolve(strict=True); out=out.resolve()
    if not root.is_dir() or out==root or root in out.parents:
        raise EvidenceError('capture out-dir must be outside the working repository')
    out.mkdir(mode=0o700,parents=True,exist_ok=False); os.chmod(out,0o700)
    start=time.monotonic(); reason=None; code=127; proc=None
    stdout=out/'stdout.log'; stderr=out/'stderr.log'
    with stdout.open('xb') as so, stderr.open('xb') as se:
        os.chmod(stdout,0o600);os.chmod(stderr,0o600)
        try:
            proc=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=so,stderr=se,
                                  start_new_session=True)
            while proc.poll() is None:
                if time.monotonic()-start>=timeout: reason='local_deadline'
                elif os.fstat(so.fileno()).st_size+os.fstat(se.fileno()).st_size>log_limit: reason='log_budget'
                if reason:
                    stop_process_group(proc);break
                time.sleep(0.05)
            code=proc.wait(timeout=3)
            if os.fstat(so.fileno()).st_size+os.fstat(se.fileno()).st_size>log_limit:
                reason=reason or 'log_budget'
        except KeyboardInterrupt:
            if proc is not None:stop_process_group(proc)
            reason='interrupted';code=-2
        except OSError as exc:
            se.write(str(exc).encode('utf-8'));reason='launch_failed'
        except BaseException:
            if proc is not None:stop_process_group(proc)
            raise
    wrapped=124 if reason=='local_deadline' else 130 if reason=='interrupted' else 125 if reason=='log_budget' else (code if code>=0 else 128-code)
    result: dict[str,Any]={'kind':'captured_command','returncode':code,'wrapper_exit_code':wrapped,
            'record_path':str(out/'capture.json'),
            'termination_reason':reason,'elapsed_seconds':round(time.monotonic()-start,3),
            'success_claimed':False,'raw_logs_complete_for_observed_process':reason is None,
            'preview_policy':'exact_tail_not_semantic_summary',
            'stdout':tail(stdout,256 if wrapped==0 else 1536),
            'stderr':tail(stderr,256 if wrapped==0 else 1536)}
    for key in ('stdout','stderr'):result[key]['sha256']=digest_file(out/f'{key}.log')
    if len(compact_json(result).encode()) > 6144:
        for key in ('stdout','stderr'):
            result[key]['tail'] = None
            result[key]['preview_is_partial'] = True
            result[key]['preview_encoding'] = 'omitted: serialized preview exceeds byte budget'

    # Full invocation may contain secrets; retain privately, not in model-facing stdout.
    record: dict[str,Any]=dict(result,command=command,cwd=str(root))
    write_private(out/'capture.json',json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    if wrapped==0:
        # Keep audit metadata in capture.json; model-facing success output needs
        # only the exit status, short exact previews, and recoverable log paths.
        result={key:result[key] for key in ('kind','returncode','elapsed_seconds','record_path')}
        for key in ('stdout','stderr'):
            result[key]={field:record[key][field] for field in ('path','total_bytes','tail')}
    return result,wrapped


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd());p.add_argument('--out-dir',type=Path,required=True)
    p.add_argument('--timeout',type=float,default=300);p.add_argument('--log-limit-bytes',type=int,default=16*1024*1024)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    cmd=a.command[1:] if a.command[:1]==['--'] else a.command
    try:
        result,code=capture(a.repo,a.out_dir,cmd,a.timeout,a.log_limit_bytes)
        print(compact_json(result));return code
    except (EvidenceError,OSError,ValueError,subprocess.SubprocessError) as exc:
        print(compact_json({'ok':False,'error':str(exc)}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
