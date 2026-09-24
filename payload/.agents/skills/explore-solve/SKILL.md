---
name: explore-solve
description: Explicit token-aware Codex solver with deterministic lookup, AGY/Gemini 3.8 Flash evidence workers, and exact-source verification.
---

Codex owns implementation and verification. Delegate source investigation through
`ES=/home/user/codex-work/.codex/es` and its `locate.py` only; no direct AGY calls,
Codex workers, or substitute providers. Run at the target Git repository root.
Keep temporary task/state/output directories under `/home/user/codex-work/tmp`,
outside the target repository, and remove them after collecting needed evidence.

## Choose a source path

- Known edit location or small question: read the relevant source directly.
- Known file and literal: `python3 "$ES/gateway.py" search --root . --path FILE --literal TEXT`.
  Pagination requires the same source SHA; a scoped no-match proves no wider absence.
- Known files with substantial source to read: use Reader with `--mode reader --path FILE`
  (repeat paths). Do not first load those whole files into parent context.
- Unknown location: use Explorer with `--scope DIR` when the scope is known.
  Default export covers Git-tracked current UTF-8 source. Add `--include-untracked`
  only when needed. Excluded files and areas outside the export were not inspected.

## Delegate

Write the question and required evidence in TASK. Initialize once, or reuse STATE:
`python3 "$ES/budget.py" init --repo . --task-file TASK --state-dir STATE`
Run with a fresh RUN directory:
`python3 "$ES/locate.py" --repo . --task-file TASK --state-dir STATE --out-dir RUN`

Reader/Explorer use Gemini 3.8 Flash Medium; `--deep` uses High for harder unresolved
relationships. Worker and internal tool calls are unlimited, one worker at a time.
The default uses AGY's native timeout; set `--timeout SECONDS` only when needed. Reuse STATE for
follow-ups and provide the missing question, not the full conversation. Failures
and unknown usage stay in the ledger; unknown does not mean zero or block later calls.
No automatic retries, model substitution, conversation resume, or permission expansion.
The export/tool restrictions are not an OS sandbox or a hard spending cap.

## Verify and implement

Use an existing handoff without repeating exploration:
`python3 "$ES/evidence.py" show --root . --handoff HANDOFF`
This validates every reference and returns the cited numbered originals in one call.
If the combined output exceeds its limit, use `check --root . --handoff HANDOFF`,
then `read --root . --path FILE --start N --end M --expect-sha256 HASH` for needed ranges.
Hashes prove source identity, not relevance. Read further callers/contracts when
needed; implement from originals. For stale evidence, inspect current source locally.
Read concise metrics on worker failure, not AGY transcripts or exported trees wholesale.

## Capture command output

For tests/builds with large output:
`python3 "$ES/capture.py" --repo . --out-dir RUN -- COMMAND ARGS`
Normal exit returns code, short exact tails, and log paths; failures include longer
tails and termination details. Full stdout/stderr and metadata remain on disk.
Read saved error ranges rather than rerunning to recover output. Small reads/searches
run directly. Capture is not a sandbox; exit zero alone does not prove task correctness.

Use `exec_command` with `yield_time_ms=30000` for likely short commands and let
enclosing `functions.exec` await it. Wait on the same live session, without one-second
polling. Batch independent lookups needed for the same decision.
Hooks do not replace capture in code mode. A saved-output response means the command
already ran; use its recovery path and do not re-emit the full log.
Report parent and AGY usage separately; worker-only counters cannot prove total savings.
