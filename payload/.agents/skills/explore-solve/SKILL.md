---
name: explore-solve
description: Explicit token-aware Codex solver with deterministic lookup, AGY/Gemini 3.8 Flash evidence workers, and exact-source verification.
---

You are the Codex parent solver. Own requirements, implementation, and verification.
Respect project instructions and preserve user changes. Do not read this kit's
source/long docs unless debugging it. Do not spawn Codex subagents, invoke codex
exec workers, or send tasks to another model/provider. All kit delegation uses
`/home/user/codex-work/.codex/es/locate.py`, which calls Antigravity CLI (agy), Gemini 3.8 Flash.
Do not call agy directly to bypass validation, admission control, or model pinning.

This workspace installation is shared across projects. Set `ES=/home/user/codex-work/.codex/es`
for the commands below. Run them at the target Git repository root, not the
multi-project workspace root. Create temporary task/state/output directories under
`/home/user/codex-work/tmp`; remove them after the task and evidence collection.

## Route before reading

- Known edit location: directly read necessary original source and implement.
  Necessary security/concurrency/compatibility reasoning remains with you.
- Known file and literal anchor: use
  `python3 "$ES/gateway.py" search --root . --path FILE --literal TEXT`.
  Follow pagination only with the same source snapshot SHA. A scoped no-match is
  not proof of repository-wide absence. Do not spawn a worker for a trivial lookup.
- Known files and a small factual question: read the relevant source directly.
  Use one AGY reader only when the source volume warrants delegation: `--mode reader`,
  explicit repeated `--path`. The script bundles source; do not paste whole files
  into your context before sending them to the worker.
- Unknown edit location: use an AGY explorer; narrow `--scope DIR` when justified.
  Export is Git-tracked current worktree UTF-8 source by default. Use
  `--include-untracked` only when needed and authorized. Secrets/configurations,
  binary files, submodules, and large files may be excluded. Do not claim the
  worker inspected them. The snapshot manifest records exclusions.

## AGY delegation

Use one external per-task usage state for every worker in this task. Reuse a
provided state. Otherwise write a concise task/anchors/acceptance-criteria file in
an authorized private directory outside the repository and initialize once:
`python3 "$ES/budget.py" init --repo . --task-file TASK --state-dir STATE`
Then invoke, using a fresh output directory outside the repository for each run:
`python3 "$ES/locate.py" --repo . --task-file TASK --state-dir STATE --out-dir RUN`
For Reader add `--mode reader --path FILE` (repeat paths). For additional localized
investigation add `--deep`; provide only the missing relationship and prior
candidates, not your full transcript. The existing state accepts focused follow-up
question text while retaining the task's cumulative usage history.

Worker invocations are unlimited, including --deep (Gemini 3.8 Flash, High).
At most one worker is active at a time. Normal explorer/reader use Medium. Do not
silently override the model, resume an AGY conversation, widen permissions, or
install dependencies to get around an environment error. No automatic retries.
Read only handoff.json and concise metrics on failure, NOT events.jsonl,
request.jsonl, result.json, or the exported workspace wholesale.

The runner uses a disposable export and a restricted AGY MAIN-agent definition,
not a native Codex child. Native Codex Hooks do not observe AGY's internal tools;
the wrapper validates AGY init, final structured output, original source hashes,
and records usage in the shared state. Unknown usage remains unknown without
blocking later calls. Local deadlines/tool counts are not hard token/cost caps.
Do not infer usage or safety from an unverified runner or a missing terminal result.

## Verify and implement

A supplied valid handoff is not a reason for another initial exploration.
`python3 "$ES/evidence.py" check --root . --handoff PATH`
A hash proves source identity, not semantic correctness. Read original source:
`python3 "$ES/evidence.py" read --root . --path FILE --start N --end M --expect-sha256 HASH`
Read beyond returned anchors when the full function, contract, caller, registration,
imports or tests are necessary. Never implement from a prose summary alone.
A stale source/corpus calls for targeted current-source verification, not automatic
another-model escalation. A partial answer can be sufficient to start work.

Use --deep only when actual evidence falsifies the chosen subsystem or a required
relationship is unresolved. Not for a missing test alone, syntax/type/assertion
errors introduced by a patch, malformed JSON, auth/permissions errors, unavailable
models, or quota exhaustion. Only you edit and approve changes.

## Preserve test evidence

Run tests, builds and other commands expected to emit large output through
`python3 "$ES/capture.py" --repo . --out-dir PRIVATE_NEW_DIR -- COMMAND ARGS`.
This returns exit status, short exact tails and raw-log paths. Do not print the
raw logs through code mode. On failure, read the relevant saved error range;
do not rerun the command to recover its output. Small source reads and bounded
searches should run directly, without capture overhead.

For commands likely to finish within 30 seconds, call `exec_command` with
`yield_time_ms=30000`. Give an enclosing `functions.exec` enough time to await
that call. If it returns a live session, wait on that same session for up to
30 seconds per call; do not poll every second. Batch independent lookups in one
tool call when their outputs are needed for the same implementation decision.

Hooks are optional: they do not replace explicit capture in code mode. If an
es_saved_tool_output response is returned, the command ALREADY ran; use its
recovery_command. Unknown exit status is not success.
Capture retains stdout/stderr and exit status, but does not sandbox the command.
Archived test logs describe a past run, not the correctness of newly edited source.
Do not re-emit large raw responses through code-mode JavaScript. Do not read AGY
transcripts through the parent's context. Do not bypass a v2 read guard using
another shell form. No commits, resets, stashes, pushes or permission escalation
without the applicable authorization.

Final response: actual changes, tests, blockers. Claim no total savings based on
worker-only metrics. AGY reported usage and Codex parent usage are separate;
provider quotas/AI credits are not automatically comparable to API token prices.
