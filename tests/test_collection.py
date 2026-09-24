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
from server import collect, read_evidence
from evidence import EvidenceError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base/'repo'
        self.repo.mkdir()
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        (self.repo/'src').mkdir()
        (self.repo/'src/example.py').write_text('def f():\n    return 1\n')
        subprocess.run(['git','-C',str(self.repo),'add','src'],check=True)
        bin_dir = self.base/'bin';bin_dir.mkdir()
        (bin_dir/'agy').symlink_to(KIT/'tests/fixtures/fake_agy.py')
        self.env = patch.dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                              FAKE_CASE='ok',FAKE_ORIGINAL_FILE=str(self.repo/'src/example.py'))
        self.env.start()

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    async def run_collection(self, **kw):
        kw.setdefault('navigation',None)
        kw.setdefault('known_findings','')
        scope=kw.pop('scope',['src'])
        return await collect(str(self.repo),'Where is f defined?', ['definition'],str(self.base),scope,**kw)

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

    async def test_untracked_and_model_options_reach_collection(self):
        (self.repo/'src/new.py').write_text('new = 1\n')
        result=await self.run_collection(include_untracked=True,deep=True,model='gemini-custom-model')
        self.assertEqual(result['status'],'validated',result)
        run=Path(result['run_dir'])
        manifest=json.loads((run/'source-manifest.json').read_text())
        self.assertIn('src/new.py',[e['path'] for e in manifest['files']])
        metrics=json.loads((run/'metrics.json').read_text())
        self.assertEqual(metrics['effective_model'],'gemini-custom-model')

    async def test_waits_and_returns_index_then_originals(self):
        nav={'root':str(self.base),'queries':[{'tool':'references','error':'unsupported'}]}
        result=await self.run_collection(navigation=nav,known_findings='The source is src/example.py.')
        self.assertEqual(result['status'],'validated')
        self.assertNotIn('source',result['locations'][0])
        run=Path(result['run_dir'])
        request=(run/'request.jsonl').read_text()
        self.assertIn('unsupported',request)
        self.assertIn('The source is src/example.py.',request)
        full=read_evidence(str(run),[1])
        self.assertIn('2:     return 1',full['text'])
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
                result=await session.call_tool('collect',dict(repo=str(self.repo),
                    question='Where is f?',evidence_needed=['definition'],scratch_dir=str(self.base),
                    scope=['src/**/*.py'],navigation=None,known_findings=''))
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
                self.assertIn('2:     return 1',json.loads(source.content[0].text)['text'])
                repeated=await session.call_tool('read_evidence',args)
                self.assertTrue(json.loads(repeated.content[0].text)['already_returned'])

    async def test_deadline_returns_failure_and_finalizes_usage(self):
        os.environ['FAKE_CASE']='timeout'
        result=await self.run_collection(timeout=0.2)
        self.assertNotEqual(result['status'],'validated')
        self.assertEqual(result['locations'],[])
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
        self.assertEqual(page['text'].count('2:     return 1'),1)


if __name__=='__main__':unittest.main()
