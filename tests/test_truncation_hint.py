import json
from pathlib import Path
import subprocess
import sys
import unittest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT/'payload/.codex/es'))
from truncation_hint import HINT, hint


class TruncationHintTests(unittest.TestCase):
    def test_normal_output_is_silent(self):
        for output in ('short output', 'truncated is a word', 'x'*50000):
            with self.subTest(output=output[:30]):
                self.assertEqual(hint({'tool_response': {'output': output}}), {})

    def test_unified_exec_truncation_adds_only_advice(self):
        response = {'output': '1\n2\n…3810 tokens truncated…\n1999\n2000\n',
                    'exit_code': 1, 'original_token_count': 3900}
        original = dict(response)
        result = hint({'tool_response': response})
        self.assertEqual(result, {'hookSpecificOutput': {
            'hookEventName': 'PostToolUse', 'additionalContext': HINT}})
        self.assertEqual(response, original)
        self.assertIn('consider', HINT)

    def test_text_response_and_character_truncation(self):
        self.assertTrue(hint({'tool_response': 'first…320 chars truncated…last'}))

    def test_cli_emits_advice_and_exits_successfully(self):
        run = subprocess.run([sys.executable, str(KIT/'payload/.codex/es/truncation_hint.py')],
                             input=json.dumps({'tool_response': '…100 tokens truncated…'}),
                             text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(run.stdout)['hookSpecificOutput']['additionalContext'], HINT)
        self.assertEqual(run.stderr, '')


if __name__ == '__main__':
    unittest.main()
