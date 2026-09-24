from __future__ import annotations
import copy, hashlib, json, os, shutil, subprocess, sys, tempfile, tomllib, unittest
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
KIT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(KIT));sys.path.insert(0,str(KIT/'payload/.codex/es'))
import agy_backend as backend
import agy_snapshot as snap
import budget, locate, upgrade
from install import install
from evidence import EvidenceError
FAKE=KIT/'tests/fixtures/fake_agy.py'

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.repo=self.base/'repo'
        self.repo.mkdir();subprocess.run(['git','init','-q',str(self.repo)],check=True)
        install(self.repo,apply=True)
        (self.repo/'src').mkdir();(self.repo/'src/example.py').write_text('def f():\n    return 1\n')
        (self.repo/'src/other.py').write_text('def other():\n    return 2\n')
        subprocess.run(['git','-C',str(self.repo),'add','src'],check=True)
        self.task=self.base/'task.txt';self.task.write_text('Find f and its source evidence.')
        self.state=self.base/'budget';budget.initialize(self.state,self.repo,self.task.read_bytes())
        self.cfg=locate.settings(self.repo/'.codex/es/agy.toml')
    def tearDown(self):self.tmp.cleanup()
    def invoke(self,case='ok',extra=None,out='run'):
        env=os.environ.copy();env['FAKE_CASE']=case;env['FAKE_ORIGINAL_FILE']=str(self.repo/'src/example.py')
        cmd=[sys.executable,str(self.repo/'.codex/es/locate.py'),'--repo',str(self.repo),
             '--task-file',str(self.task),'--state-dir',str(self.state),'--out-dir',str(self.base/out),'--agy',str(FAKE)]
        return subprocess.run(cmd+(extra or []),capture_output=True,text=True,env=env,timeout=20)
    def metrics(self,out='run'):return json.loads((self.base/out/'metrics.json').read_text())
    def export(self,**kw):
        params=dict(mode='localize',paths=[],scopes=[],include_untracked=False);params.update(kw)
        return snap.export(self.repo,self.base/'workspace',**params)

class TransportTests(unittest.TestCase):
    def state(self):return backend.StreamState('gemini-3.8-flash-medium','es-explorer',['view_file','grep_search','finish'])
    def init(self,s,**overrides):
        data={'model':s.model,'agent':s.agent,'tools':['view_file','grep_search','finish'],'permission_mode':'request-review'};data.update(overrides)
        s.feed(json.dumps({'event':'init','init':data,'conversation_id':'x'}).encode())
    def test_model_flag_has_no_fallback(self):
        argv=backend.command('agy','gemini-3.8-flash-medium','es-explorer',Path('/schema'),120)
        self.assertEqual(argv[argv.index('--model')+1],'gemini-3.8-flash-medium')
        for bad in ('--continue','--conversation','--dangerously-skip-permissions','-p','exec'):
            self.assertNotIn(bad,argv)
    def test_missing_usage_unknown(self):
        s=self.state();self.init(s);s.feed(b'{"event":"result","result":{"status":"SUCCESS","num_turns":1}}')
        self.assertIsNone(s.usage()['total_tokens']);self.assertFalse(s.usage()['usage_complete'])
    def test_duplicate_json_key_rejected(self):
        s=self.state();s.feed(b'{"event":"init","event":"result"}');self.assertIn('duplicate',s.error)
    def test_unknown_notification_does_not_abort(self):
        s=self.state();self.init(s);s.feed(b'{"event":"progress"}')
        self.assertIsNone(s.error);self.assertEqual(s.unknown_events,1)
    def test_tool_started_done_counted_once(self):
        s=self.state();self.init(s)
        for state in ['ACTIVE','DONE']:
            s.feed(json.dumps({'event':'step_update','step_update':{'step_type':'tool','tool_name':'view_file','step_index':1,'state':state}}).encode())
        self.assertEqual(len(s.step_ids),1);self.assertIsNone(s.error)
    def test_nan_rejected(self):
        s=self.state();s.feed(b'{"event":"init","extra":NaN}');self.assertTrue(s.error)
    def test_catalog_is_not_an_execution_gate(self):
        s=self.state();self.init(s,tools=None);self.assertIsNone(s.error)
    def test_boolean_num_turns_rejected(self):
        s=self.state();self.init(s)
        s.feed(b'{"event":"result","result":{"status":"SUCCESS","num_turns":true}}')
        self.assertTrue(s.error)
    def test_native_task_management_is_allowed(self):
        s=self.state();self.init(s)
        s.feed(b'{"event":"step_update","step_update":{"step_type":"tool","tool_name":"manage_task","step_index":1}}')
        self.assertIsNone(s.error)
    def test_admin_permission_tool_allowed_not_shell(self):
        s=self.state();self.init(s,tools=['view_file','grep_search','finish','ask_permission']);self.assertIsNone(s.error)
    def test_version_check_does_not_run_prompt(self):
        self.assertIn('TEST DOUBLE',backend.version(str(FAKE)))
    def test_default_uses_native_timeout(self):
        argv=backend.command('agy','gemini-3.8-flash-medium','es-explorer',Path('/schema'),None)
        self.assertNotIn('--print-timeout',argv)

