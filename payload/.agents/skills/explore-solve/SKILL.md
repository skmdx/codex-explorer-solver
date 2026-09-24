---
name: explore-solve
description: Explicit source investigation with direct lookup or AGY evidence workers, exact-source verification, and compact command results.
---

Codex owns implementation and verification. Use `ES=/home/user/codex-work/.codex/es`
at the target Git repository root. Keep task/state/output directories under
`/home/user/codex-work/tmp`, outside the repository; remove them after collecting evidence.

## Investigate

Reuse current source and findings already in context. For a small question, search
only for missing locations and read the needed ranges directly. An unknown location
alone does not warrant delegation.

Use AGY to locate relevant ranges within large unread sources you would otherwise
load in full; choose this before reading them. Also delegate when explicitly requested.
Give the worker known findings and the unresolved question, not a repeat of completed
investigation or the conversation.
Recheck settled findings when source changes, evidence is missing, or an independent
review is requested.

For this skill's Gemini investigation, use `locate.py` to retain usage tracking and
source verification:
`python3 "$ES/locate.py" --repo . --task-file TASK --state-dir STATE --out-dir AGY_RUN`

Write the question and required evidence in TASK. STATE is created on first use;
reuse it for the same task. AGY_RUN must be a new directory for each invocation.
Reader: `--mode reader --path FILE` (repeat paths) sends the specified files, including
explicit untracked files; do not combine it with `--deep`, `--scope`, or `--include-untracked`.
Explorer: use `--scope DIR` when known. It exports current Git-tracked UTF-8 source;
`--include-untracked` adds non-ignored untracked files. Unexported areas were not inspected.
Default workers use Gemini 3.8 Flash Medium; `--deep` selects High for exploration without requiring
a previous worker. AGY calls/tool steps are unlimited, with one worker per STATE at a time.
Unknown usage stays unknown and permits later calls. AGY's native timeout applies unless
`--timeout` is set.

## Wait for the result

Keep waiting inside one code-mode cell instead of returning empty status to the model.
For AGY's default five-minute deadline, allow ten minutes for the enclosing cell.
If an explicit worker deadline is longer, extend the enclosing wait accordingly.
The shell session still needs internal wait calls; these do not require model turns.

```javascript
// @exec: {"yield_time_ms": 600000, "max_output_tokens": 5000}
let r = await tools.exec_command({cmd: COMMAND, yield_time_ms: 30000, max_output_tokens: 5000});
const output = [r.output];
while (r.session_id !== undefined) {
  r = await tools.write_stdin({session_id: r.session_id, chars: "", yield_time_ms: 30000, max_output_tokens: 5000});
  output.push(r.output);
}
text({...r, output: output.join("")});
```

If the host yields the cell, use `functions.wait` on that cell with the same enclosing
wait duration; do not start another shell poll or inspect logs for progress.

## Verify and implement

Successful `locate.py` output includes `evidence`: hash-verified, numbered originals
for all cited ranges, plus unresolved questions. Use those originals directly;
retrieve only missing callers/contracts or ranges needed for editing. Hashes establish
identity, not semantic relevance. Do not fetch the same unchanged source again just
because a handoff was saved. Re-read when it changed or is no longer available in context.
For changed source, inspect current contents locally rather than reuse the old handoff hash.
For missing ranges use `python3 "$ES/evidence.py" read --root . --path FILE --start N --end M --expect-sha256 HASH`.
If all cited originals are missing from context, retrieve them together with
`python3 "$ES/evidence.py" show --root . --handoff HANDOFF`.
If tool output was truncated, recover missing source from the saved result
instead of rerunning AGY. Resolve remaining questions without repeating completed work.
On worker failure, use concise metrics to choose a targeted retry or local investigation.

For tests/builds with large output:
`python3 "$ES/capture.py" --repo . --out-dir TEST_RUN -- COMMAND ARGS`
Use a new TEST_RUN, separate from AGY_RUN. Capture stops at 300 seconds or 16 MiB of logs
by default; raise `--timeout` / `--log-limit-bytes` for longer or noisier commands.
This returns exit code, exact short tails and full log paths. Check the actual test/build
result; use saved log ranges for missing details instead of rerunning. Small reads/searches
run directly. Hooks do not replace capture in code mode. When usage measurement is part
of the task, report parent and AGY usage separately; do not add measurement to every call.
