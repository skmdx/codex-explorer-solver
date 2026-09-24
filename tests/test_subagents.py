from __future__ import annotations
import asyncio
import json
import os
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


if __name__ == '__main__': unittest.main()
