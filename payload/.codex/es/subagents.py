# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.12,<2", "charset-normalizer>=3.4,<4"]
# ///
"""Blocking external reviews and edits through AGY, without polling handles."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
import signal
import tempfile
import time
import tomllib
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import agy_backend as agy

HERE = Path(__file__).resolve().parent
CONFIG = tomllib.loads((HERE/'agy.toml').read_text())
AGENT_DEFINITIONS = {name: (HERE/'agy_agents'/f'{name}.md').read_bytes()
                     for name in ('es-reviewer', 'es-editor')}
mcp = FastMCP('agy-subagents')
REVIEW_TOOLS = ['view_file', 'grep_search', 'finish']
EDIT_TOOLS = REVIEW_TOOLS + ['find_by_name', 'list_dir', 'run_command',
                            'write_to_file', 'replace_file_content', 'multi_replace_file_content']


async def stop(proc: asyncio.subprocess.Process) -> None:
    """Reap the AGY process group on cancellation or deadline."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), 2)
    except TimeoutError:
        os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def models() -> str:
    """List the AGY model slugs available to the authenticated account."""
    proc = await asyncio.create_subprocess_exec(CONFIG['executable'], 'models',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 60)
    except BaseException:
        await stop(proc)
        raise
    if proc.returncode:
        raise RuntimeError(stderr.decode(errors='replace'))
    return stdout.decode(errors='replace')


@mcp.tool(structured_output=False, annotations=ToolAnnotations(openWorldHint=True))
async def run(task: str, scratch_dir: str, model: str | None = None,
              repo: str | None = None, mode: Literal['review', 'edit'] = 'review') -> dict:
    """Delegate a self-contained review or an authorized edit and wait until finished.

    Call this MCP tool directly, outside code-mode cells. No poll handle is returned.
    task describes the objective, relevant facts, deliverable and (for edits) files
    to change. For Claude reviews, keep the request short and let it choose how to review.
    repo adds the real repository for reading or editing; omit it for pure reasoning.
    review exposes read/search tools only. edit also exposes file edits and commands;
    use it only for work already authorized by the user, with a repo.
    scratch_dir is an existing absolute temporary directory outside repo.
    Default model is Gemini High; use models to find other model slugs.
    Reviews starting with a model in agy.toml's review_model_order advance to
    the next model on quota or model-unavailable errors, resuming the AGY
    conversation when one exists. Edits do not retry.
    The deadline is configured in agy.toml (30 minutes), not chosen per call.
    Returns the actual model, attempt history, total usage, response and saved logs.
    Verify advice and edits.
    """
    if not task.strip():
        raise ValueError('task must be nonempty')
    scratch = Path(scratch_dir).resolve(strict=True)
    root = Path(repo).resolve(strict=True) if repo else None
    if root and (scratch == root or root in scratch.parents):
        raise ValueError('scratch_dir must be outside repo')
    if mode == 'edit' and root is None:
        raise ValueError('edit requires repo')
    out = Path(tempfile.mkdtemp(prefix='agy-subagent-', dir=scratch))
    workspace = out/'workspace'
    definitions = workspace/'.agents/agents'
    definitions.mkdir(parents=True)
    agent = 'es-reviewer' if mode == 'review' else 'es-editor'
    (definitions/f'{agent}.md').write_bytes(AGENT_DEFINITIONS[agent])
    git = await asyncio.create_subprocess_exec('git', 'init', '-q', str(workspace))
    if await git.wait():
        raise RuntimeError('could not initialize AGY runtime repository')
    selected_model: str = model or CONFIG['subagent_model']
    order = CONFIG['review_model_order']
    candidates: list[str] = order[order.index(selected_model):] if mode == 'review' and selected_model in order else [selected_model]
    if root:
        task = f'REPOSITORY: {root}\n\n{task}'
    request = json.dumps({'event': 'user', 'message': {'content': task}})+'\n'
    (out/'request.jsonl').write_text(request)
    started = time.monotonic()
    deadline = started + CONFIG['timeout_seconds']
    attempts = []
    conversation_id = None
    while True:
        candidate = candidates.pop(0)
        remaining = deadline - time.monotonic()
        attempt_dir = out/f'attempt-{len(attempts)+1}'
        attempt_dir.mkdir()
        attempt_request = attempt_dir/'request.jsonl'
        attempt_request.write_text(json.dumps({'event': 'user', 'message': {'content':
            'Continue the original task from the existing conversation. The previous model became unavailable.'
            if conversation_id else task}})+'\n')
        report = await run_attempt(candidate, mode, agent, root, workspace, attempt_request,
                                   attempt_dir, remaining, conversation_id)
        conversation_id = report['conversation_id'] or conversation_id
        attempts.append({key: report[key] for key in
                         ('model','status','error','usage','run_dir','conversation_id','resumed_from')})
        if not report['model_unavailable'] or not candidates:
            break
        if time.monotonic() >= deadline:
            report['status'] = 'timeout'
            report['error'] = 'AGY review exhausted the configured deadline'
            break
    usage: dict = report['usage']
    if len(attempts) > 1:
        # AGY terminal usage is cumulative across resumed turns of the same conversation.
        latest = {a['conversation_id'] or a['resumed_from'] or a['run_dir']: a['usage'] for a in attempts}
        usage = {key: sum(u[key] for u in latest.values())
                 if all(u[key] is not None for u in latest.values()) else None
                 for key in agy.USAGE_KEYS}
        usage.update(source='latest_conversation_results.usage', provider='antigravity_cli',
                     usage_complete=all(u['usage_complete'] for u in latest.values()))
    report.update(run_dir=str(out), attempts=attempts, usage=usage,
                  elapsed_seconds=round(time.monotonic()-started, 3))
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return {key: report[key] for key in ('status', 'response', 'error', 'usage', 'run_dir', 'model', 'attempts')}


