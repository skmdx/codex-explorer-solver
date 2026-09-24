---
name: explore-solve
description: Explicit source investigation with direct lookup or AGY evidence workers, exact-source verification, and compact command results.
---

Codex owns implementation and verification. Run commands at the target Git repository
root. Set `ES=/home/user/codex-work/.codex/es` in each shell call that uses it; shell
variables and `cd` do not persist between calls. Use absolute task/state/output paths under
`/home/user/codex-work/tmp`, outside the repository. Retain STATE for the task's lifetime;
clean up task directories at completion, after retaining needed evidence.

## Investigate

Reuse current source and findings already in context. For a small question, search
only for missing locations and read the needed ranges directly. An unknown location
alone does not warrant delegation.

Use AGY to locate relevant ranges within large unread sources you would otherwise
load in full; choose this before reading them. Also delegate when explicitly requested.
Give the worker known findings and the unresolved question, not a repeat of completed
investigation or the conversation.
For a multi-part investigation, delegate a specific call chain or ownership boundary
with a concrete question and completion criterion. Keep its necessary callers and
callees together; integrate separate findings in Codex instead of asking each worker
to perform the whole audit. Request `partial` with useful evidence when another
independent question remains. A handoff can contain all necessary source locations.
Recheck settled findings when source changes, evidence is missing, or an independent
review is requested.

For this skill's Gemini investigation, use `locate.py` to retain usage tracking and
source verification:
`python3 "$ES/locate.py" --repo . --task-file TASK --state-dir STATE --out-dir AGY_RUN`

Create a task directory and write the question and required evidence in TASK inside it.
STATE is created on first use; reuse it for the same task. Choose an unused AGY_RUN path
for each invocation; let `locate.py` create it instead of precreating it with `mkdir`/`mktemp`.
Reader: `--mode reader --path FILE` (repeat paths) sends the specified files, including
explicit untracked files; do not combine it with `--deep`, `--scope`, or `--include-untracked`.
Explorer: use `--scope DIR` when known. It exports current Git-tracked text source;
`--include-untracked` adds non-ignored untracked files. Unexported areas were not inspected.
The tool detects encoding separately for each file and exports UTF-8 copies; mixed
ASCII, UTF-8, CP932 and EUC-JP inputs need no prereading or encoding arguments.
`--encoding PATH=CODEC` only corrects a known detection error for that file.
Reader also accepts explicit Git hook paths, agent configuration and absolute paths
outside the repository. These are investigation data, separate from the worker configuration.
Default workers use Gemini 3.8 Flash Medium; `--deep` selects High for exploration without requiring
a previous worker. AGY calls/tool steps are unlimited. Normally run one worker at a time;
unfinished usage records do not prevent the next invocation.
Unknown usage stays unknown and permits later calls. AGY defaults to a five-minute
deadline. For a long cross-module investigation, set a suitable `--timeout` in seconds
(for example `--timeout 900`). Extending the enclosing cell does not extend AGY's deadline.

## Wait for the result

Keep waiting inside one code-mode cell instead of returning empty status to the model.
After starting AGY, wait for its result before doing further source investigation;
otherwise Codex may read the same source that the worker is about to return.
For AGY's default five-minute deadline, allow ten minutes for the enclosing cell.
If an explicit worker deadline is longer, extend the enclosing wait accordingly.
The shell session still needs internal wait calls; these do not require model turns.
In the example, COMMAND is the complete shell command (including ES if used), and
REPO is the absolute target Git root. Substitute both before executing the cell.

```javascript
// @exec: {"yield_time_ms": 600000, "max_output_tokens": 5000}
let r = await tools.exec_command({cmd: COMMAND, workdir: REPO, yield_time_ms: 30000, max_output_tokens: 5000});
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

Exit zero from `locate.py` means a validated report, not completion of the task.
Read `handoff_status` and unresolved questions: `partial`/`blocked` may need follow-up,
and `not_found` applies only to the exported scope. Its `evidence` includes hash-verified,
numbered originals for cited ranges. Use those originals directly;
retrieve only missing callers/contracts or ranges needed for editing. Hashes establish
identity, not semantic relevance. Do not fetch the same unchanged source again just
because a handoff was saved. Re-read when it changed or is no longer available in context.
For changed source, inspect current contents locally rather than reuse the old handoff hash.
For missing ranges use `python3 "$ES/evidence.py" read --root . --path FILE --start N --end M --expect-sha256 HASH`.
If all cited originals are missing from context, retrieve them together with
`python3 "$ES/evidence.py" show --root . --handoff HANDOFF`.
If tool output was truncated, recover missing source from the saved result
instead of rerunning AGY. Resolve remaining questions without repeating completed work.
The response includes `usage`, exported `scope`, and `error`; read `metrics_path` only
for missing diagnostics. `status` describes harness execution; `handoff_status` describes
the findings. `accounting_failed` can still carry usable evidence: address the ledger
error rather than rerun the investigation. Errors outside the worker run return
`invocation_failed` and `error`; the CLI's argument syntax errors use stderr.

For tests/builds with large output:
`python3 "$ES/capture.py" --repo . --out-dir TEST_RUN -- COMMAND ARGS`
TEST_RUN must not exist and must differ from AGY_RUN. Capture waits for command completion
and keeps full logs. Set `--timeout` or `--log-limit-bytes` only when the task needs those limits.
This returns exit code, exact short tails and full log paths. Check the actual test/build
result; use saved log ranges for missing details instead of rerunning. Small reads/searches
run directly. Hooks do not replace capture in code mode. When usage measurement is part
of the task, report parent and AGY usage separately; do not add measurement to every call.
