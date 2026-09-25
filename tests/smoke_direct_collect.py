#!/usr/bin/env python3
"""Exercise the installed plugin through real Codex and AGY (uses both quotas).

Requires the updated plugin. Delays the real AGY executable for 65 seconds to
cross the host's former observation windows; no model or MCP response is mocked.
The output directory must be outside this repository, under workspace tmp.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--codex', default='codex')
    args = parser.parse_args()
    root = args.out_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = root / 'repo'
    repo.mkdir()
    (repo / 'example.py').write_text('def normalize(text):\n    return text.strip()\n\ndef dispatch(text):\n    return normalize(text)\n')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', 'example.py'], check=True)
    bin_dir = root / 'bin'
    bin_dir.mkdir()
    agy = shutil.which('agy')
    if not agy:
        raise RuntimeError('agy must be installed and authenticated')
    wrapper = bin_dir / 'agy'
    wrapper.write_text('#!/usr/bin/env python3\nimport os,sys,time\n'
                       "if 'stream-json' in sys.argv: time.sleep(65)\n"
                       f'os.execv({agy!r}, [{agy!r}, *sys.argv[1:]])\n')
    wrapper.chmod(0o755)
    prompt = f'''This is an authorized live regression test of the installed explore-solve plugin.
First use functions.exec with yield_time_ms 1. Print typeof tools.mcp__explore_solve__collect
and whether ALL_TOOLS contains it. Then actually attempt to call that nested function with
empty arguments, catch the TypeError, and print NESTED_BLOCKED. Do not invoke a shell fallback.
Next call the direct explore-solve collect MCP tool once with repo={str(repo)!r},
scratch_dir={str(root)!r}, navigation=null, known_findings="",
question="How does dispatch normalize its input?",
evidence_needed=[{{"fact":"dispatch definition and the normalize function it calls","scope":["example.py"]}}].
Use real AGY. After collection succeeds, call read_evidence for the relevant returned IDs,
then answer from the originals. Do not read example.py through other tools or launch subprocesses.
Do not retry collection. This is a read-only test.'''
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
    started = time.time()
    with (root / 'events.jsonl').open('w') as out, (root / 'stderr.log').open('w') as err:
        result = subprocess.run([args.codex, 'exec', '--enable', 'code_mode', '--enable',
                                 'code_mode_only', '--json',
                                 '--dangerously-bypass-approvals-and-sandbox', prompt],
                                cwd=repo, env=env, stdout=out, stderr=err)
    verify(root, result.returncode, started)


def verify(root, returncode, started):
    """Check recorded host calls, not the test model's final self-report."""
    events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
    thread = next(e['thread_id'] for e in events if e['type'] == 'thread.started')
    sessions = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions'
    log = next(sessions.rglob(f'*{thread}*.jsonl'))
    records = [json.loads(line) for line in log.read_text().splitlines()]
    calls = [(i, d) for i, d in enumerate(records)
             if d.get('payload', {}).get('type') in ('function_call', 'custom_tool_call')]
    collect = [(i, d) for i, d in calls
               if d['payload'].get('name', '').endswith('collect')]
    assert returncode == 0, root / 'stderr.log'
    assert len(collect) == 1, [(d['payload'].get('name')) for _, d in calls]
    index, call = collect[0]
    assert call['payload'].get('namespace') == 'mcp__explore_solve', 'not a direct MCP call'
    call_id = call['payload']['call_id']
    finish_index, finish = next((i, d) for i, d in enumerate(records[index + 1:], index + 1)
                                if d.get('payload', {}).get('call_id') == call_id
                                and d['payload'].get('type') == 'function_call_output')
    from datetime import datetime
    duration = (datetime.fromisoformat(finish['timestamp']) -
                datetime.fromisoformat(call['timestamp'])).total_seconds()
    assert duration >= 65, duration
    assert not any(index < i < finish_index for i, _ in calls), 'tool call during collection'
    assert not any(d['payload'].get('name') in ('wait', 'write_stdin') for _, d in calls)
    assert any(d['payload'].get('name', '').endswith('read_evidence') for _, d in calls)
    outputs = '\n'.join(json.dumps(d['payload'].get('output', '')) for d in records
                        if d.get('payload', {}).get('type', '').endswith('call_output'))
    assert 'NESTED_BLOCKED' in outputs and 'undefined' in outputs, 'nested call not rejected'
    assert 'Script running with cell ID' not in outputs, 'host yielded a cell'
    assert 'return text.strip()' in outputs, 'original source not returned'
    summary = {'passed': True, 'session_log': str(log), 'collection_seconds': duration,
               'elapsed_seconds': round(time.time() - started, 2),
               'collect_calls': 1, 'poll_calls': 0, 'nested_call_rejected': True,
               'injected_delay_seconds': 65}
    (root / 'verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
