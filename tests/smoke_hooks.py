#!/usr/bin/env python3
"""Local real-subprocess + synthetic Codex-hook-envelope test. No Codex/model/API.

The intentionally failing unittest below is a fixture, not a kit test failure.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

KIT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(KIT));sys.path.insert(0,str(KIT/'payload/.codex/es'))
from configure_hooks import configure
from hook_store import Store, canonical
from hook_artifacts import search, read_lines


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    with tempfile.TemporaryDirectory() as td:
        base=Path(td);repo=base/'repo';repo.mkdir();(repo/'.git').mkdir()
        (repo/'.codex').mkdir()
        config=b'model = "user-choice-preserved"\n';agents=b'Preserve user conventions.\n'
        original_hooks={'description':'user-owned','hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo original-user-hook'}]}]}}
        hp=repo/'.codex/hooks.json';hp.write_text(json.dumps(original_hooks))
        cp=repo/'.codex/config.toml';cp.write_bytes(config);(repo/'AGENTS.md').write_bytes(agents)
        old_hooks=hp.read_bytes()
        shutil.copytree(KIT/'payload/.codex/es',repo/'.codex/es',ignore=shutil.ignore_patterns('__pycache__'))
        assert hp.read_bytes()==old_hooks and cp.read_bytes()==config and (repo/'AGENTS.md').read_bytes()==agents
        state=base/'state'
        configure(repo,state,mode='audit',apply=True,backup=base/'hooks-backup-1')
        configure(repo,state,mode='enforce',apply=True,backup=base/'hooks-backup-2')
        assert json.loads(hp.read_text())['hooks']['Stop']==original_hooks['hooks']['Stop']
        tests=repo/'tests';tests.mkdir()
        (tests/'test_fixture.py').write_text('''import unittest
class Fixture(unittest.TestCase):
    def test_intentional_failure(self):
        for i in range(3000):
            print(f"progress record {i}: " + "abcdefghijklmnopqrstuvwxyz" * 2)
        self.assertEqual(1, 2, "SMOKE_SENTINEL_FAILURE")
''')
        run=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=repo,capture_output=True,text=True)
        assert run.returncode==1 and 'SMOKE_SENTINEL_FAILURE' in run.stderr
        response={'stdout':run.stdout,'stderr':run.stderr,'exit_code':run.returncode}
        event={'hook_event_name':'PostToolUse','session_id':'smoke-session','turn_id':'smoke-turn','tool_use_id':'smoke-call',
               'tool_name':'Bash','tool_input':{'command':'python3 -m unittest discover -s tests -v'},
               'tool_response':response,'cwd':str(repo)}
        cmd=[sys.executable,str(repo/'.codex/es/hook_runtime.py'),'--root',str(repo),'--state-dir',str(state),'--mode','enforce']
        started=time.monotonic()
        h=subprocess.run(cmd,input=json.dumps(event),text=True,capture_output=True,check=True)
        elapsed=time.monotonic()-started
        out=json.loads(h.stdout);assert out['continue'] is False and 'decision' not in out
        detail=json.loads(out['stopReason']);aid=detail['artifact_id'];store=Store(state,repo)
        assert store.load(aid)==response
        assert detail['status']['response.exit_code']==1 and detail['success_claimed'] is False
        found=search(store,aid,'stderr','AssertionError',20)
        assert found['line_numbers']
        line=found['line_numbers'][0]
        assert 'SMOKE_SENTINEL_FAILURE' in read_lines(store,aid,'stderr',line,line)['source']
        # Actual installed recovery command must execute successfully.
        import shlex
        info=subprocess.run(shlex.split(detail['recovery_command']),text=True,capture_output=True,check=True)
        assert json.loads(info.stdout)['artifact_id']==aid
        configure(repo,remove=True,apply=True,backup=base/'hooks-backup-3')
        assert json.loads(hp.read_text())['hooks']['Stop']==original_hooks['hooks']['Stop']
        assert cp.read_bytes()==config and (repo/'AGENTS.md').read_bytes()==agents
        before=len(canonical(response));after=len(canonical(out))
        result={'kind':'local_subprocess_and_synthetic_hook_envelope_smoke',
                'real_codex_integration':False,'model_calls':0,
                'existing_agents_config_and_unrelated_hooks_preserved':True,
                'audit_to_enforce_update_and_remove_verified':True,
                'original_command_executions':1,'intentionally_failing_fixture_exit_code':run.returncode,
                'failure_preserved_in_feedback':True,'full_observed_response_value_recovered':True,
                'recover_failure_via_literal_search_and_exact_line':True,
                'actual_recovery_command_verified':True,
                'observed_response_canonical_json_bytes':before,
                'replacement_hook_canonical_json_bytes':after,
                'local_representation_reduction_percent':round((1-after/before)*100,2),
                'one_hook_cli_wall_seconds_this_environment':round(elapsed,4),
                'token_savings':None,'billing_savings':None,
                'important':'The fixture measures JSON byte size, not actual Codex/model input, token usage, '
                            'cached usage, or total task cost. The envelope is constructed locally.'}
        print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
