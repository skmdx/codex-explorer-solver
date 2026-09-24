from __future__ import annotations
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'payload/.codex/es'))
import configure_hooks as cfg
import hook_artifacts as artifacts
import hook_runtime as hooks
from hook_store import Store, StoreError, canonical, digest


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'repo'; self.root.mkdir(); (self.root / '.git').mkdir()
        self.state = self.base / 'state'
    def tearDown(self): self.temp.cleanup()
    def event(self, command='pytest -q', response=None, call='call-1'):
        return dict(hook_event_name='PostToolUse', session_id='session-A', turn_id='turn-A',
                    cwd=str(self.root), tool_name='Bash', tool_use_id=call,
                    tool_input={'command': command},
                    tool_response=response if response is not None else {'stdout':'running tests\n'*2000,
                                                                         'stderr':'AssertionError: expected 1, got 2\n', 'exit_code': 1})
    def invoke(self, event=None, **kw):
        return hooks.dispatch(event or self.event(), self.root, self.state, **kw)
    def store(self, **kw): return Store(self.state, self.root, **kw)


class HookTests(Fixture):
    def test_default_audit_does_not_change_result(self):
        self.assertEqual(self.invoke(), {})
        report = self.store().report()
        self.assertEqual(report['actions'][0]['action'], 'would_spill')
        self.assertEqual(report['archived_objects'], 0)
    def test_spill_uses_continue_false_not_block(self):
        out = self.invoke(mode='enforce')
        self.assertIs(out['continue'], False)
        self.assertNotIn('decision', out)
        self.assertNotIn('hookSpecificOutput', out)
        self.assertNotIn('updatedMCPToolOutput', out)
        self.assertNotIn('suppressOutput', out)
    def test_full_response_value_is_recoverable(self):
        e = self.event(); out = self.invoke(e, mode='enforce')
        aid = json.loads(out['stopReason'])['artifact_id']
        self.assertEqual(self.store().load(aid), e['tool_response'])
    def test_failure_status_is_not_changed_to_success(self):
        out = json.loads(self.invoke(mode='enforce')['stopReason'])
        self.assertEqual(out['status']['response.exit_code'], 1)
        self.assertFalse(out['success_claimed'])
    def test_success_is_not_inferred_from_text(self):
        out = json.loads(self.invoke(self.event(response='OK all passed\n'*3000), mode='enforce')['stopReason'])
        self.assertIsNone(out['status']['exit_code'])
        self.assertFalse(out['success_claimed'])
    def test_metadata_status_is_preserved(self):
        e = self.event(response={'output':'xx\n'*8000, 'metadata':{'exit_code':7,'wall_time_seconds':0.3}})
        out = json.loads(self.invoke(e, mode='enforce')['stopReason'])
        self.assertEqual(out['status']['metadata.exit_code'],7)
    def test_small_outputs_no_context_added(self):
        self.assertEqual(self.invoke(self.event(response='passed'), mode='enforce'), {})
    def test_exact_source_reads_not_replaced(self):
        for cmd in ('cat src/foo.py', "sed -n '1,300p' src/foo.py", 'head -n 10 x', 'tail x'):
            self.assertEqual(self.invoke(self.event(command=cmd), mode='enforce'), {})
    def test_edits_are_not_replaced(self):
        self.assertEqual(self.invoke(self.event(command='git apply patch.diff'), mode='enforce'), {})
        e = self.event(); e['tool_name'] = 'apply_patch'
        self.assertEqual(self.invoke(e, mode='enforce'), {})
    def test_mcp_is_untouched(self):
        e = self.event(); e['tool_name'] = 'mcp__fs__read'
        self.assertEqual(self.invoke(e, mode='enforce'), {})
    def test_pipelines_remain_untouched(self):
        self.assertEqual(self.invoke(self.event(command='pytest -q | tee log'), mode='enforce'), {})
    def test_redirects_remain_untouched(self):
        self.assertEqual(self.invoke(self.event(command='pytest > log'), mode='enforce'), {})
    def test_unknown_commands_remain_untouched(self):
        self.assertEqual(self.invoke(self.event(command='python3 application.py'), mode='enforce'), {})
    def test_machine_formats_remain_untouched(self):
        for cmd in ('rg --json ERROR .', 'grep -Z needle file', 'pytest --json=report'):
            self.assertEqual(self.invoke(self.event(command=cmd), mode='enforce'), {})
    def test_search_spills(self):
        self.assertIn('stopReason', self.invoke(self.event(command='rg -n pattern src'), mode='enforce'))
    def test_supported_tests_classified(self):
        for cmd in ('pytest -q','python3 -m unittest discover','python -m pytest','cargo test',
                    'cargo check','cargo clippy','npm test','pnpm test','yarn test'):
            self.assertEqual(hooks.command_kind(self.event(command=cmd)), 'test', cmd)
    def test_schema_unknown_is_not_destroyed(self):
        e = self.event(response={'stdout':'x'*20000,'structuredContent':{'x':1}})
        self.assertEqual(self.invoke(e, mode='enforce'), {})
    def test_live_session_not_consumed(self):
        for response in ({'output':'x'*20000,'session_id':123}, 'Process running with session ID 123\n'+'x'*20000):
            self.assertEqual(self.invoke(self.event(response=response), mode='enforce'), {})
    def test_utf8_preview_contains_only_original_substrings(self):
        text = '日本語🙂検証\n' * 3000
        data = json.loads(self.invoke(self.event(response=text), mode='enforce')['stopReason'])
        preview = data['preview']['text']
        self.assertTrue(text.startswith(preview['head']))
        self.assertTrue(text.endswith(preview['tail']))
        self.assertNotIn('\ufffd', preview['head'] + preview['tail'])
    def test_escape_heavy_preview_is_capped(self):
        out = self.invoke(self.event(response='"\\\n'*10000), mode='enforce')
        self.assertLessEqual(len(canonical(out)), 6144)
    def test_explicit_recovery_command_present(self):
        data = json.loads(self.invoke(mode='enforce')['stopReason'])
        self.assertIn(str(self.state), data['recovery_command'])
        self.assertIn('hook_artifacts.py', data['recovery_command'])
    def test_storage_full_preserves_original(self):
        out = self.invoke(mode='enforce', store_max_bytes=100)
        self.assertNotIn('continue', out)
        self.assertIn('systemMessage', out)
    def test_storage_failure_never_suppresses_result(self):
        with patch('hook_runtime.Store.archive', side_effect=OSError('disk full')):
            out = self.invoke(mode='enforce')
        self.assertNotIn('continue', out)
    def test_ledger_failure_after_archive_never_suppresses_result(self):
        with patch('hook_runtime.Store.observe', side_effect=sqlite3.OperationalError('busy')):
            out = self.invoke(mode='enforce')
        self.assertNotIn('continue', out)
    def test_repeated_hook_call_does_not_double_count(self):
        self.invoke(mode='enforce'); self.invoke(mode='enforce')
        self.assertEqual(self.store().report()['actions'][0]['observations'], 1)
        self.assertEqual(self.store().report()['archived_objects'], 1)
    def test_no_token_savings_are_fabricated(self):
        self.invoke(mode='enforce'); r = self.store().report()
        self.assertIsNone(r['token_savings']); self.assertIsNone(r['billing_savings'])
    def test_missing_call_id_no_spill(self):
        e = self.event(); e.pop('tool_use_id')
        self.assertEqual(self.invoke(e, mode='enforce'), {})
    def test_wrong_session_no_spill(self):
        e = self.event(); e.pop('session_id')
        self.assertEqual(self.invoke(e, mode='enforce'), {})
    def test_stop_does_not_force_retry(self):
        e = self.event(); e['hook_event_name'] = 'Stop'
        self.assertEqual(self.invoke(e, mode='enforce'), {})
    def test_precompact_is_silent_and_does_not_block(self):
        e = self.event(); e['hook_event_name'] = 'PreCompact'
        self.assertEqual(self.invoke(e, mode='enforce'), {})
    def test_compact_restore_is_pointer_only(self):
        self.invoke(mode='enforce')
        e = self.event(); e.update(hook_event_name='SessionStart', source='compact')
        out = self.invoke(e, mode='enforce')['hookSpecificOutput']['additionalContext']
        self.assertLessEqual(len(out.encode()), 1024)
        self.assertNotIn('AssertionError',out)
        self.assertIn('historical',out)
    def test_compact_uses_checkpoint_not_later_output(self):
        old = json.loads(self.invoke(mode='enforce')['stopReason'])['artifact_id']
        e = self.event(); e['hook_event_name'] = 'PreCompact'
        self.invoke(e, mode='enforce')
        new = json.loads(self.invoke(self.event(response='new logs\n'*3000, call='later'), mode='enforce')['stopReason'])['artifact_id']
        e.update(hook_event_name='SessionStart', source='compact')
        text = self.invoke(e, mode='enforce')['hookSpecificOutput']['additionalContext']
        self.assertIn(old, text); self.assertNotIn(new, text)
    def test_no_restore_for_unrelated_session(self):
        self.invoke(mode='enforce')
        e = self.event(); e.update(hook_event_name='SessionStart', source='compact', session_id='other')
        self.assertEqual(self.invoke(e, mode='enforce'),{})
    def test_startup_does_not_inject_context(self):
        self.invoke(mode='enforce')
        e = self.event(); e.update(hook_event_name='SessionStart', source='startup')
        self.assertEqual(self.invoke(e, mode='enforce'),{})
    def test_audit_restore_silent(self):
        self.invoke(mode='enforce')
        e = self.event(); e.update(hook_event_name='SessionStart', source='compact')
        self.assertEqual(self.invoke(e, mode='audit'),{})
    def test_no_source_or_command_in_audit_ledger(self):
        self.invoke()
        db = self.state / 'ledger.sqlite3'
        self.assertNotIn(b'AssertionError',db.read_bytes())
        self.assertNotIn(b'pytest -q',db.read_bytes())
    def test_private_archive_permissions(self):
        self.invoke(mode='enforce')
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        for p in (self.state/'blobs').iterdir(): self.assertEqual(p.stat().st_mode&0o777,0o600)
    def test_cli_noop_is_zero_and_silent(self):
        cmd=[sys.executable,str(KIT/'payload/.codex/es/hook_runtime.py'),'--root',str(self.root),'--state-dir',str(self.state)]
        p=subprocess.run(cmd,input=json.dumps(self.event(response='OK')),text=True,capture_output=True)
        self.assertEqual(p.returncode,0);self.assertEqual(p.stdout,'')
    def test_cli_malformed_warns_does_not_deny(self):
        cmd=[sys.executable,str(KIT/'payload/.codex/es/hook_runtime.py'),'--root',str(self.root),'--state-dir',str(self.state)]
        p=subprocess.run(cmd,input='not json',text=True,capture_output=True)
        self.assertEqual(p.returncode,0)
        self.assertNotIn('continue',json.loads(p.stdout))
    def test_duplicate_keys_rejected(self):
        with self.assertRaises(StoreError): hooks.load_event(b'{"a":1,"a":2}')
    def test_nonfinite_numbers_rejected(self):
        with self.assertRaises(StoreError): hooks.load_event(b'{"a":NaN}')


