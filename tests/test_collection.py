from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT/'payload/.codex/es'))
from server import collect, read_evidence, active_paths, remember_navigation
from scratch_space import ScratchSpace
from evidence import EvidenceError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        active_paths.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.space = ScratchSpace(self.root, session='collection-tests')
        item = self.space.create()
        self.ref, self.base = item['scratch_ref'], Path(item['path'])
        self.repo = self.root/'repo'
        self.repo.mkdir()
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        (self.repo/'src').mkdir()
        (self.repo/'src/example.py').write_text('def f():\n    return 1\n')
        subprocess.run(['git','-C',str(self.repo),'add','src'],check=True)
        bin_dir = self.base/'bin';bin_dir.mkdir()
        (bin_dir/'agy').symlink_to(KIT/'tests/fixtures/fake_agy.py')
        self.env = patch.dict(os.environ, SCRATCH_ROOT=str(self.root), PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                              FAKE_CASE='ok',FAKE_ORIGINAL_FILE=str(self.repo/'src/example.py'))
        self.env.start()

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    async def run_collection(self, **kw):
        kw.setdefault('navigation',None)
        kw.setdefault('known_findings','')
        scope=kw.pop('scope',['src'])
        evidence = kw.pop('evidence_needed', [{'fact':'definition','scope':scope}])
        return await collect(str(self.repo),'Where is f defined?', evidence,self.ref,**kw)

    async def test_navigation_handle_and_unlimited_collection(self):
        nav = {'root':str(self.repo), 'queries':[]}
        saved = remember_navigation(nav, self.ref)
        self.assertRegex(saved['navigation_id'], r'^[a-z]+-[a-z]+-[a-z]+$')
        self.assertTrue(Path(saved['path']).is_file())
        with patch.dict(os.environ, FAKE_CASE='many_tools'):
            result = await self.run_collection(navigation=saved['navigation_id'])
        self.assertEqual(result['status'], 'validated', result)
        run = Path(result['run_dir'])
        self.assertEqual(json.loads((run/'navigation.json').read_text()), nav)
        self.assertEqual(json.loads((run/'metrics.json').read_text())['observed_tool_calls'], 50)
        self.assertFalse((run.parent/'collection.json').exists())
        self.space.delete()
        self.assertFalse(Path(saved['path']).exists())

    async def test_reader_accepts_hook_external_file_and_encoding_correction(self):
        hook=self.repo/'.git/hooks/pre-commit'
        hook.write_bytes('# 日本語\nexit 0\n'.encode('cp932'))
        external=self.base/'external.txt';external.write_text('external source\n')
        result=await self.run_collection(scope=[],paths=['src/example.py','.git/hooks/pre-commit',str(external)],
                                         encodings={'.git/hooks/pre-commit':'cp932'})
        self.assertEqual(result['status'],'validated',result)
        run=Path(result['run_dir'])
        manifest=json.loads((run/'source-manifest.json').read_text())
        self.assertEqual(manifest['file_count'],3)
        entry=next(e for e in manifest['files'] if e['path']=='.git/hooks/pre-commit')
        self.assertIn('日本語',(run/'sources'/entry['export_path']).read_text())
        self.assertIn('external source',(run/'request.jsonl').read_text())
        self.assertIn('return 1',read_evidence(str(run),[1])['text'])

    async def test_params_file_over_stdio_and_retry(self):
        request = self.base/'request.json'
        arguments = dict(repo=str(self.repo), scratch_ref=self.ref,
                         question='Where is f?', evidence_needed=[{'fact':'definition','scope':[]}],
                         navigation=None, known_findings='既知の情報',
                         paths=['src/example.py'], model='gemini-custom-model',
                         encodings={'src/example.py':'utf-8'})
        request.write_text(json.dumps(arguments, ensure_ascii=False), encoding='utf-8')
        params = StdioServerParameters(command=sys.executable,
                    args=[str(KIT/'payload/.codex/es/server.py')], env=dict(os.environ))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                schema = next(t.inputSchema for t in (await session.list_tools()).tools if t.name=='collect')
                self.assertNotIn('max_steps', schema['properties'])
                self.assertNotIn('resume', {t.name for t in (await session.list_tools()).tools})
                self.assertIn('params_file', schema['properties'])
                self.assertFalse(schema.get('required'))
                failed = await session.call_tool('collect', {'params_file':str(request), 'question':'mixed'})
                self.assertTrue(failed.isError)
                for _ in range(2):
                    request.write_text(json.dumps(arguments), encoding='utf-8')
                    result = await session.call_tool('collect', {'params_file':str(request)})
                    self.assertFalse(result.isError, result)
                    self.assertFalse(request.exists())
                    index = json.loads(result.content[0].text)
                    self.assertEqual(index['status'], 'validated', index)
                    run = Path(index['run_dir'])
                    self.assertIn('既知の情報', (run/'request.jsonl').read_text())
                    self.assertEqual(index['effective_model'], 'gemini-custom-model')
                    source = await session.call_tool('read_evidence', {'run_dir':str(run), 'ids':[1]})
                    self.assertFalse(source.isError)
                    self.assertIn('return 1', json.loads(source.content[0].text)['text'])
        self.assertFalse(request.exists())

    async def test_params_file_validation_before_collection(self):
        request = self.base/'request.json'
        valid = dict(repo=str(self.repo), scratch_ref=self.ref, question='Where?',
                     evidence_needed=[{'fact':'definition','scope':['src']}],
                     navigation=None, known_findings='')
        for contents in ('{', '[]', '{}', json.dumps(dict(valid, params_file=str(request))),
                         json.dumps(dict(valid, evidence_needed=[{'fact':'missing scope'}])),
                         json.dumps(dict(valid, include_untracked='invalid'))):
            request.write_text(contents)
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                await collect(params_file=str(request))
        with self.assertRaisesRegex(ValueError, 'absolute'):
            await collect(params_file='relative.json')
        with self.assertRaises(FileNotFoundError):
            await collect(params_file=str(self.base/'missing.json'))
        self.assertEqual(list(self.base.glob('explore-solve-*')), [])

    async def test_inline_mcp_errors_save_original_arguments_for_retry(self):
        arguments: dict = dict(repo=str(self.repo), scratch_ref=self.ref,
                         question='定義はどこ?', evidence_needed=[{'fact':'definition','scope':42}],
                         navigation=None, known_findings='既知の情報')
        params = StdioServerParameters(command=sys.executable,
                    args=[str(KIT/'payload/.codex/es/server.py')], env=dict(os.environ))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for scope in (42, ['missing.py']):
                    arguments['evidence_needed'][0]['scope'] = scope
                    result = await session.call_tool('collect', arguments)
                    self.assertTrue(result.isError)
                    message = result.content[0].text
                    saved = json.loads(message.splitlines()[-1])
                    file = Path(saved['params_file'])
                    self.assertEqual(json.loads(file.read_text()), arguments)
                    self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                    retry = await session.call_tool('collect', {'params_file':str(file)})
                    self.assertTrue(retry.isError)
                    self.assertTrue(file.exists())
                    self.assertNotIn('collect-failed-', retry.content[0].text)
                    failed_update = await session.call_tool('collect', {'params_file':str(file),
                        'updates': {'evidence_needed':[{'fact':'definition','scope':['still-missing.py']}]}})
                    self.assertTrue(failed_update.isError)
                    self.assertEqual(json.loads(file.read_text())['evidence_needed'][0]['scope'], ['still-missing.py'])
                    retry = await session.call_tool('collect', {'params_file':str(file),
                        'updates': {'evidence_needed':[{'fact':'definition','scope':['src']}]}})
                    self.assertFalse(retry.isError, retry)
                    self.assertEqual(json.loads(retry.content[0].text)['status'], 'validated')
                    self.assertFalse(file.exists())
                self.assertEqual(len(list(self.base.glob('collect-failed-*.json'))), 0)
                arguments['scratch_ref'] = str(self.base/'missing')
                result = await session.call_tool('collect', arguments)
                self.assertTrue(result.isError)
                self.assertIn('params_save_error', result.content[0].text)
                self.assertIn('unknown scratch reference', result.content[0].text)

    async def test_untracked_and_model_options_reach_collection(self):
        (self.repo/'src/new.py').write_text('new = 1\n')
        result=await self.run_collection(include_untracked=True,model='gemini-custom-model')
        self.assertEqual(result['status'],'validated',result)
        run=Path(result['run_dir'])
        manifest=json.loads((run/'source-manifest.json').read_text())
        self.assertIn('src/new.py',[e['path'] for e in manifest['files']])
        metrics=json.loads((run/'metrics.json').read_text())
        self.assertEqual(metrics['effective_model'],'gemini-custom-model')

    async def test_each_fact_scope_is_checked_before_worker_or_snapshot_creation(self):
        before = set(self.base.iterdir())
        with self.assertRaisesRegex(ValueError, 'regression tests'):
            await self.run_collection(evidence_needed=[
                {'fact':'definition','scope':['src/*.py']},
                {'fact':'regression tests','scope':['missing/*.c']}])
        self.assertEqual(set(self.base.iterdir()),before)

    async def test_empty_scope_failure_returns_error_without_catalog_crash(self):
        with self.assertRaisesRegex(ValueError, 'No source'):
            await self.run_collection(scope=['missing/*.c'])

    async def test_fact_scopes_are_unioned_including_untracked_tests(self):
        (self.repo/'tests').mkdir()
        (self.repo/'tests/test_example.py').write_text('assert 1 == 1\n')
        result=await self.run_collection(evidence_needed=[
            {'fact':'definition','scope':[]},
            {'fact':'regression tests','scope':['tests']}])
        run=Path(result['run_dir'])
        manifest=json.loads((run/'source-manifest.json').read_text())
        self.assertEqual([f['path'] for f in manifest['files']],['src/example.py','tests/test_example.py'])
        request=(run/'request.jsonl').read_text()
        self.assertIn('regression tests [scope: tests]',request)

    async def test_waits_and_returns_index_then_originals(self):
        nav={'root':str(self.base),'queries':[{'tool':'references','error':'unsupported'}]}
        result=await self.run_collection(navigation=nav,known_findings='The source is src/example.py.')
        self.assertEqual(result['status'],'validated')
        self.assertNotIn('source',result['locations'][0])
        run=Path(result['run_dir'])
        request=(run/'request.jsonl').read_text()
        metrics=json.loads((run/'metrics.json').read_text())
        self.assertEqual(metrics['timeout_seconds'],1800)
        argv=metrics['argv']
        self.assertEqual(argv[argv.index('--print-timeout')+1],'1800s')
        self.assertIn('unsupported',request)
        self.assertIn('The source is src/example.py.',request)
        full=read_evidence(str(run),[1])
        self.assertIn('    return 1',full['text'])
        self.assertEqual(read_evidence(str(run),[1])['text'],'')
        parts=[];offset=0
        while True:
            page=read_evidence(str(run),[1],offset=offset,max_chars=17)
            parts.append(page['text'])
            if page['next_offset'] is None:break
            offset=page['next_offset']
        self.assertEqual(''.join(parts),full['text'])

    async def test_mcp_transport_returns_index_and_selected_source_once(self):
        (self.repo/'src/nested').mkdir()
        (self.repo/'src/nested/other.py').write_text('other = 1\n')
        (self.repo/'src/notes.txt').write_text('not selected\n')
        subprocess.run(['git','-C',str(self.repo),'add','src'],check=True)
        params=StdioServerParameters(command=sys.executable,
                    args=[str(KIT/'payload/.codex/es/server.py')],env=dict(os.environ))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                schema=next(t.inputSchema for t in (await session.list_tools()).tools if t.name=='collect')
                self.assertNotIn('timeout',schema['properties'])
                result=await session.call_tool('collect',dict(repo=str(self.repo),
                    question='Where is f?',evidence_needed=[{'fact':'definition','scope':['src/**/*.py']}],scratch_ref=self.ref,
                    navigation=None,known_findings=''))
                self.assertFalse(result.isError)
                self.assertIsNone(result.structuredContent)
                index=json.loads(result.content[0].text)
                manifest=json.loads((Path(index['run_dir'])/'source-manifest.json').read_text())
                self.assertEqual(manifest['scopes'],['src/**/*.py'])
                self.assertEqual([e['path'] for e in manifest['files']],
                                 ['src/example.py','src/nested/other.py'])
                args=dict(run_dir=index['run_dir'],ids=[1])
                source=await session.call_tool('read_evidence',args)
                self.assertFalse(source.isError)
                self.assertIn('    return 1',json.loads(source.content[0].text)['text'])
                repeated=await session.call_tool('read_evidence',args)
                self.assertTrue(json.loads(repeated.content[0].text)['already_returned'])

    async def test_deadline_returns_failure_and_finalizes_usage(self):
        os.environ['FAKE_CASE']='timeout'
        with patch.dict('server.CONFIG',timeout_seconds=0.2):
            result=await self.run_collection()
        self.assertNotEqual(result['status'],'validated')
        self.assertEqual(result['locations'],[])
        saved = json.loads(Path(result['params_file']).read_text())
        self.assertEqual(saved['repo'], str(self.repo))
        self.assertEqual(saved['question'], 'Where is f defined?')
        metrics=json.loads((Path(result['run_dir'])/'metrics.json').read_text())
        self.assertTrue(metrics['task_usage_recorded'])

    async def test_cancellation_waits_for_worker_cleanup(self):
        os.environ['FAKE_CASE']='timeout'
        pid_file=self.base/'worker.pid'
        os.environ['FAKE_PID_FILE']=str(pid_file)
        task=asyncio.create_task(self.run_collection())
        async def started():
            while not pid_file.exists():await asyncio.sleep(0.01)
        await asyncio.wait_for(started(),5)
        pid=int(pid_file.read_text())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    async def test_cleanup_skips_active_file_and_directory_then_removes_cancelled_run(self):
        os.environ['FAKE_CASE']='timeout'
        pid_file=self.base/'worker.pid'
        os.environ['FAKE_PID_FILE']=str(pid_file)
        request=self.base/'request.json'
        request.write_text(json.dumps(dict(repo=str(self.repo), scratch_ref=self.ref,
            question='Where?', evidence_needed=[{'fact':'definition','scope':['src']}],
            navigation=None, known_findings='')))
        task=asyncio.create_task(collect(params_file=str(request)))
        try:
            async def started():
                while not pid_file.exists(): await asyncio.sleep(0.01)
            await asyncio.wait_for(started(),5)
            active=self.space.delete()
            self.assertEqual(active['skipped_active'], [self.ref])
            self.assertEqual(active['deleted'], [])
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(request.exists())
        removed=self.space.delete()
        self.assertEqual(removed['deleted'], [self.ref])
        self.assertFalse(request.exists())
        self.assertFalse(active_paths)

    async def test_failed_collection_keeps_argument_file_until_success(self):
        request=self.base/'request.json'
        request.write_text(json.dumps(dict(repo=str(self.repo), scratch_ref=self.ref,
            question='Where?', evidence_needed=[{'fact':'definition','scope':['src']}],
            navigation=None, known_findings='')))
        os.environ['FAKE_CASE']='timeout'
        with patch.dict('server.CONFIG',timeout_seconds=0.2):
            failed=await collect(params_file=str(request))
        self.assertNotEqual(failed['status'], 'validated')
        self.assertTrue(request.exists())
        os.environ['FAKE_CASE']='ok'
        result=await collect(params_file=str(request))
        self.assertEqual(result['status'], 'validated')
        self.assertFalse(request.exists())

    async def test_argument_unlink_failure_preserves_result_and_scratch_can_delete_it(self):
        request = self.base/'request.json'
        request.write_text(json.dumps(dict(repo=str(self.repo), scratch_ref=self.ref,
            question='Where?', evidence_needed=[{'fact':'definition','scope':['src']}],
            navigation=None, known_findings='')))
        with patch.object(Path, 'unlink', side_effect=PermissionError('denied')):
            result = await collect(params_file=str(request))
        self.assertEqual(result['status'], 'validated')
        self.assertEqual(result['params_delete_error'], 'denied')
        self.assertTrue(request.exists())
        self.assertEqual(self.space.delete()['deleted'], [self.ref])
        self.assertFalse(request.exists())

    async def test_implicit_offset_resumes_and_id_order_is_stable(self):
        result=await self.run_collection();run=Path(result['run_dir'])
        handoff=json.loads((run/'handoff.json').read_text())
        handoff['related']=[dict(handoff['primary'][0],start=2)]
        (run/'handoff.json').write_text(json.dumps(handoff))
        first=read_evidence(str(run),[2,1],max_chars=20)
        rest=read_evidence(str(run),[1,2])
        full=read_evidence(str(run),[1,2],offset=0)
        self.assertEqual(first['text']+rest['text'],full['text'])

    async def test_read_rechecks_source(self):
        result=await self.run_collection()
        (self.repo/'src/example.py').write_text('changed\n')
        with self.assertRaises(EvidenceError):read_evidence(result['run_dir'],[1])

    async def test_symbols_receipts_skip_only_matching_version_and_keep_patch_text(self):
        raw = 'def f():\r\n    return "日本語😀"'.encode()
        file = self.repo/'src/example.py'
        file.write_bytes(raw)
        receipt = {'path':str(file), 'contentSha256':hashlib.sha256(raw).hexdigest(),
                   'range':{'start':{'line':1,'character':1},'end':{'line':2,'character':1}}}
        nav = {'root':str(self.base),'queries':[{'tool':'read_symbols', 'result':{
            'content':[{'type':'text','text':raw.decode()}],
            'structuredContent':{'readSources':[receipt]}}}]}
        result = await self.run_collection(navigation=nav)
        page = read_evidence(result['run_dir'],[1])
        self.assertNotIn('def f():',page['text'])
        self.assertIn('    return "日本語😀"',page['text'])
        replay = read_evidence(result['run_dir'],[1],offset=0)
        self.assertIn(raw.decode(),replay['text'])
        receipt['contentSha256'] = '0'*64
        result = await self.run_collection(navigation=nav)
        self.assertIn(raw.decode(),read_evidence(result['run_dir'],[1])['text'])

    async def test_changed_id_selection_does_not_repeat_completed_source(self):
        result = await self.run_collection(); run = Path(result['run_dir'])
        handoff = json.loads((run/'handoff.json').read_text())
        handoff['related'] = [dict(handoff['primary'][0],start=2,evidence='return value')]
        (run/'handoff.json').write_text(json.dumps(handoff))
        first = read_evidence(str(run),[2])
        second = read_evidence(str(run),[1,2])
        self.assertIn('    return 1',first['text'])
        self.assertNotIn('    return 1',second['text'])
        self.assertIn('def f():',second['text'])
        self.assertNotIn('return value',second['text'])

    async def test_partial_delivery_does_not_hide_unread_source_in_another_selection(self):
        result = await self.run_collection(); run = Path(result['run_dir'])
        handoff = json.loads((run/'handoff.json').read_text())
        handoff['related'] = [dict(handoff['primary'][0],start=2)]
        (run/'handoff.json').write_text(json.dumps(handoff))
        read_evidence(str(run),[1],max_chars=5)
        self.assertIn('    return 1',read_evidence(str(run),[2])['text'])
        # The in-flight selection keeps stable offsets even after another read.
        tail = read_evidence(str(run),[1])
        self.assertIn('def f():\n    return 1\n',tail['text'])

    async def test_failed_collection_returns_error_without_retry(self):
        os.environ['FAKE_CASE']='auth'
        result=await self.run_collection()
        self.assertNotEqual(result['status'],'validated')
        self.assertIn('authentication',result['error'])
        self.assertEqual(result['locations'],[])

    async def test_overlapping_ids_do_not_duplicate_original_lines(self):
        result=await self.run_collection();run=Path(result['run_dir'])
        handoff=json.loads((run/'handoff.json').read_text())
        handoff['related']=[dict(handoff['primary'][0],start=2)]
        (run/'handoff.json').write_text(json.dumps(handoff))
        page=read_evidence(str(run),[1,2])
        self.assertEqual(page['text'].count('    return 1'),1)


if __name__=='__main__':unittest.main()
