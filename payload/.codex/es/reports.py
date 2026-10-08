"""Bounded views of completed reports shared by both MCP servers."""
import json
from pathlib import Path

from scratch_space import ScratchSpace


def text_page(text: str, offset: int = 0, max_chars: int = 6000) -> dict:
    if not 1000 <= max_chars <= 20000 or not 0 <= offset <= len(text):
        raise ValueError('max_chars must be 1000..20000 and offset within the text')
    end = min(offset + max_chars, len(text))
    return dict(text=text[offset:end], offset=offset, total_chars=len(text),
                next_offset=end if end < len(text) else None)


def read_report(run_dir: str, pointer: str = '', offset: int = 0, max_chars: int = 6000) -> dict:
    """Read a saved report or JSON pointer in bounded text pages.

    Use /response for AGY's remaining result, /usage or /attempts for diagnostics,
    or /evidence/unresolved for collection details. Repeat run_dir and pointer
    with next_offset; null means complete. Strings are returned verbatim, other
    values as JSON. max_chars counts source characters, excluding the envelope.
    """
    if pointer and not pointer.startswith('/'):
        raise ValueError('pointer must be empty or a JSON pointer starting with /')
    with ScratchSpace().lease_path(Path(run_dir) / 'report.json') as path:
        value = json.loads(path.read_text())
        for part in pointer.split('/')[1:]:
            key = part.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return text_page(text, offset, max_chars)
