"""Suggest a better retrieval method after a tool reports truncated output."""
from __future__ import annotations

import json
import re
import sys

HINT = (
    'Output was truncated. If further investigation spans multiple source files, '
    'consider codex-explorer-solver:explore-solve to collect only the missing evidence. '
    'For a known location, a targeted read or a larger output limit may suffice. '
    'For build/test output, prefer an existing saved log or the skill’s capture tool.'
)
TRUNCATION = re.compile(r'…\d+ (?:tokens|chars) truncated…')


def hint(event: dict) -> dict:
    response = event.get('tool_response')
    if isinstance(response, dict):
        text = response.get('output', '')
    else:
        text = response
    if not isinstance(text, str) or not TRUNCATION.search(text):
        return {}
    return {'hookSpecificOutput': {
        'hookEventName': 'PostToolUse', 'additionalContext': HINT,
    }}


if __name__ == '__main__':
    print(json.dumps(hint(json.load(sys.stdin)), ensure_ascii=False))
