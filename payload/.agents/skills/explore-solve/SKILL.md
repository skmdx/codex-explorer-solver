---
name: explore-solve
description: Source investigation with direct lookup or AGY source collection, verified originals, and compact command results.
---

Codex is the Solver: it evaluates guarantees, constructs race scenarios, chooses fixes,
and implements/tests them. Explorer collects the definitions, callers, state changes,
conditions and test locations needed for those decisions. It interprets code to find
relevant evidence, but is not assigned the Solver's final analysis or implementation plan.

## Choose the investigation

Reuse findings and source already in context. Handle small questions directly with
Symbols or targeted reads. Use AGY for large unread source searches, or when explicitly
requested. Give it the missing evidence to collect, rather than the entire user task.
For example: "Locate epoch updates, capture revalidation, source retirement conditions,
and their tests; return the relevant originals and necessary callers." Codex then
constructs and evaluates the competing execution orders.

Group evidence requests that share source and callers into one investigation. Split
when they need separate source areas, not merely because the final answer has several
questions. Keep necessary callers/callees together. Pass known findings to follow-ups;
request only missing evidence. Calls and tool steps have no fixed count limit.

## Run AGY and wait

Run at the target Git root. Write the collection request to TASK. Use absolute paths
under `/home/user/codex-work/tmp`, outside that repository, for TASK, STATE and RUN.
STATE is created on first invocation and reused for this task; RUN must be a new path.

Construct COMMAND using `/home/user/codex-work/.codex/es/locate.py --repo REPO
--task-file TASK --state-dir STATE --out-dir RUN` with `python3` as the executable.
The default call uses Gemini 3.8 Flash High. Add options as needed:

- `--scope DIR` narrows Explorer's Git-tracked working-tree sources;
  `--include-untracked` adds non-ignored untracked files.
- `--mode reader --path FILE` sends known files directly; repeat `--path` as needed.
  Reader uses explicit paths instead of `--scope`, `--include-untracked` or `--deep`.
- `--deep` selects High for exploration; a prior Medium call is not required.
- `--timeout 900` gives a long investigation 15 minutes; AGY defaults to five minutes.

Start and wait in the SAME code-mode cell below. Substitute COMMAND and REPO with the
complete shell command and absolute Git root. The long enclosing yield is part of this
invocation, not an optional optimization. Internal session waits do not need model turns.

```javascript
// @exec: {"yield_time_ms": 1200000, "max_output_tokens": 12000}
let r = await tools.exec_command({cmd: COMMAND, workdir: REPO, yield_time_ms: 1000, max_output_tokens: 12000});
const output = [r.output];
while (r.session_id !== undefined) {
  r = await tools.write_stdin({session_id: r.session_id, chars: "", yield_time_ms: 30000, max_output_tokens: 12000});
  output.push(r.output);
}
text({...r, output: output.join("")});
```

If the host yields the cell anyway, wait on that SAME cell with the longest supported
wait; do not switch to one- or ten-second calls. Match the enclosing wait to longer
worker deadlines. Wait for the evidence before reading the delegated source yourself.
A host that forces intermediate returns still requires resuming the cell; this skill
does not supply a separate completion-notification service.

## Use the returned originals

The default output contains status, scope counts, short observations and verified,
numbered originals. Overlapping lines are displayed once. Use that text directly for
Solver decisions; read only missing context or source that changed. Hash checks establish
source identity, not the correctness of Explorer's interpretation.

`ready` means the requested collection is complete, not that the user's task is solved.
`partial` or `not_found` describes this exported scope. Resolve missing evidence locally
or with a focused follow-up; reuse established findings. Accounting errors can accompany
usable evidence and do not require rerunning the investigation.

RUN contains `report.txt` (the displayed text), `report.json` (the complete machine report),
`handoff.json` and `metrics.json`. If output is cut off, retrieve only the missing part of
`report.txt`; do not dump the whole JSON or repeat the completed source ranges.
Use `--json` only for a programmatic consumer. Usage is recorded in metrics; compare Codex
and AGY separately when measurement is part of the task, not on every invocation.

Encoding is detected per file, including mixed ASCII/UTF-8/CP932/EUC-JP; no Codex prereading
is needed. `--encoding PATH=CODEC` corrects a known detection error. Reader accepts Git
hooks, agent configuration and absolute paths outside the repository as source data.
Unexported files were not inspected. Clean up RUN/STATE after retaining needed evidence.

For tests/builds with large output, use
`python3 /home/user/codex-work/.codex/es/capture.py --repo REPO --out-dir TEST_RUN -- COMMAND ARGS`.
TEST_RUN must be new and separate from RUN. It waits for completion, saves full logs and
returns the exit code and short exact tails. Set time/log limits only when needed.
