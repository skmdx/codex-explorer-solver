# AGY collection

Use `collect` for source facts missing from the next Solver decision. State the
question and the needed conditions, updates or callers separately; do not ask it
to solve an entire issue or find design flaws. Group facts sharing a source path.
Existing conclusions belong in `known_findings`, not in another broad investigation.

The MCP call waits for completion. It does not return a running job to poll. If the
host yields a code-mode cell, resume that same cell; do not restart or kill AGY
because an observation wait expired. `timeout` is the collection deadline in seconds.

## Known symbol → LSP → AGY, in one cell

Use existing Symbols results where available. Otherwise request only the relations
needed: `inspect` for definitions, `references` for uses, `call_hierarchy` for calls.
Use `outline` or `search` first only when the position is unknown. If LSP answers the
question directly, finish without AGY. Match the actual tool names exposed by the host.

Replace the example paths, position and question. `navigation.root` is the Symbols
profile's workspacePath; it may be above the repository. Positions are 1-based.

```javascript
// @exec: {"yield_time_ms": 1200000, "max_output_tokens": 4000}
const args = {file: "/absolute/repo/src/file.c", line: 120, character: 5};
const results = await Promise.allSettled([tools.mcp__language_servers__references(args)]);
const r = results[0];
const navigation = {root: "/absolute/workspace", queries: [{tool: "references", args,
  ...(r.status === "fulfilled" ? {result: r.value} : {error: String(r.reason)})}]};
text(await tools.mcp__explore_solve__collect({
  repo: "/absolute/repo", scratch_dir: "/absolute/workspace/tmp", scope: ["src"],
  question: "Which condition prevents adoption of an old completion?",
  evidence_needed: ["The adoption condition and writes to the version it checks"],
  navigation, known_findings: "The completion carries a saved version."
}));
```

Do not print LSP results before the collection call. They go straight into its
initial prompt. Errors remain unavailable results, not empty reference lists.
`scope` covers the question's source area; LSP hits are starting points, not an
exhaustive export filter. Without a useful symbol seed, pass `navigation: null`.
Use an empty `known_findings` only for a new investigation.

## Read only needed originals

`collect` returns `run_dir`, indexed locations, short observations and unresolved
questions. These observations are leads, not verified semantic conclusions.
Call `read_evidence(run_dir, ids)` for the locations needed now. It checks source
hashes and combines overlapping lines. For large selections, repeat the same IDs
without an offset: each call returns the next page until `complete` is true.
`next_offset` reports where that next page starts.
Completed selections return no duplicate source; explicit `offset: 0` rereads after
context loss. `max_chars` is the response page size, not an
evidence limit. Keep using returned originals instead of rereading them with sed.

All originals and usage remain in `run_dir/report.json`; `report.txt` is a full
diagnostic artifact, not the default context input. A failed collection returns
its error and artifact path. Do not rerun successful collection because usage
accounting failed. Keep useful artifacts, then remove the task's temporary run.
