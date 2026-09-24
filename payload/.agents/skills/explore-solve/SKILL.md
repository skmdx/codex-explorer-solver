---
name: explore-solve
description: Explicit source investigation with direct lookup or AGY evidence workers, exact-source verification, and compact command results.
---

Codex owns implementation and verification. Use `ES=/home/user/codex-work/.codex/es`
at the target Git repository root. Keep task/state/output directories under
`/home/user/codex-work/tmp`, outside the repository; remove them after collecting evidence.

## Investigate

Start with local source navigation/search and read the relevant originals. An unknown
file location alone does not warrant delegation. Batch independent searches/reads;
run requested tests alongside investigation when their command is already known.

Delegate substantial independent reading or exploration when it avoids loading that
source into the parent, or when explicitly requested. Use `locate.py` only, with no
direct AGY invocation, native Codex workers, or substitute providers.
Write the question and needed evidence in TASK, then use a fresh RUN directory:

`python3 "$ES/locate.py" --repo . --task-file TASK --state-dir STATE --out-dir RUN`

STATE is created on first use; reuse it for follow-ups. Known files: add
`--mode reader --path FILE` (repeat paths). Broader exploration: add `--scope DIR`
when known. Export defaults to current Git-tracked UTF-8 source; add
`--include-untracked` only when needed. Unexported areas were not inspected.
Default workers use Gemini 3.8 Flash Medium; `--deep` uses High for unresolved relationships.
Worker/tool calls are unlimited, with one worker at a time. Unknown usage stays unknown
and does not block later calls. AGY's native timeout applies unless `--timeout` is set.

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
for all cited ranges, plus unresolved questions. Read these originals and any needed
callers/contracts; hashes establish identity, not semantic relevance. No separate
`evidence.py show` call is needed. For a saved handoff use
`python3 "$ES/evidence.py" show --root . --handoff HANDOFF`.
Inspect current source locally when evidence is stale. On worker failure, read concise
metrics rather than entire transcripts or exports; there are no automatic retries.

For tests/builds with large output:
`python3 "$ES/capture.py" --repo . --out-dir RUN -- COMMAND ARGS`
This returns exit code, exact short tails and full log paths. Check the actual test/build
result; use saved log ranges for missing details instead of rerunning. Small reads/searches
run directly. Hooks do not replace capture in code mode. Report parent and AGY usage
separately; worker-only counters cannot prove total savings.