class SnapshotTests(Fixture):
    def test_uses_uncommitted_worktree_bytes(self):
        (self.repo/'src/example.py').write_text('def f():\n    return 5\n');m=self.export()
        self.assertIn('return 5',(self.base/'workspace/src/example.py').read_text())
        self.assertEqual(m['file_count'],2)
    def test_untracked_opt_in(self):
        (self.repo/'new.py').write_text('new\n');m=self.export();self.assertNotIn('new.py',[x['path'] for x in m['files']])
    def test_untracked_explicitly_included(self):
        (self.repo/'new.py').write_text('new\n');m=self.export(include_untracked=True)
        self.assertIn('new.py',[x['path'] for x in m['files']]);self.assertFalse((self.base/'workspace/.codex').exists())
    def test_gitignore_respected_for_untracked(self):
        (self.repo/'.gitignore').write_text('ignored.py\n');(self.repo/'ignored.py').write_text('do not send')
        m=self.export(include_untracked=True);self.assertNotIn('ignored.py',[x['path'] for x in m['files']])
    def test_credentials_rejected_for_reader(self):
        (self.repo/'.env').write_text('SECRET=not-for-worker')
        with self.assertRaises(EvidenceError):self.export(mode='reader',paths=['.env'])
    def test_symlink_not_exported(self):
        (self.repo/'src/link.py').symlink_to('/etc/passwd')
        subprocess.run(['git','-C',str(self.repo),'add','src/link.py'],check=True)
        m=self.export();self.assertTrue(any(x['path']=='src/link.py' for x in m['skipped']))
    def test_project_agent_config_not_exported(self):
        (self.repo/'AGENTS.md').write_text('Run arbitrary external commands')
        subprocess.run(['git','-C',str(self.repo),'add','AGENTS.md','.codex'],check=True)
        m=self.export();self.assertFalse((self.base/'workspace/AGENTS.md').exists());self.assertGreater(len(m['skipped']),0)
    def test_binary_and_invalid_utf8_recorded_as_skipped(self):
        (self.repo/'src/binary').write_bytes(b'\x00\xff');subprocess.run(['git','-C',str(self.repo),'add','src/binary'],check=True)
        self.assertEqual(len(self.export()['skipped']),1)
    def test_case_variant_agent_directory_excluded(self):
        self.assertIsNotNone(snap.exclusion('.Agents/agents/untrusted.md'))
    def test_reader_accepts_more_than_twelve_files_and_old_byte_cap(self):
        paths=[]
        for i in range(20):
            name=f'src/extra{i}.py';(self.repo/name).write_text('# source\n'*1500);paths.append(name)
        m=self.export(mode='reader',paths=paths)
        self.assertEqual(m['file_count'],20);self.assertGreater(m['total_bytes'],196608)
    def test_nested_agent_named_directories_are_source(self):
        name='payload/.codex/es/budget.py';p=self.repo/name;p.parent.mkdir(parents=True);p.write_text('def reserve(): pass\n')
        subprocess.run(['git','-C',str(self.repo),'add',name],check=True)
        m=self.export();self.assertIn(name,[x['path'] for x in m['files']])
    def test_source_larger_than_old_one_megabyte_cap_is_exported(self):
        (self.repo/'src/example.py').write_text('#'+('x'*1048576))
        m=self.export();self.assertIn('src/example.py',[x['path'] for x in m['files']])
    def test_scope_is_literal_not_git_pathspec(self):
        m=self.export(scopes=['src/example.py']);self.assertEqual(m['file_count'],1)
    def test_invalid_scope_refused(self):
        with self.assertRaises(EvidenceError):self.export(scopes=['../secret'])
    def test_negative_finding_invalidated_by_uncited_file_change(self):
        m=self.export();(self.repo/'src/other.py').write_text('changed\n')
        with self.assertRaisesRegex(EvidenceError,'stale_source_corpus'):snap.verify_export(self.repo,self.base/'workspace',m)
    def test_snapshot_change_detected(self):
        m=self.export();p=self.base/'workspace/src/example.py';p.chmod(0o600);p.write_text('changed\n')
        with self.assertRaisesRegex(EvidenceError,'snapshot_modified'):snap.verify_export(self.repo,self.base/'workspace',m)
    def test_reader_scope_cannot_silently_skip_inputs(self):
        with self.assertRaisesRegex(EvidenceError,'reader input rejected'):self.export(mode='reader',paths=['src/missing.py'])


