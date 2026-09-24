# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.12,<2", "charset-normalizer>=3.4,<4"]
# ///
"""Blocking AGY collection and selective, paginated original-source retrieval."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import signal
import sys
import tempfile

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from evidence import format_evidence, load_handoff, verify_handoff

HERE = Path(__file__).resolve().parent
mcp = FastMCP("explore-solve")


def catalog(run: Path) -> dict:
    report = json.loads((run/'report.json').read_text())
    result = {k: report[k] for k in ('status', 'error', 'handoff_status', 'usage')}
    result['run_dir'] = str(run)
    evidence = report.get('evidence')
    result['locations'] = []
    if evidence:
        for category in ('primary', 'related'):
            for item in evidence[category]:
                result['locations'].append(dict(
                    id=len(result['locations'])+1, role=category,
                    **{k:item[k] for k in ('path','start','end','symbol','evidence')}))
        result['unresolved'] = evidence['unresolved']
    if result['locations']:
        result['next'] = 'Read needed IDs with read_evidence before making Solver judgments.'
    return result


@mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def collect(
    repo: str, question: str, evidence_needed: list[str], scratch_dir: str,
    scope: list[str], navigation: dict | None,
    known_findings: str, timeout: float = 900,
    paths: list[str] | None = None, include_untracked: bool = False,
    model: str | None = None, deep: bool = False,
    encodings: dict[str, str] | None = None,
) -> dict:
    """Collect missing source evidence through Gemini High and wait for completion.

    One call includes the entire wait; no polling or background process management.
    question is the next Solver decision, evidence_needed names the code facts
    needed for it (definitions, conditions, updates, callers), not a whole issue.
    Pass known Symbols results directly as navigation={root: LSP workspacePath,
    queries: [{tool, args, result/error}]}. Do not print/transcribe them first.
    scope selects repository-relative files/directories, not a limit on LSP hits.
    scratch_dir is an existing absolute temporary directory outside repo.
    Returns an index without source dumps. Use read_evidence for selected IDs.
    Use navigation=null only when there is no useful symbol seed. known_findings
    carries relevant prior observations (empty string for a new investigation).
    paths selects Reader input files, including Git hooks and external files;
    use scope=[], navigation=null and no deep/include_untracked with paths.
    Otherwise include_untracked includes non-ignored untracked source files.
    model overrides the configured model; deep selects the configured deep model.
    Encodings are detected per file; encodings={path: codec} corrects known mistakes.
    """
    root = Path(repo).resolve(strict=True)
    scratch = Path(scratch_dir).resolve(strict=True)
    if scratch == root or root in scratch.parents:
        raise ValueError('scratch_dir must be outside repo')
    if not question.strip() or not evidence_needed:
        raise ValueError('provide the next question and missing evidence')
    work = Path(tempfile.mkdtemp(prefix='explore-solve-', dir=scratch))
    run = work/'result'
    task = work/'task.txt'
    task.write_text('SOLVER QUESTION (do not solve it):\n'+question
                    +'\n\nCOLLECT THESE CODE FACTS:\n'
                    +'\n'.join('- '+s for s in evidence_needed)
                    +'\n\nALREADY KNOWN:\n'+known_findings
                    +'\nReturn the smallest source blocks establishing these facts. '
                    'Do not collect whole functions unless needed for the facts.\n')
    argv = [sys.executable, str(HERE/'locate.py'), '--repo', str(root),
            '--task-file', str(task), '--state-dir', str(work/'state'),
            '--out-dir', str(run), '--timeout', str(timeout)]
    for path in scope:
        argv.extend(['--scope',path])
    if paths is not None:
        argv.extend(['--mode','reader'])
        for path in paths:
            argv.extend(['--path',path])
    if include_untracked:
        argv.append('--include-untracked')
    if model is not None:
        argv.extend(['--model',model])
    if deep:
        argv.append('--deep')
    for path, encoding in (encodings or {}).items():
        argv.extend(['--encoding',f'{path}={encoding}'])
    if navigation is not None:
        nav = work/'navigation.json'
        nav.write_text(json.dumps(navigation, ensure_ascii=False))
        argv.extend(['--navigation-file',str(nav)])
    with (work/'runner.log').open('wb') as log:
        proc = await asyncio.create_subprocess_exec(*argv, stdout=log, stderr=log)
        try:
            await proc.wait()
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.send_signal(signal.SIGINT)
                await asyncio.shield(proc.wait())
            raise
    if not (run/'report.json').exists():
        raise RuntimeError(f'Collection failed: {(work/"runner.log").read_text()}')
    return catalog(run)


@mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def read_evidence(run_dir: str, ids: list[int], offset: int | None = None,
                  max_chars: int = 12000) -> dict:
    """Read selected original-source IDs from collect, checking current hashes.

    Output is paginated, never silently truncated: repeat the same IDs to continue
    from the last delivered offset. Already completed selections return no source.
    Explicit offset=0 rereads after context loss. Select just the evidence needed.
    Reuse returned source; do not reread those ranges with shell tools. Increase max_chars
    if the client can display more. All evidence remains saved in run_dir.
    """
    if (offset is not None and offset < 0) or max_chars < 1 or not ids:
        raise ValueError('provide IDs, nonnegative offset and positive max_chars')
    run = Path(run_dir)
    handoff = load_handoff((run/'handoff.json').read_bytes())
    metadata = json.loads((run/'metrics.json').read_text())
    entries = [(category,item) for category in ('primary','related') for item in handoff[category]]
    selected = dict(handoff, primary=[], related=[])
    for i in sorted(set(ids)):
        if i < 1 or i > len(entries):
            raise ValueError(f'unknown evidence ID {i}')
        category,item = entries[i-1]
        selected[category].append(item)
    verified = verify_handoff(Path(metadata['repo']), selected, include_source=True)
    source = format_evidence(verified)
    offsets_path = run/'read_offsets.json'
    offsets: dict[str, int] = json.loads(offsets_path.read_text()) if offsets_path.exists() else {}
    key = ','.join(map(str,sorted(set(ids))))
    if offset is None:
        offset = offsets.get(key,0)
    end = min(offset+max_chars, len(source))
    offsets[key] = end
    offsets_path.write_text(json.dumps(offsets))
    return dict(ids=ids, offset=offset, already_returned=offset==len(source),
                text=source[offset:end], next_offset=end if end < len(source) else None,
                total_chars=len(source), complete=end==len(source))


if __name__ == '__main__':
    mcp.run(transport='stdio')
