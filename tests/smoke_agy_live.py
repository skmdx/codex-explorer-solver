#!/usr/bin/env python3
"""Opt-in LIVE AGY test on synthetic source only. Consumes your AGY quota.

Not part of the unit tests. Keeps a private temporary test directory and logs
for inspection. Does not modify your repository/global AGY/Codex settings.
"""
import argparse,json,os
from pathlib import Path
import subprocess,sys,tempfile
KIT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(KIT));sys.path.insert(0,str(KIT/'payload/.codex/es'))
from install import install
import budget

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-models',action='store_true',help='explicitly authorize two live AGY calls')
    p.add_argument('--agy',default='agy');p.add_argument('--timeout',type=float,default=120)
    a=p.parse_args()
    if not a.run_models:
        p.error('--run-models is required; this check consumes real provider quota')
    base=Path(tempfile.mkdtemp(prefix='codex-agy-live-'));os.chmod(base,0o700)
    repo=base/'repo';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True)
    install(repo,apply=True);(repo/'src').mkdir()
    (repo/'src/example.py').write_text('def normalize(text):\n    return text.strip()\n\ndef dispatch(text):\n    return normalize(text)\n')
    subprocess.run(['git','-C',str(repo),'add','src'],check=True)
    task=base/'task.txt';task.write_text('Find dispatch and the function it calls to normalize text. Return exact source references only.\n')
    state=base/'budget';budget.initialize(state,repo,task.read_bytes())
    reports=[];ok=True
    for name,extra in [('reader',['--mode','reader','--path','src/example.py']),('explorer',[])]:
        cmd=[sys.executable,str(repo/'.codex/es/locate.py'),'--repo',str(repo),'--task-file',str(task),
             '--state-dir',str(state),'--out-dir',str(base/name),'--agy',a.agy,'--timeout',str(a.timeout),*extra]
        r=subprocess.run(cmd,capture_output=True,text=True)
        reports.append({'role':name,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        if r.returncode:ok=False;break
    result={'ok':ok,'live_model_calls_authorized':True,'synthetic_source_only':True,
            'artifacts_directory':str(base),'results':reports,'savings_measured':False}
    (base/'live-smoke-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if ok else 1
if __name__=='__main__':raise SystemExit(main())