class NativeAdmissionTests(Fixture):
    def spawn(self, call='spawn-1', turn='turn-A', **changes):
        e=self.event(call=call);e.update(hook_event_name='PreToolUse',tool_name='spawn_agent',turn_id=turn,
                                      tool_input={'agent_type':'repo_explorer','message':'find entrypoint'})
        e.update(changes);return e
    def run_spawn(self,e=None,mode='enforce'):
        return self.invoke(e or self.spawn(),mode=mode,native_spawn_limit=2)
    def test_first_calls_never_grant_permissions(self):
        self.assertEqual(self.run_spawn(),{})
        self.assertEqual(self.run_spawn(self.spawn('spawn-2')), {})
    def test_third_call_denied(self):
        self.run_spawn();self.run_spawn(self.spawn('spawn-2'))
        self.assertEqual(self.run_spawn(self.spawn('spawn-3'))['hookSpecificOutput']['permissionDecision'],'deny')
    def test_replayed_id_is_idempotent(self):
        self.run_spawn();self.run_spawn();self.assertEqual(self.run_spawn(self.spawn('spawn-2')), {})
    def test_mutated_same_call_is_denied(self):
        self.run_spawn();e=self.spawn(tool_input={'message':'new'})
        self.assertIn('hookSpecificOutput',self.run_spawn(e))
    def test_new_turn_has_separate_budget(self):
        self.run_spawn();self.run_spawn(self.spawn('spawn-2'))
        self.assertEqual(self.run_spawn(self.spawn('spawn-3',turn='turn-B')), {})
    def test_no_turn_id_fails_closed_when_enabled(self):
        e=self.spawn();e.pop('turn_id')
        self.assertIn('hookSpecificOutput',self.run_spawn(e))
    def test_budget_disabled_by_default(self):
        for i in range(5): self.assertEqual(self.invoke(self.spawn(str(i)),mode='enforce'),{})
    def test_audit_does_not_deny(self):
        for i in range(5): self.assertEqual(self.run_spawn(self.spawn(str(i)),mode='audit'),{})
    def test_parallel_admissions_cannot_overspend_limit(self):
        self.store() # initialize before the race
        def f(i):return self.run_spawn(self.spawn(str(i)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex: result=list(ex.map(f,range(6)))
        self.assertEqual(sum(r=={} for r in result),2)
    def test_missing_state_blocks_only_spawn(self):
        self.state.mkdir();(self.state/'ledger.sqlite3').write_text('not sqlite')
        self.assertIn('hookSpecificOutput',self.run_spawn())
        self.assertNotIn('continue',self.invoke(mode='enforce'))
    def test_all_roles_count_when_explicitly_enabled(self):
        e=self.spawn(tool_input={'task_name':'generic','message':'x'})
        self.run_spawn(e);self.run_spawn(self.spawn('2'))
        self.assertIn('hookSpecificOutput',self.run_spawn(self.spawn('3')))
    def test_namespaced_alias(self):
        self.assertEqual(self.run_spawn(self.spawn(tool_name='collaboration.spawn_agent')), {})


class ArtifactTests(Fixture):
    def save(self,response): return self.store().archive(response)
    def test_read_exact_lines(self):
        aid=self.save({'stdout':'line1\nline2\nline3\n','exit_code':1})
        self.assertEqual(artifacts.read_lines(self.store(),aid,'stdout',2,2)['source'],'line2\n')
    def test_oversized_single_line_refused(self):
        aid=self.save('x'*20000)
        with self.assertRaises(StoreError):artifacts.read_lines(self.store(),aid,'text',1,1)
    def test_slice_can_recover_giant_line(self):
        aid=self.save('x'*20000)
        self.assertEqual(len(artifacts.byte_slice(self.store(),aid,'text',100,128)['source']),128)
    def test_slice_utf8_boundary_fails_explicitly(self):
        aid=self.save('日本語'*100)
        with self.assertRaises(StoreError):artifacts.byte_slice(self.store(),aid,'text',1,10)
    def test_search_returns_lines_not_entire_matched_lines(self):
        aid=self.save('foo'+('x'*30000)+'\nbar\nfoo\n')
        r=artifacts.search(self.store(),aid,'text','foo',20)
        self.assertEqual(r['line_numbers'],[1,3]);self.assertFalse(r['source_text_returned'])
    def test_corrupt_blob_rejected(self):
        aid=self.save('original');(self.state/'blobs'/f'{aid}.json').write_text('"new"')
        with self.assertRaises(StoreError):self.store().load(aid)
    def test_archive_id_traversal_refused(self):
        with self.assertRaises(StoreError):self.store().load('../file')
    def test_symlink_archive_refused(self):
        aid=self.save('original');p=self.state/'blobs'/f'{aid}.json';p.unlink();p.symlink_to(self.base/'outside')
        with self.assertRaises(StoreError):self.store().load(aid)
    def test_cross_repo_store_refused(self):
        self.store();other=self.base/'other';other.mkdir()
        with self.assertRaises(StoreError):Store(self.state,other)
    def test_in_repo_store_refused(self):
        with self.assertRaises(StoreError):Store(self.root/'state',self.root)
    def test_symlink_state_refused(self):
        target=self.base/'target';target.mkdir();self.state.symlink_to(target)
        with self.assertRaises(StoreError):self.store()
    def test_duplicate_archive_deduplicates_storage_only(self):
        aid=self.save({'output':'abc'});self.assertEqual(aid,self.save({'output':'abc'}))
        self.assertEqual(self.store().report()['archived_objects'],1)


class ConfigTests(Fixture):
    def setUp(self):
        super().setUp()
        (self.root/'.codex/es').mkdir(parents=True)
        (self.root/'.codex/es/hook_runtime.py').write_text('# test fixture\n')
    def configure(self,**kw):return cfg.configure(self.root,self.state,**kw)
    def test_dry_run_does_not_write(self):
        self.configure()
        self.assertFalse((self.root/'.codex/hooks.json').exists());self.assertFalse(self.state.exists())
    def test_unrelated_hooks_preserved(self):
        original={'description':'mine','hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo custom'}]}]}}
        (self.root/'.codex/hooks.json').write_text(json.dumps(original))
        r=self.configure(apply=True,backup=self.base/'backup')
        self.assertEqual(r['hooks']['hooks']['Stop'],original['hooks']['Stop'])
        self.assertEqual(r['hooks']['description'],'mine')
    def test_no_permissionrequest_or_stop_hook_registered(self):
        r=self.configure()['hooks']['hooks']
        self.assertNotIn('PermissionRequest',r);self.assertNotIn('Stop',r)
    def test_native_budget_opt_in_only(self):
        self.assertNotIn('PreToolUse',self.configure()['hooks']['hooks'])
        self.assertIn('PreToolUse',self.configure(native_limit=2)['hooks']['hooks'])
    def test_hooks_synchronous(self):
        for groups in self.configure()['hooks']['hooks'].values():
            for g in groups:
                for h in g['hooks']:self.assertFalse(h.get('async',False))
    def test_reinstall_idempotent(self):
        self.configure(apply=True,backup=self.base/'b')
        self.assertFalse(self.configure()['changed'])
    def test_mode_switch_keeps_single_owned_handler(self):
        self.configure(apply=True,backup=self.base/'b')
        r=self.configure(mode='enforce',apply=True,backup=self.base/'b2')
        self.assertEqual(len(r['hooks']['hooks']['PostToolUse']),1)
        self.assertIn('--mode enforce',r['hooks']['hooks']['PostToolUse'][0]['hooks'][0]['command'])
    def test_remove_just_owned_groups(self):
        self.configure(apply=True,backup=self.base/'b')
        hp=self.root/'.codex/hooks.json';d=json.loads(hp.read_text())
        custom={'hooks':[{'type':'command','command':'echo mine'}]};d['hooks']['PostToolUse'].append(custom);hp.write_text(json.dumps(d))
        r=cfg.configure(self.root,remove=True,apply=True,backup=self.base/'b2')
        self.assertEqual(r['hooks']['hooks']['PostToolUse'],[custom])
        self.assertFalse((self.root/cfg.MANIFEST).exists())
    def test_edited_owned_group_refused(self):
        self.configure(apply=True,backup=self.base/'b')
        hp=self.root/'.codex/hooks.json';d=json.loads(hp.read_text());d['hooks']['PostToolUse'][0]['hooks'][0]['timeout']=10;hp.write_text(json.dumps(d))
        with self.assertRaises(StoreError):self.configure(mode='enforce')
    def test_config_agents_untouched(self):
        cp=self.root/'.codex/config.toml';cp.write_text('model="user-choice"\n')
        ap=self.root/'AGENTS.md';ap.write_text('local instructions')
        self.configure(apply=True,backup=self.base/'b')
        self.assertEqual(cp.read_text(),'model="user-choice"\n');self.assertEqual(ap.read_text(),'local instructions')
    def test_backup_contains_exact_original_bytes(self):
        raw=b'{  "hooks": {} }\n';(self.root/'.codex/hooks.json').write_bytes(raw)
        self.configure(apply=True,backup=self.base/'b')
        self.assertEqual((self.base/'b/hooks.json').read_bytes(),raw)
    def test_no_apply_without_backup(self):
        with self.assertRaises(StoreError):self.configure(apply=True)
        self.assertFalse((self.root/'.codex/hooks.json').exists())
    def test_existing_backup_refused(self):
        b=self.base/'b';b.mkdir()
        with self.assertRaises(StoreError):self.configure(apply=True,backup=b)
    def test_unknown_marker_refused(self):
        hp=self.root/'.codex/hooks.json'
        hp.write_text(json.dumps({'hooks':{'PostToolUse':[{'hooks':[{'command':'python hook_runtime.py'}]}]}}))
        with self.assertRaises(StoreError):self.configure()
    def test_duplicate_json_refused(self):
        (self.root/'.codex/hooks.json').write_text('{"hooks":{},"hooks":{}}')
        with self.assertRaises(StoreError):self.configure()
    def test_no_bypass_flag_in_commands(self):
        r=self.configure()
        self.assertNotIn('dangerously',json.dumps(r['hooks']))
    def test_v2_read_guard_is_not_deleted(self):
        group={'hooks':[{'command':'python read_guard.py --mode deny'}]}
        (self.root/'.codex/hooks.json').write_text(json.dumps({'hooks':{'PreToolUse':[group]}}))
        r=self.configure(native_limit=2)
        self.assertEqual(r['hooks']['hooks']['PreToolUse'][0],group)
        self.assertTrue(any('v2' in w for w in r['warnings']))

if __name__ == '__main__':unittest.main()
