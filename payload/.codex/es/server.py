# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.12,<2", "charset-normalizer>=3.4,<4"]
# ///
"""Blocking AGY collection and selective, paginated original-source retrieval."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import tomllib
from typing import Annotated, Any, Literal
from typing_extensions import TypedDict

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, validate_call

from evidence import EvidenceError, evidence_blocks, format_evidence, load_handoff, verify_location
from agy_snapshot import select_paths
from scratch_space import ScratchSpace
from reports import read_report
from word_ids import create_directory, store_reference

HERE = Path(__file__).resolve().parent
CONFIG = tomllib.loads((HERE/'agy.toml').read_text())
active_paths: Counter[Path] = Counter()


def navigation_database():
    db = sqlite3.connect(ScratchSpace().state / 'navigation.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS refs (id TEXT PRIMARY KEY, payload TEXT NOT NULL UNIQUE)')
    return db

def save_failed_arguments(arguments: dict[str, Any]) -> dict:
    """Preserve an inline request without hiding its original failure."""
    try:
        contents = json.dumps(arguments, ensure_ascii=False, indent=2) + '\n'
        with ScratchSpace().lease(arguments['scratch_ref']) as scratch, tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         prefix='collect-failed-', suffix='.json',
                                         dir=scratch, delete=False) as file:
            file.write(contents)
        return {'params_file': file.name}
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {'params_save_error': f'Could not save arguments in scratch_ref: {error}'}


class CollectionMCP(FastMCP):
    async def call_tool(self, name: str, arguments: dict[str, Any]):
        try:
            return await super().call_tool(name, arguments)
        except Exception as error:
            request = arguments.get('request', {})
            if name != 'collect' or not isinstance(request, dict) or request.get('params_file') is not None:
                raise
            saved = save_failed_arguments(request)
            raise ToolError(f'{error}\n{json.dumps(saved, ensure_ascii=False)}') from error


mcp = CollectionMCP("explore-solve")
mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))(read_report)


class EvidenceRequest(TypedDict):
    fact: str
    scope: list[str]


class NavigationResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: str
    result: dict[str, Any]


class NewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repo: str
    question: Annotated[str, Field(min_length=1)]
    evidence_needed: Annotated[list[EvidenceRequest], Field(min_length=1)]
    scratch_ref: str
    known_findings: str = ''
    model: str | None = None
    encodings: dict[str, str] | None = None


class RepositoryRequest(NewRequest):
    """Search each fact's repository-relative scopes in tracked Git files."""
    source: Literal['repository'] = 'repository'
    include_untracked: bool = Field(default=False, description='Add nonignored untracked files for every scope.')
    navigation: list[NavigationResult] | str | None = Field(default=None, description='Unchanged Symbols responses, or a prior navigation_id.')


class FileRequest(NewRequest):
    """Read explicit files in each fact's scope; accepts ignored and external files."""
    source: Literal['files']


class RetryRequest(BaseModel):
    """Load an inner request JSON saved under a Scratch directory."""
    model_config = ConfigDict(extra='forbid')
    params_file: str
    updates: dict[str, Any] | None = Field(default=None, description='Replace only these top-level request fields before retrying.')


NEW_REQUEST = TypeAdapter(RepositoryRequest | FileRequest)
REQUEST = TypeAdapter(RepositoryRequest | FileRequest | RetryRequest)


def catalog(run: Path) -> dict:
    report = json.loads((run/'report.json').read_text())
    result = {k: report[k] for k in ('status', 'handoff_status')}
    if report.get('error'):
        result['error'] = str(report['error'])[:1000]
        if len(str(report['error'])) > 1000:
            result['error_truncated'] = True
    result['effective_model'] = report.get('effective_model')
    result['run_dir'] = str(run)
    evidence = report.get('evidence')
    result.update(location_page(evidence, 0, 40))
    if (report.get('scope') or {}).get('unmatched_scopes'):
        result.update(summary_strings('unmatched_scopes', report['scope']['unmatched_scopes']))
    if evidence and evidence['unresolved']:
        result.update(summary_strings('unresolved', evidence['unresolved']))
    return result


def summary_strings(key: str, values: list[str]) -> dict:
    selected = []
    used = 0
    for value in values:
        display = value[:1000]
        size = len(json.dumps(display, ensure_ascii=False))
        if used + size > 2000:
            break
        selected.append(display)
        used += size
    result: dict[str, Any] = {key: selected}
    if selected != values:
        result.update({key + '_count': len(values), key + '_truncated': True})
    return result


