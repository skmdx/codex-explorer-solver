import json
from pathlib import Path
import subprocess
import os
import tempfile
import unittest

KIT = Path(__file__).resolve().parents[1]
COMMAND = json.loads((KIT/'hooks/hooks.json').read_text())['hooks']['PostToolUse'][0]['hooks'][0]['command']


class TruncationHintTests(unittest.TestCase):
    def run_hook(self, response, **kwargs):
        run = subprocess.run(['sh', '-c', COMMAND], input=json.dumps({'tool_response':response}),
                             text=True,capture_output=True,check=True,**kwargs)
        self.assertEqual(run.stderr,'')
        return json.loads(run.stdout)

    def test_normal_output_is_silent(self):
        for output in ('short output', 'truncated is a word', 'x'*50000):
            with self.subTest(output=output[:30]):
                self.assertEqual(self.run_hook({'output': output}), {})

    def test_unified_exec_truncation_adds_only_advice(self):
        response = {'output': '1\n2\n…3810 tokens truncated…\n1999\n2000\n',
                    'exit_code': 1, 'original_token_count': 3900}
        original = dict(response)
        result = self.run_hook(response)
        self.assertEqual(set(result),{'hookSpecificOutput'})
        self.assertEqual(set(result['hookSpecificOutput']),{'hookEventName','additionalContext'})
        self.assertEqual(result['hookSpecificOutput']['hookEventName'],'PostToolUse')
        self.assertEqual(response, original)
        self.assertIn('consider', result['hookSpecificOutput']['additionalContext'])

    def test_text_response_and_character_truncation(self):
        self.assertTrue(self.run_hook('first…320 chars truncated…last'))

    def test_loaded_hook_survives_plugin_cache_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'old-plugin';root.mkdir()
            config=root/'hooks.json';config.write_text((KIT/'hooks/hooks.json').read_text())
            command=json.loads(config.read_text())['hooks']['PostToolUse'][0]['hooks'][0]['command']
            config.unlink();root.rmdir()
            run=subprocess.run(['sh','-c',command],input=json.dumps({'tool_response':'…100 tokens truncated…'}),
                               env=dict(os.environ,PLUGIN_ROOT=str(root)),text=True,capture_output=True,check=True)
            self.assertIn('consider',json.loads(run.stdout)['hookSpecificOutput']['additionalContext'])


if __name__ == '__main__':
    unittest.main()
