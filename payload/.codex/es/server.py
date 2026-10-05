# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.12,<2", "charset-normalizer>=3.4,<4"]
# ///
"""Blocking AGY collection and selective, paginated original-source retrieval."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
import signal
import sys
import tempfile
import tomllib
from typing import Any
from typing_extensions import TypedDict

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import validate_call

from evidence import evidence_blocks, format_evidence, load_handoff, verify_handoff
from agy_snapshot import select_paths
from scratch_space import ScratchSpace

HERE = Path(__file__).resolve().parent
CONFIG = tomllib.loads((HERE/'agy.toml').read_text())
active_paths: Counter[Path] = Counter()

def save_failed_arguments(arguments: dict[str, Any]) -> dict:
    """Preserve an inline request without hiding its original failure."""
    try:
        contents = json.dumps(arguments, ensure_ascii=False, indent=2) + '\n'
        with ScratchSpace().lease(arguments['scratch_ref']) as scratch, tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         prefix='collect-failed-', suffix='.json',
                                         dir=scratch, delete=False) as file:
            file.write(contents)
        return {'params_file': file.name,
                'retry': 'Call collect with params_file and optional updates containing only changed arguments. Success deletes the saved request; scratch.delete removes unused temporary files.'}
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {'params_save_error': f'Could not save arguments in scratch_ref: {error}'}


class CollectionMCP(FastMCP):
    async def call_tool(self, name: str, arguments: dict[str, Any]):
        try:
            return await super().call_tool(name, arguments)
        except Exception as error:
            if name != 'collect' or arguments.get('params_file') is not None:
                raise
            saved = save_failed_arguments(arguments)
            raise ToolError(f'{error}\n{json.dumps(saved, ensure_ascii=False)}') from error


mcp = CollectionMCP("explore-solve")


class EvidenceRequest(TypedDict):
    fact: str
    scope: list[str]


def catalog(run: Path) -> dict:
    report = json.loads((run/'report.json').read_text())
    result = {k: report[k] for k in ('status', 'error', 'handoff_status', 'usage')}
    result['effective_model'] = report.get('effective_model')
    result['skipped_models'] = report.get('skipped_models',[])
    result['attempts'] = [{k:a.get(k) for k in ('model','error','elapsed_seconds')} for a in report.get('attempts',[])]
    result['run_dir'] = str(run)
    evidence = report.get('evidence')
    result['locations'] = []
    if (report.get('scope') or {}).get('unmatched_scopes'):
        result['unmatched_scopes'] = report['scope']['unmatched_scopes']
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
    repo: str | None = None, question: str | None = None,
    evidence_needed: list[EvidenceRequest] | None = None, scratch_ref: str | None = None,
    navigation: dict | str | None = None,
    known_findings: str | None = None,
    paths: list[str] | None = None, include_untracked: bool | None = None,
    model: str | None = None,
    encodings: dict[str, str] | None = None,
    params_file: str | None = None,
    updates: dict[str, Any] | None = None,
) -> dict:
    """Collect source evidence through Sonnet, falling back to Gemini Flash High on usage limits, and wait for completion.

    Call this tool directly. The host exposes this namespace as direct-only,
    outside code-mode cells. One call waits for completion without a poll handle.
    navigation also accepts the ID returned by remember_navigation.
    The collection deadline is set in agy.toml, not selected per tool call.
    question is the next Solver decision. Each evidence_needed item pairs a fact
    (definition, condition, caller or test) with its own scope. The harness unions
    those scopes and rejects unmatched scopes before starting a model.
    Pass known Symbols results directly as navigation={root: LSP workspacePath,
    queries: [{tool, args, result/error}]}. result is the unchanged MCP response,
    including structuredContent. Include prior read_symbols results to reuse read ranges.
    Each scope selects repository-relative files/directories or globs (e.g. src/**/*.c).
    * and ? stay within a path component; ** spans directories. [] selects all.
    Explicit scopes include ignored/untracked files and nested repositories.
    [] or ["."] uses Git's tracked files (plus include_untracked if requested).
    These are source export patterns, not a limit on LSP hits.
    scratch_ref is returned by scratch.create; its directory must be outside repo.
    Returns an index without source dumps. Use read_evidence for selected IDs.
    Use navigation=null only when there is no useful symbol seed. known_findings
    carries relevant prior observations (empty string for a new investigation).
    paths selects Reader input files, including Git hooks and external files;
    use empty item scopes, navigation=null and no include_untracked with paths.
    With no scope filter, include_untracked adds non-ignored untracked files.
    model overrides the configured model.
    Encodings are detected per file; encodings={path: codec} corrects known mistakes.
    For large requests, save these arguments as a UTF-8 JSON object and call with
    only params_file="/absolute/path/request.json", stored inside a scratch-created
    directory. Do not mix non-null inline
    arguments with params_file. File contents use the same validation as inline
    arguments; params_file cannot occur inside the file. Paths retain their usual
    meaning (they are not relative to the JSON file).
    Failed inline calls save their arguments as collect-failed-*.json under scratch_ref
    and return params_file for retry. If saving fails, params_save_error explains why.
    Retry with params_file and optional updates containing only changed top-level
    arguments; updates replace whole values, including lists, and may set null.
    The tool saves corrections; no file editing or full argument resubmission is needed.
    A validated collection deletes
    its params_file automatically. Failed requests retain it. Use scratch.delete
    to remove temporary outputs after reading evidence. Active calls are protected.
    """
    arguments: dict[str, Any] = dict(repo=repo, question=question, evidence_needed=evidence_needed,
                     scratch_ref=scratch_ref, navigation=navigation,
                     known_findings=known_findings, paths=paths,
                     include_untracked=include_untracked, model=model, encodings=encodings)
    with ExitStack() as leases:
        if params_file is not None:
            leases.enter_context(ScratchSpace().lease_path(params_file))
        elif scratch_ref is not None:
            leases.enter_context(ScratchSpace().lease(scratch_ref))
        return await collect_request(arguments, params_file, updates)


async def collect_request(arguments: dict[str, Any], params_file: str | None,
                          updates: dict[str, Any] | None) -> dict:
    file = None
    if params_file is not None:
        if any(value is not None for value in arguments.values()):
            raise ValueError('params_file cannot be combined with inline arguments')
        file = Path(params_file)
        if not file.is_absolute():
            raise ValueError('params_file must be an absolute path')
        contents = file.read_text(encoding='utf-8')
        arguments = json.loads(contents)
        if not isinstance(arguments, dict):
            raise ValueError('params_file must contain a JSON object')
        if updates is not None:
            if active_paths[file]:
                raise ValueError('cannot update an active request')
            arguments.update(updates)
            file.write_text(json.dumps(arguments, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        if updates is not None:
            raise ValueError('updates requires params_file')
        arguments = {key: value for key, value in arguments.items()
                     if value is not None or key == 'navigation'}
    if file is not None:
        active_paths[file] += 1
    result = None
    try:
        call = dict(arguments)
        ref = call.pop('scratch_ref', None)
        with ScratchSpace().lease(ref) as directory:
            result = await _collect(scratch_dir=str(directory), **call)
        if params_file is None and result['status'] != 'validated':
            result.update(save_failed_arguments(arguments))
        return result
    finally:
        if file is not None:
            active_paths[file] -= 1
            if not active_paths[file]:
                del active_paths[file]
                if result is not None and result['status'] == 'validated':
                    try:
                        file.unlink(missing_ok=True)
                    except OSError as error:
                        result['params_delete_error'] = str(error)


@validate_call
async def _collect(
    repo: str, question: str, evidence_needed: list[EvidenceRequest], scratch_dir: str,
    navigation: dict | str | None, known_findings: str,
    paths: list[str] | None = None, include_untracked: bool = False,
    model: str | None = None, encodings: dict[str, str] | None = None,
) -> dict:
    root = Path(repo).resolve(strict=True)
    scratch = Path(scratch_dir).resolve(strict=True)
    if scratch == root or root in scratch.parents:
        raise ValueError('scratch_dir must be outside repo')
    if not question.strip() or not evidence_needed:
        raise ValueError('provide the next question and missing evidence')
    if isinstance(navigation, str):
        with ScratchSpace().lease_path(navigation) as nav:
            navigation = json.loads(nav.read_text(encoding='utf-8'))
    scope = []
    for item in evidence_needed:
        if not item['fact'].strip():
            raise ValueError('evidence fact must be nonempty')
        if paths is not None:
            if item['scope']:
                raise ValueError('reader paths cannot be combined with evidence scopes')
        else:
            candidates, unmatched = select_paths(root, item['scope'], include_untracked)
            if unmatched or not candidates:
                raise ValueError(f"No source for {item['fact']!r}: {unmatched or item['scope']}")
            scope.extend(item['scope'] or ['.'])
    scope = list(dict.fromkeys(scope))
    work = Path(tempfile.mkdtemp(prefix='explore-solve-', dir=scratch))
    run = work/'result'
    task = work/'task.txt'
    task.write_text('SOLVER QUESTION (do not solve it):\n'+question
                    +'\n\nCOLLECT THESE CODE FACTS:\n'
                    +'\n'.join('- '+item['fact']+' [scope: '+', '.join(item['scope'] or ['.'])+']' for item in evidence_needed)
                    +'\n\nALREADY KNOWN:\n'+known_findings
                    +'\nReturn the smallest source blocks establishing these facts. '
                    'Do not collect whole functions unless needed for the facts.\n')
    argv = [sys.executable, str(HERE/'locate.py'), '--repo', str(root),
            '--task-file', str(task), '--state-dir', str(work/'state'),
            '--out-dir', str(run), '--timeout', str(CONFIG['timeout_seconds'])]
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
    for path, encoding in (encodings or {}).items():
        argv.extend(['--encoding',f'{path}={encoding}'])
    if navigation is not None:
        nav = work/'navigation.json'
        nav.write_text(json.dumps(navigation, ensure_ascii=False))
        argv.extend(['--navigation-file',str(nav)])
    active_paths[work] += 1
    try:
        with (work/'runner.log').open('wb') as log:
            proc = await asyncio.create_subprocess_exec(*argv, stdout=log, stderr=log)
            try:
                await proc.wait()
            except asyncio.CancelledError:
                if proc.returncode is None:
                    proc.send_signal(signal.SIGINT)
                    await asyncio.shield(proc.wait())
                raise
    finally:
        del active_paths[work]
    if not (run/'report.json').exists():
        raise RuntimeError(f'Collection failed: {(work/"runner.log").read_text()}')
    return catalog(run)


@mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True))
def remember_navigation(navigation: dict, scratch_ref: str) -> dict:
    """Store unchanged Symbols responses once, returning a navigation ID for collect.

    Pass {root: absolute LSP workspace, queries: [{tool,args,result/error}]}.
    Does not mark source as retained in the model's context; original-source
    reads still validate hashes. scratch_ref comes from scratch.create;
    scratch.delete removes saved navigation with the other temporary outputs.
    """
    if not isinstance(navigation.get('root'), str) or not Path(navigation['root']).is_absolute() or not isinstance(navigation.get('queries'), list):
        raise ValueError('navigation requires absolute root and queries list')
    with ScratchSpace().lease(scratch_ref) as scratch, tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='navigation-', suffix='.json', dir=scratch, delete=False) as f:
        json.dump(navigation, f, ensure_ascii=False)
    return {'navigation_id':f.name}


@mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def read_evidence(run_dir: str, ids: list[int], offset: int | None = None,
                  max_chars: int = 12000) -> dict:
    """Read selected original-source IDs from collect, checking current hashes.

    Output is paginated, never silently truncated: repeat the same IDs to continue
    from the last delivered offset. Completed source ranges are reused across ID
    selections and matching Symbols readSources receipts. Source blocks preserve
    whitespace and line endings, with range labels outside the body for patching.
    Explicit offset=0 rereads after context loss. Select just the evidence needed.
    Reuse returned source; do not reread those ranges with shell tools. Increase max_chars
    if the client can display more. All evidence remains saved in run_dir.
    """
    with ScratchSpace().lease_path(run_dir):
        return read_evidence_files(run_dir, ids, offset, max_chars)


def read_evidence_files(run_dir: str, ids: list[int], offset: int | None,
                        max_chars: int) -> dict:
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
    state_path = run/'read_state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        'read': navigation_read_ranges(run, Path(metadata['repo'])), 'selections': {}}
    key = ','.join(map(str,sorted(set(ids))))
    selections = state['selections']
    new_selection = key not in selections or offset == 0
    verified = verify_handoff(Path(metadata['repo']), selected, include_source=new_selection)
    if new_selection:
        blocks = evidence_blocks(verified, [] if offset == 0 else state['read'])
        selections[key] = dict(text=format_evidence(verified, blocks, include_facts=False), offset=0, covered=0,
                               ranges=[{k:v for k,v in block.items() if k != 'source'} for block in blocks])
    selection = selections[key]
    source = selection['text']
    if offset is None:
        offset = int(selection['offset'])
    if offset > len(source):
        raise ValueError('offset exceeds the selected output')
    end = min(offset+max_chars, len(source))
    selection['offset'] = end
    if offset <= selection['covered']:
        selection['covered'] = max(selection['covered'], end)
    if selection['covered'] == len(source):
        for receipt in selection['ranges']:
            if receipt not in state['read']:
                state['read'].append(receipt)
    state_path.write_text(json.dumps(state, ensure_ascii=False))
    return dict(ids=ids, offset=offset, already_returned=offset==len(source),
                text=source[offset:end], next_offset=end if end < len(source) else None,
                total_chars=len(source), complete=end==len(source))


def navigation_read_ranges(run: Path, root: Path) -> list[dict]:
    navigation_path = run/'navigation.json'
    if not navigation_path.exists():
        return []
    navigation = json.loads(navigation_path.read_text())
    manifest = json.loads((run/'source-manifest.json').read_text())
    files = {str(root/entry['path']): entry for entry in manifest['files']}
    ranges = []
    for query in navigation['queries']:
        result = query.get('result', {})
        if result.get('isError'):
            continue
        for receipt in result.get('structuredContent', {}).get('readSources', []):
            entry = files.get(receipt['path'])
            if entry is None or receipt['contentSha256'] != entry['export_sha256']:
                continue
            start, end = receipt['range']['start'], receipt['range']['end']
            ranges.append(dict(path=entry['path'], sha256=entry['sha256'], encoding=entry['encoding'],
                               start=start['line'], end=end['line'] - (end['character'] == 1)))
    return ranges


if __name__ == '__main__':
    mcp.run(transport='stdio')
