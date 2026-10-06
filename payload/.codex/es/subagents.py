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
from word_ids import create_directory
import time
import tomllib
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

import agy_backend as agy
from scratch_space import ScratchSpace

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
    """List AGY model slugs when a review needs a preferred model. Editing uses the configured Claude model."""
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


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: str = Field(min_length=1, description='Target scope, question or change, and expected result.')
    scratch_ref: str


class ReviewRequest(TaskRequest):
    """Read/search only. Omit repo for reasoning from supplied facts."""
    mode: Literal['review'] = 'review'
    repo: str | None = None
    preferred_model: str | None = Field(default=None, description='Normally omit. Starting preference, not a fixed model; automatic fallback may select another model.')


class EditRequest(TaskRequest):
    """Authorized edits and commands in repo. Uses configured Claude only; no model override or fallback."""
    mode: Literal['edit']
    repo: str = Field(min_length=1)


REQUEST = TypeAdapter(ReviewRequest | EditRequest)


@mcp.tool(structured_output=False, annotations=ToolAnnotations(openWorldHint=True))
async def run(request: ReviewRequest | EditRequest) -> dict:
    """Delegate a review or authorized edit and wait for the final result.

    Call directly, outside Code Mode. scratch_ref comes from scratch.create;
    its directory must be outside repo. Delete it after using the saved results.
    Review may switch models on availability failures. Edit never switches models;
    on failure, inspect any partial changes and continue in the parent agent.
    Check advice and changed behavior before accepting the result. Report the
    actual model and unresolved problems; attempt logs are available for diagnosis.
    """
    parsed = REQUEST.validate_python(request)
    with ScratchSpace().lease(parsed.scratch_ref) as scratch:
        return await _run(parsed.task, scratch,
                          parsed.preferred_model if isinstance(parsed, ReviewRequest) else None,
                          parsed.repo, parsed.mode)


async def _run(task: str, scratch: Path, model: str | None,
               repo: str | None, mode: Literal['review', 'edit']) -> dict:
    if not task.strip():
        raise ValueError('task must be nonempty')
    root = Path(repo).resolve(strict=True) if repo else None
    if root and (scratch == root or root in scratch.parents):
        raise ValueError('scratch directory must be outside repo')
    if mode == 'edit' and root is None:
        raise ValueError('edit requires repo')
    if mode == 'edit' and not CONFIG['edit_model'].startswith('claude-'):
        raise ValueError('edit_model must be a Claude model; Gemini editing is disabled')
    out = create_directory(scratch, prefix='agy-subagent-')
    workspace = out/'workspace'
    definitions = workspace/'.agents/agents'
    definitions.mkdir(parents=True)
    agent = 'es-reviewer' if mode == 'review' else 'es-editor'
    (definitions/f'{agent}.md').write_bytes(AGENT_DEFINITIONS[agent])
    git = await asyncio.create_subprocess_exec('git', 'init', '-q', str(workspace))
    if await git.wait():
        raise RuntimeError('could not initialize AGY runtime repository')
    if mode == 'edit':
        selected_model = CONFIG['edit_model']
        candidates = [selected_model]
    else:
        order = CONFIG['review_model_order']
        selected_model = model or order[0]
        candidates = order[order.index(selected_model):] if selected_model in order else [selected_model, *order]
    candidates, skipped_models = agy.available_models(candidates)
    if root:
        task = f'REPOSITORY: {root}\n\n{task}'
    request = json.dumps({'event': 'user', 'message': {'content': task}})+'\n'
    (out/'request.jsonl').write_text(request)
    started = time.monotonic()
    deadline = started + CONFIG['timeout_seconds']
    attempts = []
    conversation_id = None
    previous_error = None
    report: dict = dict(status='failed',response='',error='; '.join(x['model']+': '+x['error'] for x in skipped_models),
                  usage=None,model=selected_model)
    while candidates:
        candidate = candidates.pop(0)
        remaining = deadline - time.monotonic()
        attempt_dir = out/f'attempt-{len(attempts)+1}'
        attempt_dir.mkdir()
        attempt_request = attempt_dir/'request.jsonl'
        attempt_request.write_text(json.dumps({'event': 'user', 'message': {'content':
            'Continue the original task from the existing conversation.'
            if conversation_id else task}})+'\n')
        report = await run_attempt(candidate, mode, agent, root, workspace, attempt_request,
                                   attempt_dir, remaining, conversation_id,
                                   previous_error if conversation_id else None)
        conversation_id = report['conversation_id'] or conversation_id
        if report['conversation_id']:
            previous_error = report['terminal_error']
        attempts.append({key: report[key] for key in
                         ('model','status','error','usage','run_dir','conversation_id','resumed_from','elapsed_seconds')})
        if not report['model_unavailable'] or not candidates:
            break
        if time.monotonic() >= deadline:
            report['status'] = 'timeout'
            report['error'] = 'AGY subagent exhausted the configured deadline'
            break
    usage: dict | None = report['usage']
    if len(attempts) > 1:
        # AGY terminal usage is cumulative across resumed turns of the same conversation.
        latest = {a['conversation_id'] or a['resumed_from'] or a['run_dir']: a['usage'] for a in attempts}
        usage = {key: sum(u[key] for u in latest.values())
                 if all(u[key] is not None for u in latest.values()) else None
                 for key in agy.USAGE_KEYS}
        usage.update(source='latest_conversation_results.usage', provider='antigravity_cli',
                     usage_complete=all(u['usage_complete'] for u in latest.values()))
    report.update(run_dir=str(out), attempts=attempts, usage=usage, skipped_models=skipped_models,
                  elapsed_seconds=round(time.monotonic()-started, 3))
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return {key: report[key] for key in ('status', 'response', 'error', 'usage', 'run_dir', 'model', 'attempts', 'skipped_models')}


