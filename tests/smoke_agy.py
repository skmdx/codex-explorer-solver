#!/usr/bin/env python3
"""Actual v3 ZIP migration + explicit fake-AGY subprocess integration. No Google calls."""
import argparse, hashlib, json, os
from pathlib import Path, PurePosixPath
import subprocess,sys,tempfile,zipfile
KIT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(KIT));sys.path.insert(0,str(KIT/'payload/.codex/es'))
import budget
from upgrade import upgrade
from install import install
from configure_hooks import configure

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--v3-zip',type=Path,required=True);a=p.parse_args()
    with tempfile.TemporaryDirectory() as td:
        base=Path(td);repo=base/'repo';repo.mkdir()
        subprocess.run(['git','init','-q',str(repo)],check=True)
        with zipfile.ZipFile(a.v3_zip) as z:
            for name in z.namelist():
                prefix='codex-explorer-solver-v3/payload/'
                if not name.startswith(prefix) or name.endswith('/'):continue
                rel=PurePosixPath(name[len(prefix):]);assert not rel.is_absolute() and '..' not in rel.parts
                dest=repo/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(name))
        protected={'.codex/config.toml':b'# User settings\nmodel = "user-model"\n',
                   'AGENTS.md':b'Existing instructions, never overwrite.\n',
                   '.codex/agents/user_agent.toml':b'name = "untouched-user-agent"\n'}
        for name,raw in protected.items():(repo/name).write_bytes(raw)
        configure(repo,base/'hook-state',mode='audit',native_limit=2,apply=True,backup=base/'hooks-backup')
        for name in ('.codex/hooks.json','.codex/es/hooks-installed.json'):
            protected[name]=(repo/name).read_bytes()
        runtime_before=(repo/'.codex/es/hook_runtime.py').read_bytes()
        plan=upgrade(repo)
        assert sum(x['action']=='delete' for x in plan)==3
        upgrade(repo,True,base/'backup')
        for name,raw in protected.items():assert (repo/name).read_bytes()==raw
        assert (repo/'.codex/es/hook_runtime.py').read_bytes()==runtime_before
        for name in ('repo_explorer','repo_reader','repo_deep_explorer'):
            assert not (repo/f'.codex/agents/{name}.toml').exists()
            assert (base/f'backup/.codex/agents/{name}.toml').is_file()
        assert upgrade(repo)==[]
        # Migration must refuse an edited retired definition before making changes.
        edited=repo/'.codex/agents/repo_explorer.toml';edited.write_text('custom user content\n')
        try:upgrade(repo)
        except ValueError:pass
        else:raise AssertionError('customized retired role was not refused')
        edited.unlink()
        (repo/'src').mkdir();source=repo/'src/example.py';source.write_text('def f():\n    return 1\n')
        subprocess.run(['git','-C',str(repo),'add','src'],check=True)
        source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
        task=base/'task.txt';task.write_text('Locate f in the source.')
        state=base/'task-budget';budget.initialize(state,repo,task.read_bytes())
        def run(name,extra=[]):
            cmd=[sys.executable,str(repo/'.codex/es/locate.py'),'--repo',str(repo),
                 '--task-file',str(task),'--state-dir',str(state),'--out-dir',str(base/name),
                 '--agy',str(KIT/'tests/fixtures/fake_agy.py'),'--json',*extra]
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
            assert result.returncode==0,result.stderr+result.stdout
            report=json.loads(result.stdout)
            assert 'return 1' in report['evidence']['primary'][0]['source']
            return json.loads((base/name/'metrics.json').read_text())
        initial=run('explorer');deep=run('deep',['--deep'])
        assert initial['effective_model']=='gemini-3.8-flash-high'
        assert deep['effective_model']=='gemini-3.8-flash-high'
        assert budget.status(state)['attempts']==2
        assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
        report=dict(kind='v3_zip_upgrade_and_fake_agy_integration',real_agy_integration=False,
                    real_codex_integration=False,model_calls=0,
                    fake_cli_version=initial['agy_version'],
                    v3_zip_sha256=hashlib.sha256(a.v3_zip.read_bytes()).hexdigest(),
                    old_native_roles_backed_up_and_removed=True,edited_old_role_refused=True,
                    upgrade_idempotent=True,user_config_agents_and_registered_hooks_byte_preserved=True,
                    hook_runtime_byte_unchanged=True,original_source_unchanged=True,
                    validated_handoffs=2,reported_models=[initial['effective_model'],deep['effective_model']],
                    fake_usage_counted_once=True,worker_attempts_counted=2,
                    performance_or_savings_measured=False)
        print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
