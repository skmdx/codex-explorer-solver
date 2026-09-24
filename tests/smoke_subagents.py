#!/usr/bin/env python3
"""Opt-in real Codex/Claude/Gemini test of direct, blocking AGY delegation."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--codex', default='codex')
    args = parser.parse_args()
    root = args.out_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = root/'repo'; repo.mkdir()
    source = repo/'example.py'
    source.write_text('def normalize(text):\n    return text.strip()\n\ndef dispatch(text):\n    return normalize(text)\n')
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'add','example.py'],check=True)
    bin_dir = root/'bin'; bin_dir.mkdir()
    agy = shutil.which('agy')
    if not agy: raise RuntimeError('authenticated agy is required')
    wrapper = bin_dir/'agy'
    wrapper.write_text('#!/usr/bin/env python3\nimport os,sys,time\n'
                       "if 'stream-json' in sys.argv: time.sleep(65)\n"
                       f'os.execv({agy!r}, [{agy!r}, *sys.argv[1:]])\n')
    wrapper.chmod(0o755)
    prompt = f'''This is an authorized live regression test of the installed agy-subagents MCP.
First use functions.exec to print typeof tools.mcp__agy_subagents__run, and whether
ALL_TOOLS contains it. Attempt that nested function with empty arguments, catch the
TypeError and print NESTED_BLOCKED. Do not use any shell fallback.
Then call the direct agy-subagents models tool to check available model names.
Call the direct run tool with model="claude-sonnet-4-6", mode="review", repo={str(repo)!r},
scratch_dir={str(root)!r}, task="Review whether normalize removes surrounding spaces in example.py. Name the relevant function. Keep the review brief. Do not edit files."
After that finishes, call the direct run tool with default Gemini model, mode="edit",
repo={str(repo)!r}, scratch_dir={str(root)!r}, task="Edit only example.py so normalize strips surrounding spaces and lowercases the result with str.lower. Check dispatch(' HELLO ') == 'hello'. Do not commit or push."
Wait for each final response. Do not poll, use a background job, retry, or change deadlines.
Do not read or edit the fixture yourself. Report both final statuses and stop.'''
    env = dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
    with (root/'events.jsonl').open('w') as out, (root/'stderr.log').open('w') as err:
        result = subprocess.run([args.codex,'exec','--enable','code_mode','--enable','code_mode_only',
                                 '--json','--dangerously-bypass-approvals-and-sandbox',prompt],
                                cwd=repo,env=env,stdout=out,stderr=err,stdin=subprocess.DEVNULL)
    assert result.returncode == 0, root/'stderr.log'
    events = [json.loads(l) for l in (root/'events.jsonl').read_text().splitlines()]
    thread = next(e['thread_id'] for e in events if e['type']=='thread.started')
    sessions = Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'sessions'
    log = next(sessions.rglob(f'*{thread}*.jsonl'))
    records = [json.loads(l) for l in log.read_text().splitlines()]
    calls = [(i,d) for i,d in enumerate(records) if d.get('payload',{}).get('type') in ('function_call','custom_tool_call')]
    runs = [(i,d) for i,d in calls if d['payload'].get('name')=='run']
    assert len(runs)==2
    durations=[]
    for i,d in runs:
        call=d['payload']; assert call.get('namespace')=='mcp__agy_subagents'
        end,finished=next((j,r) for j,r in enumerate(records) if j>i and r.get('payload',{}).get('call_id')==call['call_id'] and r['payload'].get('type')=='function_call_output')
        assert not any(i<j<end for j,_ in calls)
        durations.append((datetime.fromisoformat(finished['timestamp'])-datetime.fromisoformat(d['timestamp'])).total_seconds())
    assert min(durations)>=65
    assert not any(d['payload'].get('name') in ('wait','write_stdin','exec_command') for _,d in calls)
    outputs='\n'.join(json.dumps(d['payload'].get('output','')) for d in records if d.get('payload',{}).get('type','').endswith('call_output'))
    assert 'NESTED_BLOCKED' in outputs and 'undefined' in outputs
    assert 'Script running with cell ID' not in outputs
    reports=[json.loads(p.read_text()) for p in root.glob('agy-subagent-*/report.json')]
    assert len(reports)==2 and all(r['status']=='completed' for r in reports), reports
    assert {r['mode'] for r in reports}=={'review','edit'}
    assert {r['model'] for r in reports}=={'claude-sonnet-4-6','gemini-3.8-flash-high'}
    subprocess.run([sys.executable,'-c',"from example import dispatch; assert dispatch(' HELLO ') == 'hello'"],
                   cwd=repo,check=True)
    summary=dict(passed=True,session_log=str(log),run_calls=2,poll_calls=0,
                 nested_call_rejected=True,durations=durations,injected_delay_seconds=65,
                 models=[r['model'] for r in reports],edit_behavior_verified=True)
    (root/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__': main()