async def run_attempt(model: str, mode: str, agent: str, root: Path | None, workspace: Path,
                      request_path: Path, out: Path, timeout: float,
                      conversation_id: str | None = None, previous_error: str | None = None) -> dict:
    argv = agy.command(CONFIG['executable'], model, agent, None, timeout,
                       conversation_id=conversation_id, mode='accept-edits' if mode == 'edit' else 'plan')
    argv += ['--add-dir', str(workspace)]
    if root:
        argv += ['--add-dir', str(root)]
    state = agy.StreamState(model, agent, REVIEW_TOOLS if mode == 'review' else EDIT_TOOLS)
    quota_log = agy.QuotaLog(out/'native.log')
    argv += ['--log-file',str(quota_log.path)]
    started = time.monotonic()
    status = 'completed'
    with request_path.open('rb') as stdin, (out/'events.jsonl').open('wb') as stdout, \
            (out/'stderr.log').open('wb') as stderr:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=workspace,
                    stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True)
        waiter = asyncio.create_task(proc.wait())
        try:
            async with asyncio.timeout(timeout):
                while not waiter.done():
                    done,_ = await asyncio.wait({waiter},timeout=0.1)
                    if done: break
                    state.quota_error = quota_log.read()
                    if state.quota_error:
                        await stop(proc)
                        break
        except TimeoutError:
            status = 'timeout'
            await stop(proc)
        except asyncio.CancelledError:
            await asyncio.shield(stop(proc))
            raise
        finally:
            await waiter
    with (out/'events.jsonl').open('rb') as stream:
        for line in stream:
            state.feed(line)
    state.recover_inherited_error(previous_error, proc.returncode)
    result = state.result or {}
    response = result.get('response', '')
    error = state.error or state.quota_error or result.get('error')
    agy.remember_quota(model,state.quota_error or result.get('error') or '')
    stderr_text = (out/'stderr.log').read_text(errors='replace').strip()
    if 'print timeout' in stderr_text:
        status = 'timeout'
    if status == 'timeout':
        error = stderr_text or f'AGY exceeded the configured {timeout}s deadline'
    elif proc.returncode or result.get('status') != 'SUCCESS' or not response or error:
        status = 'failed'
        error = error or stderr_text or f'AGY exited {proc.returncode}; no successful final response'
    report = dict(status=status, response=response, error=error, usage=state.usage(),
                  terminal_error=result.get('error'),
                  run_dir=str(out), model=model, mode=mode, argv=argv,
                  conversation_id=state.conversation_id, resumed_from=conversation_id,
                  elapsed_seconds=round(time.monotonic()-started, 3), exit_code=proc.returncode)
    availability_error = state.quota_error or result.get('error') or (stderr_text if not result else '')
    report['model_unavailable'] = bool(status == 'failed' and not state.error and re.search(
        r'quota|resource_exhausted|unknown model|model[^\n]*(?:unavailable|not available|not found|unsupported|not recognized)',
        availability_error, re.IGNORECASE))
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report


if __name__ == '__main__':
    mcp.run(transport='stdio')
