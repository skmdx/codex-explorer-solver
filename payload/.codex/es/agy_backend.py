#!/usr/bin/env python3
"""Documented AGY stream-json transport. Python stdlib, Linux/macOS/WSL.

One process receives one user event and EOF. No conversation resume, API client,
permission bypass, model fallback, automatic retries, or nested model delegation.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from typing import Any
from evidence import EvidenceError, compact_json, _unique_object

REQUIRED_FLAGS = ('--input-format','--output-format','--json-schema','--model','--agent','--add-dir','--print-timeout')
USAGE_KEYS = ('input_tokens','output_tokens','thinking_tokens','cache_read_tokens','total_tokens')
LOG_LIMIT = 16 * 1024 * 1024
LINE_LIMIT = 4 * 1024 * 1024


def strict_json(raw: str | bytes) -> Any:
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError('non-finite JSON')))


def preflight(executable: str, model: str) -> dict:
    """No prompt is sent. Check installed CLI flags and the exact model listing."""
    def get(args: list[str]) -> str:
        result = subprocess.run([executable, *args], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise EvidenceError(f'agy {" ".join(args)} failed; authenticate/check installation; no fallback: '
                                + (result.stderr or result.stdout).strip()[:500])
        text = result.stdout + result.stderr
        if len(text) > 1024*1024: raise EvidenceError('unexpectedly large agy preflight response')
        return text
    help_text = get(['--help'])
    missing = [flag for flag in REQUIRED_FLAGS if flag not in help_text]
    if missing:
        raise EvidenceError('agy is missing required flags: ' + ', '.join(missing))
    version = get(['--version']).strip()[:200]
    listing = get(['models'])
    # No substring match: 3.8-flash-high is not a match for 3.8-flash-medium.
    slugs = set(re.findall(r'(?<![A-Za-z0-9_.-])gemini-[A-Za-z0-9_.-]+', listing))
    if model not in slugs:
        raise EvidenceError(f'requested model {model} not listed by agy models; no replacement selected')
    return {'agy_version': version, 'model_available': model, 'required_flags_present': True}


def agent_definition(path: Path, expected_name: str, expected_tools: list[str]) -> str:
    """Validate this kit's deliberately simple JSON-compatible YAML frontmatter.

    Native tool schemas are owned by AGY. The init event is independently checked.
    This parser intentionally rejects custom permission expansion rather than
    pretending that arbitrary frontmatter was safely audited.
    """
    if path.is_symlink(): raise EvidenceError('agent template must not be a symlink')
    text = path.read_text(encoding='utf-8')
    if len(text.encode()) > 32768: raise EvidenceError('agent definition too large')
    parts = text.split('---\n', 2)
    if len(parts) != 3 or parts[0]: raise EvidenceError('invalid agent frontmatter')
    config = {}
    for line in parts[1].splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key, sep, val = line.partition(':')
        if not sep or key in config: raise EvidenceError('invalid/duplicate agent key')
        config[key] = strict_json(val.strip())
    required = {'name','description','tools','mainAgent','subagent','model',
                'commandExecutionPolicy','mcpServers','skills','plugins'}
    if set(config) != required or config['name'] != expected_name:
        raise EvidenceError('unrecognized agent definition')
    if config['tools'] != expected_tools or config['mainAgent'] is not True \
            or config['subagent'] is not False or config['model'] != 'inherit' \
            or config['commandExecutionPolicy'] != 'off' \
            or any(config[k] != [] for k in ('mcpServers','skills','plugins')):
        raise EvidenceError('agent capability expansion refused; use the bounded worker definitions')
    if not isinstance(config['description'], str) or not parts[2].strip():
        raise EvidenceError('agent prompt or description is empty')
    return text


def command(executable: str, model: str, agent: str, schema: Path, timeout: float) -> list[str]:
    if not math.isfinite(timeout) or timeout <= 0: raise EvidenceError('invalid timeout')
    return [executable, '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--model', model, '--agent', agent, '--json-schema', str(schema),
            '--print-timeout', f'{max(1, math.ceil(timeout))}s']


class StreamState:
    def __init__(self, model: str, agent: str, tools: list[str], tool_limit: int):
        self.model = model; self.agent = agent; self.required_tools = set(tools)
        self.allowed_tools = self.required_tools | {'ask_permission'}
        self.tool_limit = tool_limit; self.init = None; self.result = None
        self.step_ids: set[int] = set(); self.unknown_events = 0
        self.conversation_id = None; self.permission_mode = None
        self.error: str | None = None

    def feed(self, raw: bytes) -> None:
        if not raw.strip(): return
        if self.error: return
        try:
            if len(raw) > LINE_LIMIT: raise EvidenceError('AGY event line exceeds size limit')
            event = strict_json(raw)
            if not isinstance(event, dict): raise EvidenceError('event is not an object')
            kind = event.get('event')
            if self.result is not None: raise EvidenceError('unexpected event after terminal result')
            if kind == 'init':
                if self.init is not None: raise EvidenceError('duplicate init event')
                data = event.get('init')
                if not isinstance(data, dict): raise EvidenceError('missing init payload')
                if data.get('model') != self.model: raise EvidenceError('reported model differs from pinned model')
                if data.get('agent') != self.agent: raise EvidenceError('requested custom agent not active')
                tools = data.get('tools')
                if not isinstance(tools, list) or any(not isinstance(x, str) for x in tools):
                    raise EvidenceError('init tool set is not reported')
                if not set(tools) <= self.allowed_tools or not self.required_tools <= set(tools):
                    raise EvidenceError('unexpected tool exposure in agy init; refusing unrestricted worker')
                self.permission_mode = data.get('permission_mode')
                if self.permission_mode == 'always-proceed':
                    raise EvidenceError('always-proceed permissions are not allowed by this worker')
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
                    if len(self.step_ids) > self.tool_limit: raise EvidenceError('observed tool-call limit exceeded')
            elif kind == 'result':
                data = event.get('result')
                if not isinstance(data, dict): raise EvidenceError('missing terminal result')
                # Startup/auth/model errors may emit a result without an init.
                if data.get('status') == 'SUCCESS' and self.init is None:
                    raise EvidenceError('success before checked init')
                if data.get('status') == 'SUCCESS' and (type(data.get('num_turns')) is not int or data['num_turns'] != 1):
                    raise EvidenceError('expected exactly one user turn, not a resumed session')
                cid = data.get('conversation_id')
                if cid and self.conversation_id and cid != self.conversation_id:
                    raise EvidenceError('result conversation mismatch')
                self.result = data
            else:
                self.unknown_events += 1
                raise EvidenceError('unsupported AGY event; update adapter instead of guessing')
        except (ValueError, UnicodeError, RecursionError) as exc:
            self.error = str(exc)

    def usage(self) -> dict:
        raw = self.result.get('usage') if self.result else None
        counts = {k: raw.get(k) if isinstance(raw, dict) and type(raw.get(k)) is int and raw[k] >= 0 else None
                  for k in USAGE_KEYS}
        # Read ONE terminal cumulative usage. Never add step totals, cache, or thinking.
        counts['source'] = 'single_terminal_result.usage'
        counts['provider'] = 'antigravity_cli'
        counts['usage_complete'] = bool(not self.error and self.result is not None
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


def supervise(argv: list[str], workspace: Path, out: Path, timeout: float,
              state: StreamState, log_limit: int = LOG_LIMIT) -> tuple[int,str|None,float]:
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
                    if len(pending) > LINE_LIMIT:
                        state.error = 'oversize unfinished event line'; break
            try:
                while proc.poll() is None:
                    pump()
                    if state.error:
                        reason = 'protocol_or_capability_error'; stop_process_group(proc); break
                    if time.monotonic()-start >= timeout:
                        reason = 'local_deadline'; stop_process_group(proc); break
                    if os.fstat(events.fileno()).st_size + os.fstat(errors.fileno()).st_size > log_limit:
                        reason = 'log_budget'; stop_process_group(proc); break
                    time.sleep(0.05)
                code = proc.wait(timeout=3)
                observed = os.fstat(events.fileno()).st_size + os.fstat(errors.fileno()).st_size
                if observed > log_limit:
                    reason = reason or 'log_budget'
                else:
                    pump()
                    if pending.strip(): state.feed(pending)
                if state.error: reason = reason or 'protocol_or_capability_error'
            except BaseException:
                stop_process_group(proc); raise
    return code, reason, time.monotonic()-start
