from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

KIT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(KIT/'payload/.codex/es'));sys.path.insert(0,str(KIT))
import budget
import gateway
import read_guard
import upgrade
from capture import capture
from evidence import EvidenceError,source_path


class SourceGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'a.py').write_text('foo\nbar\nfoo\nfoo\n')
        (self.root/'b.py').write_text('other\n')
    def tearDown(self):self.temp.cleanup()
    def test_scoped_literal_no_source_dump(self):
        r=gateway.search(self.root,['a.py'],'foo')
        self.assertEqual(r['total_matches_in_scope'],3)
        self.assertNotIn('source',r['locations'][0]);self.assertFalse(r['repository_wide_absence_proven'])
    def test_pagination_complete_not_global(self):
        r=gateway.search(self.root,['a.py'],'foo',max_results=2)
        self.assertEqual(r['next_offset'],2);self.assertFalse(r['complete_page'])
        t=gateway.search(self.root,['a.py'],'foo',offset=2,expected_snapshot=r['snapshot_sha256'])
        self.assertTrue(t['complete_page']);self.assertEqual(len(t['locations']),1)
    def test_pagination_stale(self):
        r=gateway.search(self.root,['a.py'],'foo',max_results=1)
        (self.root/'a.py').write_text('changed\nfoo\n')
        with self.assertRaisesRegex(EvidenceError,'stale_search'):
            gateway.search(self.root,['a.py'],'foo',offset=1,expected_snapshot=r['snapshot_sha256'])
    def test_zero_results_do_not_prove_global_absence(self):
        r=gateway.search(self.root,['b.py'],'foo')
        self.assertEqual(r['total_matches_in_scope'],0);self.assertFalse(r['repository_wide_absence_proven'])
    def test_literal_not_regex(self):
        r=gateway.search(self.root,['a.py'],'.*')
        self.assertEqual(r['total_matches_in_scope'],0)
    def test_duplicate_paths_rejected(self):
        with self.assertRaises(EvidenceError):gateway.corpus(self.root,['a.py','a.py'])
    def test_invalid_offset(self):
        with self.assertRaises(EvidenceError):gateway.search(self.root,['a.py'],'foo',offset=50)
    def test_single_giant_line_is_not_a_small_read(self):
        (self.root/'big.py').write_text('x'*13000)
        r=gateway.inspect(self.root,['big.py'],'lookup')
        self.assertIn('reader',r['route']);self.assertEqual(r['files'][0]['line_count'],1)
    def test_edit_never_automatically_delegates(self):
        (self.root/'big.py').write_text('x'*13000)
        self.assertEqual(gateway.inspect(self.root,['big.py'],'edit')['route'],'solver_exact_source')
    def test_reader_snapshot_original_hash_and_lines(self):
        text,manifest=gateway.reader_snapshot(self.root,['a.py'])
        self.assertIn('1: foo',text)
        self.assertEqual(manifest[0]['sha256'],hashlib.sha256((self.root/'a.py').read_bytes()).hexdigest())
    def test_reader_corpus_size_refuses_not_truncates(self):
        (self.root/'big.py').write_text('x'*65537)
        with self.assertRaises(EvidenceError):gateway.reader_snapshot(self.root,['big.py'])
    def test_symlink_alias_to_credentials_refused(self):
        (self.root/'.env').write_text('secret')
        (self.root/'alias.py').symlink_to(self.root/'.env')
        with self.assertRaises(EvidenceError):source_path(self.root,'alias.py')


class ReadGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'big.py').write_text(('x'*100+'\n')*200)
        (self.root/'small.py').write_text('short\n')
    def tearDown(self):self.temp.cleanup()
    def event(self,cmd):return dict(hook_event_name='PreToolUse',cwd=str(self.root),tool_name='Bash',tool_input={'command':cmd})
    def test_audit_never_blocks(self):
        out,rec=read_guard.decision(self.event('cat big.py'),self.root)
        self.assertEqual(out,{});self.assertTrue(rec['would_deny'])
    def test_deny_uses_documented_shape(self):
        out,_=read_guard.decision(self.event('cat big.py'),self.root,mode='deny')
        self.assertEqual(out['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertNotIn('updatedInput',out['hookSpecificOutput'])
    def test_small_read_no_explicit_permission_grant(self):
        out,rec=read_guard.decision(self.event('cat small.py'),self.root,mode='deny')
        self.assertEqual(out,{});self.assertFalse(rec['would_deny'])
    def test_cat_multiple_aggregates_bytes(self):
        out,_=read_guard.decision(self.event('cat small.py big.py'),self.root,mode='deny')
        self.assertTrue(out)
    def test_targeted_sed_allowed(self):
        out,rec=read_guard.decision(self.event("sed -n '1,5p' big.py"),self.root,mode='deny')
        self.assertEqual(out,{});self.assertEqual(rec['classification'],'sed_range')
    def test_large_sed_denied(self):
        out,_=read_guard.decision(self.event("sed -n '1,200p' big.py"),self.root,mode='deny')
        self.assertTrue(out)
    def test_small_head_allowed(self):
        self.assertEqual(read_guard.decision(self.event('head -n 2 big.py'),self.root,mode='deny')[0],{})
    def test_small_tail_allowed(self):
        self.assertEqual(read_guard.decision(self.event('tail -c 100 big.py'),self.root,mode='deny')[0],{})
    def test_pipeline_is_unknown_not_proven_safe(self):
        out,rec=read_guard.decision(self.event('cat big.py | grep x'),self.root,mode='deny')
        self.assertEqual(out,{});self.assertEqual(rec['classification'],'compound_or_pipeline')
        self.assertIsNone(rec['inspected_bytes'])
    def test_shell_expansion_is_unknown(self):
        out,rec=read_guard.decision(self.event('cat "$FILE"'),self.root,mode='deny')
        self.assertEqual(out,{});self.assertIsNone(rec['inspected_bytes'])
    def test_unknown_python_not_security_boundary(self):
        self.assertEqual(read_guard.decision(self.event('python3 read_anything.py'),self.root,mode='deny')[0],{})
    def test_explicit_read_range_is_measured(self):
        e=self.event('');e.update(tool_name='Read',tool_input={'file_path':'big.py','offset':10,'limit':2})
        self.assertEqual(read_guard.decision(e,self.root,mode='deny')[0],{})
    def test_single_long_line_denied_even_limit_one(self):
        (self.root/'one.py').write_text('x'*13000)
        e=self.event('');e.update(tool_name='Read',tool_input={'file_path':'one.py','limit':1})
        self.assertTrue(read_guard.decision(e,self.root,mode='deny')[0])
    def test_guard_cli_roundtrip(self):
        r=subprocess.run([sys.executable,str(KIT/'payload/.codex/es/read_guard.py'),'--root',str(self.root),'--mode','deny'],
                         input=json.dumps(self.event('cat big.py')),text=True,capture_output=True)
        self.assertEqual(r.returncode,0);self.assertEqual(json.loads(r.stdout)['hookSpecificOutput']['permissionDecision'],'deny')
    def test_bad_event_cli_fails_open(self):
        r=subprocess.run([sys.executable,str(KIT/'payload/.codex/es/read_guard.py'),'--root',str(self.root)],input='not json',text=True,capture_output=True)
        self.assertEqual(json.loads(r.stdout),{})
    def test_audit_has_no_code_or_command(self):
        _,rec=read_guard.decision(self.event('cat big.py'),self.root)
        self.assertNotIn('big.py',json.dumps(rec))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=self.base/'repo';self.root.mkdir();self.state=self.base/'state'
        budget.initialize(self.state,self.root,b'fix foo')
    def tearDown(self):self.temp.cleanup()
    def reserve(self,role='repo_explorer'):return budget.reserve(self.state,self.root,role,b'question')
    def finish(self,job,tokens=100,complete=True):budget.finish(self.state,job,dict(status='done',usage_complete=complete,usage={'total_tokens':tokens}))
    def test_second_concurrent_worker_rejected(self):
        self.reserve()
        with self.assertRaisesRegex(EvidenceError,'already'):self.reserve()
    def test_failed_attempt_still_counts(self):
        j=self.reserve();budget.finish(self.state,j,dict(status='invalid_json',usage_complete=True,usage={'total_tokens':100}))
        self.finish(self.reserve())
        self.finish(self.reserve())
        self.assertEqual(budget.status(self.state)['attempts'],3)
        self.assertEqual(budget.status(self.state)['total_worker_tokens'],300)
    def test_usage_unknown_is_recorded_without_blocking(self):
        j=self.reserve();self.finish(j,None,False)
        self.finish(self.reserve())
        self.assertIsNone(budget.status(self.state)['total_worker_tokens'])
        self.assertEqual(budget.status(self.state)['observed_tokens_lower_bound'],100)
    def test_deep_can_run_first_and_repeat(self):
        for _ in range(3):self.finish(self.reserve('repo_deep_explorer'))
        self.assertEqual(budget.status(self.state)['attempts'],3)
    def test_deep_after_low_allowed(self):
        self.finish(self.reserve());self.finish(self.reserve('repo_deep_explorer'))
        self.assertEqual(budget.status(self.state)['total_worker_tokens'],200)
    def test_finish_cannot_refund_or_repeat(self):
        j=self.reserve();self.finish(j)
        with self.assertRaises(EvidenceError):self.finish(j)
    def test_cross_repo_state_refused(self):
        other=self.base/'other';other.mkdir()
        with self.assertRaises(EvidenceError):budget.reserve(self.state,other,'repo_explorer',b'q')
    def test_reservation_transaction_race(self):
        def f(_):
            try:return self.reserve()
            except EvidenceError:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:jobs=list(ex.map(f,range(4)))
        self.assertEqual(sum(x is not None for x in jobs),1)
    def test_existing_limits_no_longer_restrict_admission(self):
        with budget.connect(self.state) as c:
            policy=json.loads(c.execute('SELECT data FROM policy').fetchone()[0])
            policy.update(max_calls=2,soft_token_limit=10,allow_unknown=False)
            c.execute('UPDATE policy SET data=?',(json.dumps(policy),))
        for _ in range(25):self.finish(self.reserve('repo_deep_explorer'))
        self.assertEqual(budget.status(self.state)['total_worker_tokens'],2500)
    def test_budget_not_created_in_repo(self):
        with self.assertRaises(EvidenceError):budget.initialize(self.root/'state',self.root,b'task')


@unittest.skipUnless(os.name=='posix','POSIX process control')
class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=self.base/'repo';self.root.mkdir()
    def tearDown(self):self.temp.cleanup()
    def test_failure_code_and_raw_logs_preserved(self):
        r,code=capture(self.root,self.base/'run',[sys.executable,'-c',"import sys;print('x'*20000);print('FAIL here',file=sys.stderr);sys.exit(7)"])
        self.assertEqual(code,7);self.assertEqual(r['returncode'],7)
        self.assertTrue(r['stdout']['preview_is_partial']);self.assertIn('FAIL here',r['stderr']['tail'])
        self.assertGreater((self.base/'run/stdout.log').stat().st_size,20000)
        self.assertFalse(r['success_claimed'])
    def test_no_shell_interpretation(self):
        r,code=capture(self.root,self.base/'run',[sys.executable,'-c','import sys;print(sys.argv[1])','$(touch hacked)'])
        self.assertEqual(code,0);self.assertFalse((self.root/'hacked').exists())
    def test_timeout_is_nonzero(self):
        r,code=capture(self.root,self.base/'run',[sys.executable,'-c','import time;time.sleep(10)'],timeout=0.1)
        self.assertEqual(code,124);self.assertEqual(r['termination_reason'],'local_deadline')
    def test_binary_preview_not_fabricated_unicode(self):
        r,_=capture(self.root,self.base/'run',[sys.executable,'-c',"import sys;sys.stdout.buffer.write(b'\\xff\\xfe')"])
        self.assertIsNone(r['stdout']['tail'])
    def test_serialized_preview_is_bounded(self):
        r,_=capture(self.root,self.base/'run',[sys.executable,'-c',"import sys;sys.stdout.buffer.write(b'\\x00'*10000);sys.stderr.buffer.write(b'\\x01'*10000)"])
        self.assertLessEqual(len(json.dumps(r).encode()),6144)
    def test_existing_output_not_overwritten(self):
        (self.base/'run').mkdir()
        with self.assertRaises(FileExistsError):capture(self.root,self.base/'run',['echo','x'])
    def test_missing_executable_reported(self):
        r,code=capture(self.root,self.base/'run',['/nonexistent/command'])
        self.assertEqual(code,127);self.assertEqual(r['termination_reason'],'launch_failed')


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.kit=self.base/'kit';self.repo=self.base/'repo'
        (self.kit/'payload/.codex/es').mkdir(parents=True);(self.repo/'.git').mkdir(parents=True);(self.repo/'.codex/es').mkdir(parents=True)
        (self.kit/'payload/.codex/es/a.py').write_text('new')
        (self.kit/'payload/.codex/es/b.py').write_text('added')
        (self.repo/'.codex/es/a.py').write_text('old')
        (self.repo/'.codex/config.toml').write_text('preserve')
        (self.kit/'legacy-payload-sha256.json').write_text(json.dumps({'.codex/es/a.py':hashlib.sha256(b'old').hexdigest()}))
        self.oldroot=upgrade.ROOT;upgrade.ROOT=self.kit
    def tearDown(self):upgrade.ROOT=self.oldroot;self.temp.cleanup()
    def test_dry_run_changes_nothing(self):
        self.assertEqual(len(upgrade.upgrade(self.repo)),2);self.assertEqual((self.repo/'.codex/es/a.py').read_text(),'old')
    def test_backup_and_replace(self):
        backup=self.base/'backup';upgrade.upgrade(self.repo,True,backup)
        self.assertEqual((backup/'.codex/es/a.py').read_text(),'old')
        self.assertEqual((self.repo/'.codex/es/a.py').read_text(),'new')
        self.assertEqual((self.repo/'.codex/config.toml').read_text(),'preserve')
    def test_local_edits_refuse_all_changes(self):
        (self.repo/'.codex/es/a.py').write_text('user customization')
        with self.assertRaisesRegex(ValueError,'locally modified'):upgrade.upgrade(self.repo,True,self.base/'backup')
        self.assertFalse((self.repo/'.codex/es/b.py').exists());self.assertFalse((self.base/'backup').exists())
    def test_rerun_updated_files_skips(self):
        upgrade.upgrade(self.repo,True,self.base/'backup');self.assertEqual(upgrade.upgrade(self.repo),[])


if __name__=='__main__': unittest.main()
