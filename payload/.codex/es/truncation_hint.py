"""Suggest a better retrieval method after a tool reports truncated output."""
from __future__ import annotations

import json
import re
import sys

HINT = (
    'Output was truncated. Reconsider the scope or method of your next retrieval. '
    'For broad searches through unread source or large build/test output, consider '
    'using the codex-explorer-solver:explore-solve skill.'
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