async def run_attempt(model: str, mode: str, agent: str, root: Path | None, workspace: Path,
                      request_path: Path, out: Path, timeout: float,
                      conversation_id: str | None = None) -> dict:
    argv = agy.command(CONFIG['executable'], model, agent, None, timeout)
    if conversation_id:
        argv += ['--conversation', conversation_id]
    if mode == 'edit':
        argv[argv.index('--mode') + 1] = 'accept-edits'
    argv += ['--add-dir', str(workspace)]
    if root:
        argv += ['--add-dir', str(root)]
    state = agy.StreamState(model, agent, REVIEW_TOOLS if mode == 'review' else EDIT_TOOLS)
    started = time.monotonic()
    status = 'completed'
    with request_path.open('rb') as stdin, (out/'events.jsonl').open('wb') as stdout, \
            (out/'stderr.log').open('wb') as stderr:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=workspace,
                    stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except TimeoutError:
            status = 'timeout'
            await stop(proc)
        except asyncio.CancelledError:
            await asyncio.shield(stop(proc))
            raise
    with (out/'events.jsonl').open('rb') as stream:
        for line in stream:
            state.feed(line)
    result = state.result or {}
    response = result.get('response', '')
    error = state.error or result.get('error')
    stderr_text = (out/'stderr.log').read_text(errors='replace').strip()
    if 'print timeout' in stderr_text:
        status = 'timeout'
    if status == 'timeout':
        error = stderr_text or f'AGY exceeded the configured {timeout}s deadline'
    elif proc.returncode or result.get('status') != 'SUCCESS' or not response or error:
        status = 'failed'
        error = error or stderr_text or f'AGY exited {proc.returncode}; no successful final response'
    report = dict(status=status, response=response, error=error, usage=state.usage(),
                  run_dir=str(out), model=model, mode=mode, argv=argv,
                  conversation_id=state.conversation_id, resumed_from=conversation_id,
                  elapsed_seconds=round(time.monotonic()-started, 3), exit_code=proc.returncode)
    availability_error = result.get('error') or (stderr_text if not result else '')
    report['model_unavailable'] = bool(status == 'failed' and not state.error and re.search(
        r'quota|resource_exhausted|unknown model|model[^\n]*(?:unavailable|not available|not found|unsupported)',
        availability_error, re.IGNORECASE))
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report


if __name__ == '__main__':
    mcp.run(transport='stdio')
