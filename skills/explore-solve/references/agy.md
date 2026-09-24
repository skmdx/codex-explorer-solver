# AGY collection

Use `collect` for source facts missing from the next Solver decision. State the
question and the needed conditions, updates or callers separately; do not ask it
to solve an entire issue or find design flaws. Group facts sharing a source path.
Existing conclusions belong in `known_findings`, not in another broad investigation.

Call `collect` directly. The plugin excludes its MCP tools from Code Mode;
there is no outer cell or polling handle for this call.
The collection deadline is configured in `agy.toml`, not passed by the Solver.

## Pass known symbol locations

Use existing Symbols results where available. Otherwise request only the relations
needed: `inspect` for definitions, `references` for uses, `call_hierarchy` for calls.
Use `outline` or `search` first only when the position is unknown. If LSP answers the
question directly, finish without AGY. Match the actual tool names exposed by the host.

Replace the example paths, position and question. `navigation.root` is the Symbols
profile's workspacePath; it may be above the repository. Positions are 1-based.

Example direct `collect` arguments after a Symbols query:

```json
{
  "repo": "/absolute/repo",
  "scratch_dir": "/absolute/workspace/tmp",
  "scope": ["src"],
  "question": "Which condition prevents adoption of an old completion?",
  "evidence_needed": ["The adoption condition and writes to the version it checks"],
  "navigation": {
    "root": "/absolute/workspace",
    "queries": [{
      "tool": "references",
      "args": {"file": "/absolute/repo/src/file.c", "line": 120, "character": 5},
      "result": {"locations": [{"path": "src/file.c", "line": 160}]}
    }]
  },
  "known_findings": "The completion carries a saved version."
}
```

Pass the relevant locations already returned by Symbols. Do not repeat an LSP
query just to fill `navigation`. Errors remain unavailable results, not empty reference lists.
`scope` covers the question's source area; LSP hits are starting points, not an
exhaustive export filter. Without a useful symbol seed, pass `navigation: null`.
Scopes are repository-relative files, directories, or globs: `src/*.c` selects direct
children; `src/**/*.c` also includes nested files. Multiple patterns are combined.
Explicit scopes include ignored/untracked files and nested repositories. With
`scope: []` or `["."]`, Git's tracked list is used; `include_untracked` adds
non-ignored untracked files. Unmatched patterns are returned as `unmatched_scopes`.
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
