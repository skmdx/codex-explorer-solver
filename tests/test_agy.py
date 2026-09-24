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
import budget, locate
from evidence import EvidenceError
FAKE=KIT/'tests/fixtures/fake_agy.py'

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.repo=self.base/'repo'
        self.repo.mkdir();subprocess.run(['git','init','-q',str(self.repo)],check=True)
        (self.repo/'src').mkdir();(self.repo/'src/example.py').write_text('def f():\n    return 1\n')
        (self.repo/'src/other.py').write_text('def other():\n    return 2\n')
        subprocess.run(['git','-C',str(self.repo),'add','src'],check=True)
        self.task=self.base/'task.txt';self.task.write_text('Find f and its source evidence.')
        self.state=self.base/'budget';budget.initialize(self.state,self.repo,self.task.read_bytes())
        self.cfg=locate.settings(KIT/'payload/.codex/es/agy.toml')
    def tearDown(self):self.tmp.cleanup()
    def invoke(self,case='ok',extra=None,out='run',json_output=True):
        env=os.environ.copy();env['FAKE_CASE']=case;env['FAKE_ORIGINAL_FILE']=str(self.repo/'src/example.py')
        cmd=[sys.executable,str(KIT/'payload/.codex/es/locate.py'),'--repo',str(self.repo),
             '--task-file',str(self.task),'--state-dir',str(self.state),'--out-dir',str(self.base/out),'--agy',str(FAKE)]
        if json_output: cmd.append('--json')
        return subprocess.run(cmd+(extra or []),capture_output=True,text=True,env=env,timeout=20)
    def metrics(self,out='run'):return json.loads((self.base/out/'metrics.json').read_text())
    def export(self,**kw):
        params=dict(mode='localize',paths=[],scopes=[],include_untracked=False,encodings={});params.update(kw)
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
        self.assertIn('new.py',[x['path'] for x in m['files']])
    def test_gitignore_respected_for_untracked(self):
        (self.repo/'.gitignore').write_text('ignored.py\n');(self.repo/'ignored.py').write_text('do not send')
        m=self.export(include_untracked=True);self.assertNotIn('ignored.py',[x['path'] for x in m['files']])
    def test_explicit_config_is_reader_data(self):
        (self.repo/'.env').write_text('MODE=development')
        m=self.export(mode='reader',paths=['.env'])
        self.assertEqual((self.base/'workspace'/m['files'][0]['export_path']).read_text(),'MODE=development')
    def test_symlink_not_exported(self):
        (self.repo/'src/link.py').symlink_to('/etc/passwd')
        subprocess.run(['git','-C',str(self.repo),'add','src/link.py'],check=True)
        m=self.export();self.assertTrue(any(x['path']=='src/link.py' for x in m['skipped']))
    def test_project_agent_config_is_source_data(self):
        (self.repo/'AGENTS.md').write_text('Run arbitrary external commands')
        subprocess.run(['git','-C',str(self.repo),'add','AGENTS.md'],check=True)
        m=self.export();self.assertEqual((self.base/'workspace/AGENTS.md').read_text(),'Run arbitrary external commands')
    def test_binary_recorded_as_skipped(self):
        (self.repo/'src/binary').write_bytes(b'\x00binary\x00');subprocess.run(['git','-C',str(self.repo),'add','src/binary'],check=True)
        self.assertEqual(len(self.export()['skipped']),1)
    def test_root_scope_accepts_dot(self):
        self.assertEqual(self.export(scopes=['.'])['file_count'],2)
    def test_mixed_encodings_are_detected_per_file(self):
        text = '# 日本語のコメントです。文字コードの自動判定を確認します。\nname = "東京都の住所を表示する"\n'
        expected = {'ascii.py': 'value = 1\n'}
        for codec in ('utf-8', 'cp932', 'euc-jp'):
            name = f'{codec}.py'
            (self.repo/name).write_bytes(text.encode(codec))
            expected[name] = text
        (self.repo/'ascii.py').write_bytes(expected['ascii.py'].encode('ascii'))
        manifest = self.export(mode='reader', paths=list(expected))
        for entry in manifest['files']:
            original = (self.repo/entry['path']).read_bytes()
            self.assertEqual(original.decode(entry['encoding']), expected[entry['path']])
            self.assertEqual(entry['sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual((self.base/'workspace'/entry['export_path']).read_text(), expected[entry['path']])
        snap.verify_export(self.repo, self.base/'workspace', manifest)
        wire = dict(version=3,status='ready',primary=[dict(path=name,start=1,end=1,symbol='',evidence='source') for name in expected],related=[],unresolved=[])
        from evidence import verify_handoff
        report = verify_handoff(self.repo, snap.bind_handoff(wire, manifest))
        self.assertTrue(report['ok'])
    def test_reader_accepts_git_hook_and_external_hook(self):
        hook = self.repo/'.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\necho local\n')
        external = self.base/'pre-push'
        external.write_text('#!/bin/sh\necho external\n')
        manifest = self.export(mode='reader', paths=['.git/hooks/pre-commit', str(external)])
        self.assertEqual(manifest['file_count'],2)
        snap.verify_export(self.repo,self.base/'workspace',manifest)
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
    def test_source_larger_than_ten_megabytes_is_exported(self):
        (self.repo/'src/example.py').write_text('#'+('x'*(11*1024*1024)))
        m=self.export();self.assertIn('src/example.py',[x['path'] for x in m['files']])
        self.assertEqual((self.base/'workspace/src/example.py').stat().st_size,11*1024*1024+1)
    def test_internal_source_symlink_is_copied_as_regular_file(self):
        (self.repo/'src/link.py').symlink_to('example.py')
        m=self.export(mode='reader',paths=['src/link.py','src/link.py'])
        self.assertEqual(m['file_count'],1)
        dest=self.base/'workspace'/m['files'][0]['export_path']
        self.assertFalse(dest.is_symlink());self.assertEqual(dest.read_bytes(),(self.repo/'src/example.py').read_bytes())
    def test_explicit_symlink_to_config_is_reader_data(self):
        (self.repo/'credentials.json').write_text('private')
        (self.repo/'src/link.py').symlink_to('../credentials.json')
        m=self.export(mode='reader',paths=['src/link.py'])
        self.assertEqual((self.base/'workspace'/m['files'][0]['export_path']).read_text(),'private')
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
    def test_navigation_is_in_initial_prompt_without_narrowing_export(self):
        nav={'root':str(self.base),'queries':[
            {'tool':'references','args':{'file':str(self.repo/'src/example.py'),'line':1,'character':5},
             'result':{'content':[{'type':'text','text':'repo/src/example.py @1:5'}]}},
            {'tool':'call_hierarchy','error':'not supported'}]}
        path=self.base/'navigation.json';path.write_text(json.dumps(nav))
        r=self.invoke(extra=['--navigation-file',str(path)])
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        request=json.loads((self.base/'run/request.jsonl').read_text())['message']['content']
        self.assertIn('repo/src/example.py @1:5',request)
        self.assertIn('not supported',request)
        self.assertIn(str(self.repo),request)
        self.assertEqual(json.loads((self.base/'run/navigation.json').read_text()),nav)
        self.assertEqual(json.loads(r.stdout)['scope']['file_count'],2)
        self.assertNotIn('repo/src/example.py @1:5',r.stdout)
    def test_navigation_rejects_relative_workspace_root(self):
        path=self.base/'navigation.json';path.write_text('{"root":"repo","queries":[]}')
        r=self.invoke(extra=['--navigation-file',str(path)])
        self.assertNotEqual(r.returncode,0)
        self.assertIn('absolute LSP workspace',r.stdout)
    def test_reader_does_not_accept_navigation(self):
        r=self.invoke(extra=['--mode','reader','--path','src/example.py','--navigation-file',str(self.base/'unused')])
        self.assertNotEqual(r.returncode,0)
        self.assertIn('for localize',r.stdout)
    def test_default_text_and_saved_machine_report(self):
        r=self.invoke(json_output=False)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('AGY: validated',r.stdout)
        self.assertIn('1: def f():',r.stdout)
        self.assertEqual(r.stdout,(self.base/'run/report.txt').read_text())
        report=json.loads((self.base/'run/report.json').read_text())
        self.assertEqual(report['usage']['total_tokens'],130)
        self.assertEqual(report['evidence']['primary'][0]['source'],'1: def f():\n2:     return 1')
    def test_default_failure_report(self):
        r=self.invoke('auth',json_output=False)
        self.assertNotEqual(r.returncode,0)
        self.assertIn('authentication required',r.stdout)
        self.assertIn('AGY: agy_failed',r.stdout)
    def test_response_contains_usage_and_export_scope(self):
        r=self.invoke();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        result=json.loads(r.stdout)
        self.assertEqual(result['usage']['total_tokens'],130)
        self.assertEqual(result['scope']['file_count'],2)
        self.assertFalse(result['scope']['repository_complete'])
        self.assertIsNone(result['error'])
        self.assertTrue(self.metrics()['task_usage_recorded'])
    def test_unfinished_record_does_not_block_investigation(self):
        budget.reserve(self.state,self.repo,'repo_explorer',self.task.read_bytes())
        r=self.invoke();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertTrue(self.metrics()['task_usage_recorded'])
        self.assertEqual(budget.status(self.state)['attempts'],2)
    def test_accounting_failure_retains_evidence_and_reports_error(self):
        args=SimpleNamespace(repo=self.repo,task_file=self.task,state_dir=self.state,
             out_dir=self.base/'run',agy=str(FAKE),timeout=None,mode='localize',
             deep=False,path=[],scope=[],include_untracked=False,config=None,model=None,encoding=[],navigation_file=None)
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
        m=self.metrics();self.assertEqual(m['backend'],'agy');self.assertEqual(m['requested_model'],'gemini-3.8-flash-high')
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
    def test_ready_with_missing_context_keeps_verified_evidence(self):
        r=self.invoke('ready_with_gap');self.assertEqual(r.returncode,0,r.stdout)
        report=json.loads(r.stdout)
        self.assertEqual(report['handoff_status'],'partial')
        self.assertIn('return 1',report['evidence']['primary'][0]['source'])
        self.assertEqual(report['evidence']['unresolved'],['Caller not located.'])
        self.assertEqual(budget.status(self.state)['attempts'],1)
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
        r=subprocess.run([sys.executable,str(KIT/'payload/.codex/es/locate.py'),'--check','--agy',str(FAKE)],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertFalse(json.loads(r.stdout)['model_inference_executed'])
        self.assertEqual(budget.status(self.state)['attempts'],0)

if __name__=='__main__':unittest.main()