def location_page(evidence: dict | None, offset: int, limit: int) -> dict:
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError('offset must be nonnegative and limit 1..100')
    entries = [(category, item) for category in ('primary', 'related')
               for item in (evidence or {}).get(category, [])]
    locations, used = [], 0
    for index, (category, item) in enumerate(entries[offset:offset+limit], offset):
        location: dict[str, Any] = dict(id=index+1, role=category,
                        **{k:item[k] for k in ('path','start','end','symbol','evidence')})
        truncated = [key for key in ('symbol', 'evidence') if len(location[key]) > 1000]
        if truncated:
            for key in truncated:
                location[key] = location[key][:1000]
            location['truncated_fields'] = truncated
        size = len(json.dumps(location, ensure_ascii=False))
        if locations and used + size > 6000:
            break
        locations.append(location)
        used += size
    result: dict = {'locations': locations}
    end = offset + len(locations)
    if end < len(entries):
        result.update(next_offset=end, total_locations=len(entries))
    return result


@mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def list_evidence(run_dir: str, offset: int = 0, limit: int = 40) -> dict:
    """Continue the collection index using next_offset. IDs stay stable for read_evidence.

    Omitted next_offset means the index is complete. Original sources are read and
    hash-checked by read_evidence. Full descriptions and diagnostics remain in report.json.
    """
    with ScratchSpace().lease_path(run_dir):
        report = json.loads((Path(run_dir)/'report.json').read_text())
        return location_page(report.get('evidence'), offset, limit)


@mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def collect(request: RepositoryRequest | FileRequest | RetryRequest) -> dict:
    """Collect missing source facts and return an index for read_evidence.

    Use repository sources for scoped code searches, files for explicit inputs,
    or a returned params_file with updates to retry. Calls wait for completion.
    Inline navigation is saved automatically; reuse the returned navigation_id.
    Read selected IDs with read_evidence. If next_offset is present, list_evidence
    returns more locations. Usage and attempt details are saved in run_dir/report.json.
    """
    parsed = REQUEST.validate_python(request)
    if isinstance(parsed, RetryRequest):
        file = Path(parsed.params_file)
        if not file.is_absolute():
            raise ValueError('params_file must be an absolute path')
        with ScratchSpace().lease_path(file):
            arguments = json.loads(file.read_text(encoding='utf-8'))
            if not isinstance(arguments, dict):
                raise ValueError('params_file must contain a JSON object')
            if parsed.updates:
                if active_paths[file]:
                    raise ValueError('cannot update an active request')
                arguments.update(parsed.updates)
                file.write_text(json.dumps(arguments, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            selected = NEW_REQUEST.validate_python(arguments)
            active_paths[file] += 1
            result = None
            try:
                result = await collect_request(selected)
                return result
            finally:
                active_paths[file] -= 1
                if not active_paths[file]:
                    del active_paths[file]
                    if result is not None and result['status'] == 'validated':
                        try:
                            file.unlink(missing_ok=True)
                        except OSError as error:
                            result['params_delete_error'] = str(error)
    result = await collect_request(parsed)
    if result['status'] != 'validated':
        result.update(save_failed_arguments(parsed.model_dump()))
    return result


async def collect_request(request: RepositoryRequest | FileRequest) -> dict:
    arguments = request.model_dump()
    source = arguments.pop('source')
    ref = arguments.pop('scratch_ref')
    with ScratchSpace().lease(ref) as directory:
        return await _collect(scratch_dir=str(directory), source=source, **arguments)


@validate_call
async def _collect(
    repo: str, question: str, evidence_needed: list[EvidenceRequest], scratch_dir: str,
    source: Literal['repository', 'files'], known_findings: str = '',
    navigation: list[NavigationResult] | str | None = None, include_untracked: bool = False,
    model: str | None = None, encodings: dict[str, str] | None = None,
) -> dict:
    root = Path(repo).resolve(strict=True)
    scratch = Path(scratch_dir).resolve(strict=True)
    if scratch == root or root in scratch.parents:
        raise ValueError('scratch_dir must be outside repo')
    if not question.strip() or not evidence_needed:
        raise ValueError('provide the next question and missing evidence')
    navigation_id = navigation if isinstance(navigation, str) else None
    navigation_data = None
    if isinstance(navigation, str):
        with closing(navigation_database()) as db:
            row = db.execute('SELECT payload FROM refs WHERE id=?', (navigation,)).fetchone()
        if row is None:
            raise ValueError('unknown navigation ID; pass Symbols results again')
        with ScratchSpace().lease_path(row[0]) as nav:
            navigation_data = json.loads(nav.read_text(encoding='utf-8'))
    elif navigation is not None:
        navigation_data = {'root': str(root), 'queries': [item.model_dump() for item in navigation]}
    scope = []
    for item in evidence_needed:
        if not item['fact'].strip():
            raise ValueError('evidence fact must be nonempty')
        if source == 'files':
            if not item['scope'] or any(not (root / path).is_file() for path in item['scope']):
                raise ValueError(f"No source for {item['fact']!r}: provide existing files in scope")
        else:
            candidates, unmatched = select_paths(root, item['scope'], include_untracked)
            if unmatched or not candidates:
                raise ValueError(f"No source for {item['fact']!r}: {unmatched or item['scope']}")
        scope.extend(item['scope'] or ['.'])
    scope = list(dict.fromkeys(scope))
    if navigation_data is not None and navigation_id is None:
        navigation_id = save_navigation(navigation_data, scratch)
    work = create_directory(scratch, prefix='explore-solve-')
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
    if source == 'files':
        argv.extend(['--mode','reader'])
    for path in scope:
        argv.extend(['--path' if source == 'files' else '--scope', path])
    if include_untracked:
        argv.append('--include-untracked')
    if model is not None:
        argv.extend(['--model',model])
    for path, encoding in (encodings or {}).items():
        argv.extend(['--encoding',f'{path}={encoding}'])
    if navigation_data is not None:
        nav = work/'navigation.json'
        nav.write_text(json.dumps(navigation_data, ensure_ascii=False))
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
        log_path = work/'runner.log'
        with log_path.open('rb') as log:
            log.seek(0, 2)
            size = log.tell()
            log.seek(max(0, size-4000))
            tail = log.read().decode('utf-8', errors='replace')
        raise RuntimeError(json.dumps(dict(error='Collection failed before producing report.json',
            log_path=str(log_path), log_tail=tail, log_truncated=size>4000), ensure_ascii=False))
    result = catalog(run)
    if navigation_id is not None:
        result['navigation_id'] = navigation_id
    return result


def save_navigation(navigation: dict, scratch: Path) -> str:
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='navigation-', suffix='.json', dir=scratch, delete=False) as file:
        json.dump(navigation, file, ensure_ascii=False)
        with closing(navigation_database()) as db:
            return store_reference(db, 'refs', file.name)


@mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def read_evidence(run_dir: str, ids: list[int], reread: bool = False,
                  max_chars: int = 12000, offset: int = 0,
                  reuse_ids: list[int] | None = None) -> dict:
    """Read selected original-source IDs from collect, checking current hashes.

    Continue with returned ids and next_offset as offset, keeping other arguments.
    Null next_offset means complete. Repeating a request replays the page; no read
    position is stored. Independent failures appear in errors beside valid source.
    If a source changes during paging, offset resets to zero with restarted=true.
    reuse_ids omits ranges whose full source you already retain; Symbols receipts
    from collection are also reused. Set reread=true to ignore both after context
    loss. Source whitespace and line endings are preserved for patching.
    """
    with ScratchSpace().lease_path(run_dir):
        return read_evidence_files(run_dir, ids, reread, max_chars, offset, reuse_ids)


def read_evidence_files(run_dir: str, ids: list[int], reread: bool,
                        max_chars: int, offset: int = 0,
                        reuse_ids: list[int] | None = None) -> dict:
    if max_chars < 1 or offset < 0 or not ids:
        raise ValueError('provide IDs, positive max_chars and nonnegative offset')
    run = Path(run_dir)
    handoff = load_handoff((run/'handoff.json').read_bytes())
    metadata = json.loads((run/'metrics.json').read_text())
    entries = [(category,item) for category in ('primary','related') for item in handoff[category]]
    selected = dict(handoff, primary=[], related=[])
    valid_ids, errors = [], []
    sources = {}
    for i in sorted(set(ids)):
        if i < 1 or i > len(entries):
            errors.append(dict(id=i, error=f'unknown evidence ID {i}'))
            continue
        category,item = entries[i-1]
        try:
            verified = verify_location(Path(metadata['repo']), item, sources, include_source=True)
        except (EvidenceError, OSError) as error:
            errors.append(dict(id=i, error=str(error)))
            continue
        selected[category].append(verified)
        valid_ids.append(i)
    read_ranges = []
    if not reread:
        read_ranges = navigation_read_ranges(run, Path(metadata['repo']))
        for i in sorted(set(reuse_ids or [])):
            if i < 1 or i > len(entries):
                raise ValueError(f'unknown reuse ID {i}')
            read_ranges.append(entries[i-1][1])
    blocks = evidence_blocks(selected, read_ranges)
    source = format_evidence(selected, blocks, include_facts=False)
    restarted = bool(offset and errors)
    if restarted:
        offset = 0
    if offset > len(source):
        raise ValueError('offset must be within the text; keep the same selection and reuse arguments')
    end = min(offset+max_chars, len(source))
    result = dict(ids=valid_ids, text=source[offset:end], offset=offset,
                  total_chars=len(source), next_offset=end if end < len(source) else None)
    if errors:
        result['errors'] = errors
    if restarted:
        result['restarted'] = True
    return result


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
