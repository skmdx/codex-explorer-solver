from __future__ import annotations
import asyncio
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT/'payload/.codex/es'))
import subagents


class SubagentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root/'repo'
        self.repo.mkdir()
        self.source = self.repo/'file.txt'
        self.source.write_text('original\n')
        self.config = patch.dict(subagents.CONFIG, executable=str(KIT/'tests/fixtures/fake_agy.py'))
        self.env = patch.dict(os.environ, FAKE_CASE='ok', FAKE_ORIGINAL_FILE=str(self.source))
        self.config.start(); self.env.start()

    def tearDown(self):
        self.env.stop(); self.config.stop(); self.temp.cleanup()

    async def run_task(self, **kwargs):
        return await subagents.run('Review the given facts.', str(self.root), **kwargs)

    async def test_review_returns_final_response_and_usage(self):
        result = await self.run_task(model='claude-test', repo=str(self.repo))
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['usage']['total_tokens'], 130)
        self.assertIn('Reviewed', result['response'])
        report = json.loads((Path(result['run_dir'])/'report.json').read_text())
        argv = report['argv']
        self.assertEqual(argv[argv.index('--print-timeout')+1], '1800s')
        self.assertEqual(argv[argv.index('--model')+1], 'claude-test')
        self.assertNotIn('--effort', argv)
        self.assertNotIn('Review the given facts.', argv)
        self.assertEqual(self.source.read_text(), 'original\n')

    async def test_edit_uses_real_repository_and_edit_mode(self):
        result = await self.run_task(repo=str(self.repo), mode='edit')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(self.source.read_text(), 'edited\n')
        argv = json.loads((Path(result['run_dir'])/'report.json').read_text())['argv']
        self.assertEqual(argv[argv.index('--mode')+1], 'accept-edits')

    async def test_edit_requires_repository(self):
        with self.assertRaisesRegex(ValueError, 'edit requires repo'):
            await self.run_task(mode='edit')

    async def test_process_failure_keeps_response_and_usage(self):
        os.environ['FAKE_CASE'] = 'nonzero_success'
        result = await self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Reviewed', result['response'])
        self.assertEqual(result['usage']['total_tokens'], 130)

    async def test_native_timeout_with_partial_response_is_not_success(self):
        os.environ['FAKE_CASE'] = 'native_timeout'
        result = await self.run_task()
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['response'], 'Partial review.')

    async def test_empty_response_is_failure(self):
        os.environ['FAKE_CASE'] = 'missing_response'
        result = await self.run_task()
        self.assertEqual(result['status'], 'failed')

    async def test_deadline_reaps_process(self):
        os.environ['FAKE_CASE'] = 'timeout'
        pid = self.root/'pid'; os.environ['FAKE_PID_FILE'] = str(pid)
        with patch.dict(subagents.CONFIG, timeout_seconds=0.2):
            result = await self.run_task()
        self.assertEqual(result['status'], 'timeout')
        with self.assertRaises(ProcessLookupError): os.kill(int(pid.read_text()), 0)

    async def test_cancellation_reaps_process(self):
        os.environ['FAKE_CASE'] = 'timeout'
        pid = self.root/'pid'; os.environ['FAKE_PID_FILE'] = str(pid)
        task = asyncio.create_task(self.run_task())
        async def started():
            while not pid.exists(): await asyncio.sleep(0.01)
        await asyncio.wait_for(started(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        with self.assertRaises(ProcessLookupError): os.kill(int(pid.read_text()), 0)

    async def test_mcp_schema_has_no_wait_or_timeout_controls(self):
        params = StdioServerParameters(command=sys.executable, args=[str(KIT/'payload/.codex/es/subagents.py')])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name:t for t in (await session.list_tools()).tools}
                self.assertEqual(set(tools), {'models','run'})
                fields = tools['run'].inputSchema['properties']
                self.assertNotIn('timeout', fields)
                self.assertNotIn('effort', fields)
                self.assertNotIn('background', fields)

    async def test_model_listing(self):
        self.assertIn('gemini-3.8-flash-high', await subagents.models())

    async def test_quota_fallback_resumes_progress_through_pro_to_flash(self):
        opus, pro, flash = subagents.CONFIG['review_model_order']
        os.environ['FAKE_MODEL_CASES'] = json.dumps({opus:'quota_after_progress',pro:'quota'})
        result = await self.run_task(model=opus)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['model'],flash)
        self.assertIn('Verified checkpoint',result['response'])
        self.assertEqual([a['model'] for a in result['attempts']],[opus,pro,flash])
        self.assertEqual([a['resumed_from'] for a in result['attempts']],[None,'fixture-1','fixture-1'])
        self.assertEqual(result['usage']['total_tokens'],390)
        self.assertEqual([a['usage']['total_tokens'] for a in result['attempts']],[130,260,390])
        for attempt in result['attempts']:
            self.assertTrue((Path(attempt['run_dir'])/'events.jsonl').exists())

    async def test_finished_resume_with_inherited_quota_is_completed(self):
        opus, pro, _ = subagents.CONFIG['review_model_order']
        os.environ['FAKE_MODEL_CASES'] = json.dumps({opus:'quota',pro:'inherited_quota'})
        result = await self.run_task(model=opus)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['model'],pro)
        self.assertEqual(len(result['attempts']),2)

    async def test_startup_unavailability_starts_next_model_with_original_task(self):
        opus, pro, _ = subagents.CONFIG['review_model_order']
        os.environ['FAKE_MODEL_CASES'] = json.dumps({opus:'model_unavailable'})
        result = await self.run_task(model=opus)
        self.assertEqual(result['model'],pro)
        self.assertEqual(result['status'],'completed',result)
        self.assertIsNone(result['attempts'][1]['resumed_from'])
        self.assertIsNone(result['usage']['total_tokens'])
        self.assertFalse(result['usage']['usage_complete'])
        request=json.loads((Path(result['attempts'][1]['run_dir'])/'request.jsonl').read_text())
        self.assertEqual(request['message']['content'],'Review the given facts.')

    async def test_all_models_unavailable_returns_failure_and_all_attempts(self):
        os.environ['FAKE_CASE']='quota'
        result=await self.run_task(model=subagents.CONFIG['review_model_order'][0])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(result['attempts']),3)

    async def test_pro_starts_at_pro_and_timeout_reaps_without_trying_flash(self):
        opus, pro, flash=subagents.CONFIG['review_model_order']
        os.environ['FAKE_MODEL_CASES']=json.dumps({pro:'quota'})
        result=await self.run_task(model=pro)
        self.assertEqual([a['model'] for a in result['attempts']],[pro,flash])
        os.environ['FAKE_MODEL_CASES']=json.dumps({opus:'quota_after_progress',pro:'timeout'})
        pid=self.root/'pid';os.environ['FAKE_PID_FILE']=str(pid)
        with patch.dict(subagents.CONFIG,timeout_seconds=0.5), \
                patch.object(subagents,'run_attempt',wraps=subagents.run_attempt) as attempts:
            result=await self.run_task(model=opus)
        self.assertEqual(result['status'],'timeout',result)
        self.assertEqual(len(result['attempts']),2)
        self.assertLess(attempts.call_args_list[1].args[7],attempts.call_args_list[0].args[7])
        with self.assertRaises(ProcessLookupError):os.kill(int(pid.read_text()),0)

    async def test_cancellation_during_fallback_does_not_launch_flash(self):
        opus, pro, _=subagents.CONFIG['review_model_order']
        os.environ['FAKE_MODEL_CASES']=json.dumps({opus:'quota_after_progress',pro:'timeout'})
        pid=self.root/'pid';os.environ['FAKE_PID_FILE']=str(pid)
        task=asyncio.create_task(self.run_task(model=opus))
        async def started():
            while not pid.exists():await asyncio.sleep(0.01)
        await asyncio.wait_for(started(),5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        with self.assertRaises(ProcessLookupError):os.kill(int(pid.read_text()),0)
        self.assertEqual(len(list(self.root.glob('agy-subagent-*/attempt-*'))),2)

    async def test_auth_protocol_timeout_and_edit_do_not_fallback(self):
        opus=subagents.CONFIG['review_model_order'][0]
        for case in ('auth','bad_model','invalid_json','native_timeout','nonzero_success'):
            with self.subTest(case=case):
                os.environ['FAKE_CASE']=case
                result=await self.run_task(model=opus)
                self.assertEqual(len(result['attempts']),1)
                self.assertNotEqual(result['status'],'completed')
        os.environ['FAKE_CASE']='quota'
        result=await self.run_task(model=opus,mode='edit',repo=str(self.repo))
        self.assertEqual(len(result['attempts']),1)

    async def test_mcp_call_completes_the_whole_fallback_chain(self):
        opus, pro, flash=subagents.CONFIG['review_model_order']
        bin_dir=self.root/'bin';bin_dir.mkdir()
        (bin_dir/'agy').symlink_to(KIT/'tests/fixtures/fake_agy.py')
        env=dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                 FAKE_MODEL_CASES=json.dumps({opus:'quota_after_progress',pro:'quota'}))
        params=StdioServerParameters(command=sys.executable,
                    args=[str(KIT/'payload/.codex/es/subagents.py')],env=env)
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                reply=await session.call_tool('run',dict(task='Review the given facts.',
                    scratch_dir=str(self.root),model=opus))
                self.assertFalse(reply.isError)
                result=json.loads(reply.content[0].text)
                self.assertEqual(result['status'],'completed',result)
                self.assertEqual(result['model'],flash)
                self.assertEqual(len(result['attempts']),3)
                self.assertIn('Verified checkpoint',result['response'])

    async def test_running_mcp_survives_removal_of_its_plugin_cache(self):
        cached=self.root/'old-plugin'
        shutil.copytree(KIT/'payload/.codex/es',cached)
        bin_dir=self.root/'bin';bin_dir.mkdir()
        (bin_dir/'agy').symlink_to(KIT/'tests/fixtures/fake_agy.py')
        env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
        params=StdioServerParameters(command=sys.executable,args=[str(cached/'subagents.py')],env=env)
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                shutil.rmtree(cached)
                for mode in ('review','edit'):
                    reply=await session.call_tool('run',dict(task='Use the supplied repository.',
                        scratch_dir=str(self.root),repo=str(self.repo),mode=mode))
                    self.assertFalse(reply.isError,reply)
                    result=json.loads(reply.content[0].text)
                    self.assertEqual(result['status'],'completed',result)
                    agent='es-reviewer' if mode=='review' else 'es-editor'
                    definition=Path(result['run_dir'])/'workspace/.agents/agents'/f'{agent}.md'
                    self.assertEqual(definition.read_bytes(),subagents.AGENT_DEFINITIONS[agent])


if __name__ == '__main__': unittest.main()
