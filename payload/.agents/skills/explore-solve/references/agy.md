# AGY source collection

Use this reference only when the investigation needs AGY. Codex remains the Solver;
ask Explorer for definitions, callers, state changes, conditions, and test locations
needed for the current decision. Keep related callers/callees together. In follow-ups,
pass known findings and ask only for missing evidence.

## Invoke

Run at the target Git root. Write the collection request to TASK. Use absolute paths
under `/home/user/codex-work/tmp` for TASK, STATE, and RUN, outside the target Git
repository. STATE is created on first use and reused for this task; RUN must be new.

Construct COMMAND with `python3 /home/user/codex-work/.codex/es/locate.py --repo REPO
--task-file TASK --state-dir STATE --out-dir RUN`. The default model is Gemini 3.8
Flash High. Add only options the investigation needs:

- `--scope DIR` narrows Git-tracked working-tree sources; `--include-untracked` adds
  non-ignored untracked files.
- `--mode reader --path FILE` sends known files directly; repeat `--path` as needed.
  Reader does not combine with `--scope`, `--include-untracked`, or `--deep`.
- `--deep` uses the configured `deep_model` (currently also Flash High).
- `--timeout 900` gives a long investigation 15 minutes; AGY defaults to five minutes.

Start and wait in the **same** code-mode cell. Replace COMMAND and REPO below with
the complete shell command and absolute Git root. The long enclosing yield prevents
model-visible short polling while internal session waits run.

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

If the host yields the cell, wait on that **same cell** with a long supported wait;
do not start another investigation because observation timed out. Wait for AGY's
evidence before reading its delegated source yourself. A longer enclosing wait does
not change AGY's own deadline.

## Use the result

Default output gives status, scope counts, short observations, and verified numbered
originals; overlapping original lines appear once. Use that text directly for Solver
decisions. Source hashes establish identity, not the correctness of Explorer's
interpretation.

`ready` means collection completed, not that the user's task is solved. `partial` and
`not_found` describe the exported scope. Resolve missing evidence locally or with a
focused follow-up. Accounting errors can accompany usable evidence and do not require
rerunning the investigation.

RUN contains `report.txt` (displayed text), `report.json` (complete machine report),
`handoff.json`, and `metrics.json`. If output is cut off, fetch only the missing part
of `report.txt`; do not dump the whole JSON or repeat completed ranges. Use `--json`
only for a programmatic consumer. Compare Codex and AGY usage separately only when
measurement is part of the task.

Encoding is detected per file, including mixed ASCII/UTF-8/CP932/EUC-JP; Codex need
not preread. `--encoding PATH=CODEC` corrects a known detection error. Reader also
accepts Git hooks, agent configuration, and absolute paths outside the repository as
source data. Unexported files were not inspected. Clean up RUN and STATE after
retaining any needed evidence.
