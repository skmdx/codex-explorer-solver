#!/usr/bin/env python3
"""Print a hook fragment for manual merge. Never changes hooks/config/trust settings."""
import argparse
import json
import shlex
import sys
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--repo',type=Path,default=Path.cwd())
p.add_argument('--mode',choices=['audit','deny'],default='audit')
p.add_argument('--audit-file',type=Path,required=True)
p.add_argument('--max-bytes',type=int,default=12000)
a=p.parse_args();root=a.repo.resolve(strict=True)
script=root/'.codex/es/read_guard.py'
if not script.is_file() or a.max_bytes<1:raise SystemExit('install kit first and use a positive byte limit')
audit=a.audit_file.expanduser().resolve()
if audit==root or root in audit.parents:raise SystemExit('audit-file must be outside the repository')
command=shlex.join([sys.executable,str(script),'--root',str(root),'--mode',a.mode,
                    '--max-bytes',str(a.max_bytes),'--audit-file',str(audit)])
print(json.dumps({'hooks':{'PreToolUse':[{'matcher':'^(Bash|Read)$','hooks':[
    {'type':'command','command':command,'timeout':5}]}]}},indent=2))
