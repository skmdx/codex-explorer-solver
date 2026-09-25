#!/usr/bin/env python3
"""Documented AGY stream-json transport. Python stdlib, Linux/macOS/WSL.

One process receives one user event and EOF. Callers own conversation continuation
and model selection. This transport does not retry or delegate work.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any
from evidence import EvidenceError, compact_json, _unique_object

USAGE_KEYS = ('input_tokens','output_tokens','thinking_tokens','cache_read_tokens','total_tokens')


def strict_json(raw: str | bytes) -> Any:
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError('non-finite JSON')))


def version(executable: str) -> str:
    result = subprocess.run([executable, '--version'], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def command(executable: str, model: str, agent: str, schema: Path | None, timeout: float | None) -> list[str]:
    argv = [executable, '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--model', model, '--agent', agent,
            '--mode', 'plan', '--dangerously-skip-permissions',
            ]
    if schema is not None:
        argv += ['--json-schema', str(schema)]
    if timeout is not None:
        argv += ['--print-timeout', f'{math.ceil(timeout)}s']
    return argv


class StreamState:
    def __init__(self, model: str, agent: str, tools: list[str]):
        self.model = model; self.agent = agent
        self.allowed_tools = set(tools) | {'ask_permission','manage_task'}
        self.init = None; self.result = None
        self.step_ids: set[int] = set(); self.unknown_events = 0
        self.conversation_id = None; self.permission_mode = None
        self.error: str | None = None

    def feed(self, raw: bytes) -> None:
        if not raw.strip(): return
        if self.error: return
        try:
            event = strict_json(raw)
            if not isinstance(event, dict): raise EvidenceError('event is not an object')
            kind = event.get('event')
            if kind not in {'init','step_update','result'}:
                self.unknown_events += 1
                return
            if self.result is not None: raise EvidenceError('unexpected event after terminal result')
            if kind == 'init':
                if self.init is not None: raise EvidenceError('duplicate init event')
                data = event.get('init')
                if not isinstance(data, dict): raise EvidenceError('missing init payload')
                if data.get('model') != self.model: raise EvidenceError('reported model differs from pinned model')
                if data.get('agent') != self.agent: raise EvidenceError('requested custom agent not active')
                self.permission_mode = data.get('permission_mode')
                self.init = data; self.conversation_id = event.get('conversation_id')
            elif kind == 'step_update':
                if self.init is None: raise EvidenceError('step before checked init')
                step = event.get('step_update')
                if not isinstance(step, dict): raise EvidenceError('invalid step_update')
                if step.get('subagent_info'): raise EvidenceError('nested delegation is not permitted')
                cid = step.get('conversation_id')
                if cid and self.conversation_id and cid != self.conversation_id:
                    raise EvidenceError('unexpected second conversation')
                if step.get('step_type') == 'tool':
                    tool = step.get('tool_name')
                    if tool not in self.allowed_tools: raise EvidenceError('disallowed tool step')
                    index = step.get('step_index')
                    if type(index) is not int or index < 0: raise EvidenceError('invalid tool step index')
                    self.step_ids.add(index)
            elif kind == 'result':
                data = event.get('result')
                if not isinstance(data, dict): raise EvidenceError('missing terminal result')
                cid = data.get('conversation_id')
                if cid and self.conversation_id and cid != self.conversation_id:
                    raise EvidenceError('result conversation mismatch')
                # Retain provider usage even when the answer/turn contract fails.
                self.result = data
                # Startup/auth/model errors may emit a result without an init.
                if data.get('status') == 'SUCCESS' and self.init is None:
                    raise EvidenceError('success before checked init')
                # The CLI can add continuation turns to a single stdin request.
                # Resume is controlled by argv, not inferred from this counter.
                if data.get('status') == 'SUCCESS' and (type(data.get('num_turns')) is not int or data['num_turns'] < 1):
                    raise EvidenceError('invalid provider turn count')
        except (ValueError, UnicodeError, RecursionError) as exc:
            self.error = str(exc)

    def usage(self) -> dict:
        raw = self.result.get('usage') if self.result else None
        counts = {k: raw.get(k) if isinstance(raw, dict) and type(raw.get(k)) is int and raw[k] >= 0 else None
                  for k in USAGE_KEYS}
        # Read ONE terminal cumulative usage. Never add step totals, cache, or thinking.
        counts['source'] = 'single_terminal_result.usage'
        counts['provider'] = 'antigravity_cli'
        counts['usage_complete'] = bool(self.result is not None
                and all(counts[k] is not None for k in ('input_tokens','output_tokens','total_tokens')))
        return counts


def stop_process_group(proc: subprocess.Popen) -> None:
    try: os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError: return
    try: proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait(timeout=2)


def supervise(argv: list[str], workspace: Path, out: Path, timeout: float | None,
              state: StreamState) -> tuple[int,str|None,float]:
    """Stream monitor; termination is local/best effort, NOT remote spend cancellation.

    init checks are diagnostics/backstops, not a security boundary before the model
    starts. Restricted native agent tools are the primary capability control.
    """
    start = time.monotonic(); reason = None; pending = b''
    env = os.environ.copy(); env['PYTHONDONTWRITEBYTECODE'] = '1'
    with (out/'request.jsonl').open('rb') as request, (out/'events.jsonl').open('xb') as events, \
            (out/'stderr.log').open('xb') as errors:
        os.chmod(out/'events.jsonl',0o600); os.chmod(out/'stderr.log',0o600)
        proc = subprocess.Popen(argv, cwd=workspace, stdin=request, stdout=events,
                                stderr=errors, env=env, start_new_session=True)
        with (out/'events.jsonl').open('rb') as watch:
            def pump():
                nonlocal pending
                while True:
                    chunk = watch.read(65536)
                    if not chunk: break
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n',1); state.feed(line)
            try:
                while proc.poll() is None:
                    pump()
                    if state.error:
                        reason = 'protocol_or_capability_error'; stop_process_group(proc); break
                    if timeout is not None and time.monotonic()-start >= timeout:
                        reason = 'local_deadline'; stop_process_group(proc); break
                    time.sleep(0.05)
                code = proc.wait(timeout=3)
                pump()
                if pending.strip(): state.feed(pending)
                if state.error: reason = reason or 'protocol_or_capability_error'
            except BaseException:
                stop_process_group(proc); raise
    return code, reason, time.monotonic()-start