@unittest.skipUnless(os.name=='posix','POSIX subprocess adapter')
class AgyRunnerTests(Fixture):
    def test_response_contains_usage_and_export_scope(self):
        r=self.invoke();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        result=json.loads(r.stdout)
        self.assertEqual(result['usage']['total_tokens'],130)
        self.assertEqual(result['scope']['file_count'],2)
        self.assertFalse(result['scope']['repository_complete'])
        self.assertIsNone(result['error'])
        self.assertTrue(self.metrics()['task_usage_recorded'])
    def test_busy_state_refused_before_source_export(self):
        budget.reserve(self.state,self.repo,'repo_explorer',self.task.read_bytes())
        r=self.invoke();self.assertNotEqual(r.returncode,0)
        self.assertIn('already reserved',json.loads(r.stdout)['error'])
        self.assertEqual(json.loads(r.stdout)['status'],'admission_failed')
        self.assertFalse(self.metrics()['task_usage_recorded'])
        self.assertFalse((self.base/'run/workspace').exists())
        self.assertFalse((self.base/'run/events.jsonl').exists())
    def test_accounting_failure_retains_evidence_and_reports_error(self):
        args=SimpleNamespace(repo=self.repo,task_file=self.task,state_dir=self.state,
             out_dir=self.base/'run',agy=str(FAKE),timeout=None,mode='localize',
             deep=False,path=[],scope=[],include_untracked=False,config=None,model=None)
        with patch.object(budget,'finish',side_effect=sqlite3.OperationalError('database full')):
            result,code=locate.run(args)
        self.assertEqual(code,1)
        self.assertFalse(result['ok']);self.assertEqual(result['status'],'accounting_failed')
        self.assertEqual(result['error'],'database full')
        self.assertTrue(result['evidence']['primary'])
        self.assertTrue(Path(result['handoff_path']).exists())
        self.assertFalse(self.metrics()['task_usage_recorded'])
    def test_localize_validated_and_no_codex(self):
        r=self.invoke();self.assertEqual(r.returncode,0,r.stderr+r.stdout)
        m=self.metrics();self.assertEqual(m['backend'],'agy');self.assertEqual(m['requested_model'],'gemini-3.8-flash-medium')
        self.assertNotIn('codex',m['argv']);self.assertFalse((self.repo/'.codex/agents').exists())
        h=json.loads((self.base/'run/handoff.json').read_text());self.assertEqual(h['primary'][0]['sha256'],hashlib.sha256((self.repo/'src/example.py').read_bytes()).hexdigest())
    def test_reader_returns_verified_citations_without_source_in_argv(self):
        r=self.invoke(extra=['--mode','reader','--path','src/example.py']);self.assertEqual(r.returncode,0,r.stderr+r.stdout)
        evidence=json.loads(r.stdout)['evidence']
        self.assertEqual(evidence['primary'][0]['source'],'1: def f():\n2:     return 1')
        self.assertFalse(evidence['semantic_relevance_verified'])
        self.assertNotIn('return 1',json.dumps(self.metrics()['argv']))
        self.assertIn('return 1',(self.base/'run/request.jsonl').read_text())
    def test_initializes_and_reuses_task_ledger(self):
        shutil.rmtree(self.state)
        for out in ('first','second'):
            r=self.invoke(out=out);self.assertEqual(r.returncode,0,r.stderr+r.stdout)
        self.assertEqual(budget.status(self.state)['attempts'],2)
        self.assertEqual(budget.status(self.state)['total_worker_tokens'],260)
    def test_cumulative_usage_not_double_counted(self):
        self.invoke();m=self.metrics();self.assertEqual(m['usage']['total_tokens'],130)
        self.assertEqual(budget.status(self.state)['total_worker_tokens'],130)
    def test_usage_keeps_provider_cache_semantics(self):
        r=self.invoke('large_cache');self.assertEqual(r.returncode,0,r.stdout)
        self.assertEqual(self.metrics()['usage']['cache_read_tokens'],1000)
    def test_unknown_usage_does_not_block_second_invocation(self):
        r=self.invoke('no_usage');self.assertEqual(r.returncode,0,r.stdout)
        self.assertFalse(self.metrics()['usage_complete'])
        second=self.invoke(out='run2');self.assertEqual(second.returncode,0,second.stdout)
        self.assertIsNone(budget.status(self.state)['total_worker_tokens'])
    def test_boolean_usage_is_unknown_not_one(self):
        self.invoke('bad_usage');self.assertIsNone(self.metrics()['usage']['input_tokens'])
    def test_partial_is_accepted_not_retried(self):
        r=self.invoke('partial');self.assertEqual(r.returncode,0,r.stdout)
        self.assertEqual(json.loads(r.stdout)['handoff_status'],'partial');self.assertEqual(budget.status(self.state)['attempts'],1)
    def test_not_found_is_scoped(self):
        r=self.invoke('not_found');self.assertEqual(r.returncode,0,r.stdout)
        self.assertFalse(json.loads((self.base/'run/source-manifest.json').read_text())['scope_is_repository_complete'])
    def test_environment_blocked_can_be_valid_handoff(self):
        r=self.invoke('blocked');self.assertEqual(r.returncode,0,r.stdout);self.assertEqual(json.loads(r.stdout)['handoff_status'],'blocked')
    def test_timeout_unknown_and_nonzero(self):
        r=self.invoke('timeout',['--timeout','0.15']);self.assertEqual(r.returncode,124,r.stdout)
        self.assertFalse(self.metrics()['usage_complete']);self.assertEqual(budget.status(self.state)['attempts'],1)
    def test_missing_schema_output_not_recovered_from_text(self):
        r=self.invoke('missing_structured');self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.base/'run/handoff.json').exists())
    def test_native_timeout_with_success_status_returns_cause_and_usage(self):
        r=self.invoke('native_timeout');self.assertNotEqual(r.returncode,0)
        report=json.loads(r.stdout)
        self.assertIn('print timeout after 5m0s',report['error'])
        self.assertEqual(report['usage']['total_tokens'],130)
        self.assertIsNone(report['handoff_path'])
        self.assertEqual(budget.status(self.state)['attempts'],1)
    def test_model_mismatch_rejected(self):
        r=self.invoke('bad_model');self.assertNotEqual(r.returncode,0);self.assertIn('model',self.metrics()['protocol_error'])
    def test_agent_mismatch_rejected(self):
        r=self.invoke('bad_agent');self.assertNotEqual(r.returncode,0);self.assertIn('agent',self.metrics()['protocol_error'])
    def test_global_write_catalog_is_not_effective_exposure(self):
        r=self.invoke('write_tool');self.assertEqual(r.returncode,0,r.stderr+r.stdout)
        self.assertTrue(self.metrics()['init_identity_checked'])
        self.assertFalse(self.metrics()['init_is_effective_tool_allowlist'])
    def test_global_mcp_catalog_is_not_effective_exposure(self):
        r=self.invoke('mcp_tool');self.assertEqual(r.returncode,0,r.stderr+r.stdout)
    def test_actual_write_step_rejected(self):
        r=self.invoke('write_step');self.assertNotEqual(r.returncode,0)
        self.assertIn('disallowed tool step',self.metrics()['protocol_error'])
    def test_actual_mcp_step_rejected(self):
        r=self.invoke('mcp_step');self.assertNotEqual(r.returncode,0)
        self.assertIn('disallowed tool step',self.metrics()['protocol_error'])
    def test_nested_delegation_rejected(self):
        r=self.invoke('nested');self.assertNotEqual(r.returncode,0);self.assertIn('nested',self.metrics()['protocol_error'])
    def test_many_tool_calls_complete_and_retain_usage(self):
        r=self.invoke('many_tools');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.metrics()['observed_tool_calls'],50)
        self.assertEqual(self.metrics()['usage']['total_tokens'],130)
    def test_unexpected_shell_step_rejected(self):
        r=self.invoke('unexpected_tool');self.assertNotEqual(r.returncode,0)
    def test_global_permission_mode_does_not_block_readonly_agent(self):
        r=self.invoke('bypass');self.assertEqual(r.returncode,0,r.stdout)
    def test_no_init_no_validated_answer(self):
        r=self.invoke('no_init');self.assertNotEqual(r.returncode,0)
    def test_duplicate_result_not_double_counted(self):
        r=self.invoke('duplicate_result');self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.metrics()['usage']['total_tokens'],130)
    def test_provider_repair_turns_are_not_conversation_resume(self):
        r=self.invoke('two_turns');self.assertEqual(r.returncode,0,r.stdout)
        self.assertEqual(self.metrics()['provider_turns'],2)
        self.assertEqual(self.metrics()['usage']['total_tokens'],130)
        self.assertNotIn('--continue',self.metrics()['argv'])
        self.assertNotIn('--conversation',self.metrics()['argv'])
    def test_invalid_json_not_retried(self):
        r=self.invoke('invalid_json');self.assertNotEqual(r.returncode,0);self.assertEqual(budget.status(self.state)['attempts'],1)
    def test_auth_error_records_failure(self):
        r=self.invoke('auth');self.assertNotEqual(r.returncode,0);self.assertEqual(self.metrics()['status'],'agy_failed')
        self.assertEqual(json.loads(r.stdout)['error'],'authentication required')
    def test_failure_usage_still_counts(self):
        r=self.invoke('fail_usage');self.assertNotEqual(r.returncode,0)
        self.assertEqual(budget.status(self.state)['total_worker_tokens'],130)
    def test_nonzero_exit_with_success_result_reports_process_failure(self):
        r=self.invoke('nonzero_success');self.assertNotEqual(r.returncode,0)
        result=json.loads(r.stdout)
        self.assertEqual(result['status'],'agy_failed')
        self.assertIn('exited 1',result['error'])
        self.assertIsNone(result['evidence'])
        self.assertEqual(result['usage']['total_tokens'],130)
    def test_hallucinated_path_rejected(self):
        r=self.invoke('outside');self.assertNotEqual(r.returncode,0);self.assertIn('outside',self.metrics()['error'])
    def test_model_generated_hash_not_accepted(self):
        r=self.invoke('fake_hash');self.assertNotEqual(r.returncode,0);self.assertIn('sha256',self.metrics()['error'])
    def test_out_of_bounds_range_rejected(self):
        r=self.invoke('bad_range');self.assertNotEqual(r.returncode,0)
    def test_snapshot_modification_rejected_original_unchanged(self):
        original=(self.repo/'src/example.py').read_bytes();r=self.invoke('snapshot_changed');self.assertNotEqual(r.returncode,0)
        self.assertEqual((self.repo/'src/example.py').read_bytes(),original)
    def test_uncited_reader_input_modification_rejected(self):
        r=self.invoke('other_changed',['--mode','reader','--path','src/example.py','--path','src/other.py'])
        self.assertNotEqual(r.returncode,0);self.assertIn('snapshot_modified',self.metrics()['error'])
    def test_concurrent_original_change_rejected(self):
        r=self.invoke('original_changed');self.assertNotEqual(r.returncode,0);self.assertIn('stale_source_corpus',self.metrics()['error'])
        self.assertIsNone(json.loads(r.stdout)['evidence'])
    def test_deep_can_run_first_and_repeat(self):
        for i in range(3):
            r=self.invoke(extra=['--deep'],out=f'deep{i}')
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(budget.status(self.state)['attempts'],3)
    def test_deep_same_flash_family_high(self):
        self.assertEqual(self.invoke().returncode,0)
        r=self.invoke(extra=['--deep'],out='deep');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.metrics('deep')['requested_model'],'gemini-3.8-flash-high')
        self.assertEqual(budget.status(self.state)['attempts'],2)
    def test_more_than_two_calls_succeed(self):
        for i in range(4):
            r=self.invoke(out=f'run{i}');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(budget.status(self.state)['attempts'],4)
        self.assertEqual(budget.status(self.state)['total_worker_tokens'],520)
    def test_no_implicit_codex_fallback_when_agy_missing(self):
        r=self.invoke(extra=['--agy','/not-installed/agy']);self.assertNotEqual(r.returncode,0)
        self.assertEqual(budget.status(self.state)['attempts'],0)
        result=json.loads(r.stdout)
        self.assertEqual(result['status'],'invocation_failed')
        self.assertIn('no Codex fallback',result['error']);self.assertEqual(r.stderr,'')
    def test_explicit_model_and_large_task_are_passed_to_provider(self):
        self.task.write_text('Find implementation.\n'*1000)
        r=self.invoke(extra=['--model','gemini-custom-model']);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.metrics()['effective_model'],'gemini-custom-model')
    def test_argv_and_source_are_recorded_privately(self):
        self.invoke();self.assertEqual((self.base/'run/request.jsonl').stat().st_mode&0o777,0o600)
        self.assertEqual((self.base/'run').stat().st_mode&0o777,0o700)
        argv = self.metrics()['argv']
        workspace = Path(argv[argv.index('--add-dir') + 1])
        self.assertEqual(workspace, self.base/'run/workspace')
        self.assertTrue((workspace/'.agents/agents/es-explorer.md').is_file())
    def test_reader_cannot_cite_unprovided_file(self):
        r=self.invoke(extra=['--mode','reader','--path','src/other.py']);self.assertNotEqual(r.returncode,0)
    def test_check_no_model_run_or_export(self):
        r=subprocess.run([sys.executable,str(self.repo/'.codex/es/locate.py'),'--check','--agy',str(FAKE)],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertFalse(json.loads(r.stdout)['model_inference_executed'])
        self.assertEqual(budget.status(self.state)['attempts'],0)

if __name__=='__main__':unittest.main()
