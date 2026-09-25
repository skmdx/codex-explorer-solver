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
  "question": "Which condition prevents adoption of an old completion?",
  "evidence_needed": [{"fact": "The adoption condition and writes to the version it checks", "scope": ["src"]}],
  "navigation": {
    "root": "/absolute/workspace",
    "queries": [{
      "tool": "references",
      "args": {"file": "/absolute/repo/src/file.c", "line": 120, "character": 5},
      "result": {
        "content": [{"type": "text", "text": "Showing 1-1 of 1.\nFound 1 reference(s) across 1 file\n\nsrc/file.c (1 references)\n  @160:5 symbol"}],
        "structuredContent": {
          "locations": [{"path": "/absolute/repo/src/file.c", "range": {
            "start": {"line": 160, "character": 5}, "end": {"line": 160, "character": 10}
          }}],
          "page": {"offset": 0, "limit": 100, "total": 1, "nextOffset": null}
        }
      }
    }]
  },
  "known_findings": "The completion carries a saved version."
}
```

Pass the actual MCP response as `result`, without rebuilding this example by hand.
The host sends only `structuredContent` to the Explorer, remapping location paths while
keeping names, page metadata and errors unchanged. Text-only results need the
updated Symbols server. Include existing `read_symbols` results in `queries`:
their full-line receipts avoid returning the same version of source again.
Do not repeat an LSP
query just to fill `navigation`. Errors remain unavailable results, not empty reference lists.
Each evidence item's `scope` covers that fact's source area; LSP hits are starting points, not an
exhaustive export filter. Without a useful symbol seed, pass `navigation: null`.
The harness checks every item's scope before model startup and exports their union.
Scopes are repository-relative files, directories, or globs: `src/*.c` selects direct
children; `src/**/*.c` also includes nested files. Multiple patterns are combined.
Explicit scopes include ignored/untracked files and nested repositories. With
`scope: []` or `["."]`, Git's tracked list is used; `include_untracked` adds
non-ignored untracked files. Unmatched patterns fail before model startup and identify the affected fact.
Use an empty `known_findings` only for a new investigation.

## Read only needed originals

`collect` returns `run_dir`, indexed locations, short observations and unresolved
questions. These observations are leads, not verified semantic conclusions.
Call `read_evidence(run_dir, ids)` for the locations needed now. It checks source
hashes and combines overlapping lines. Bodies preserve indentation, line endings
and the final newline; range labels stay outside each contiguous body so it can
be used as `apply_patch` context. For large selections, repeat the same IDs
without an offset: each call returns the next page until `complete` is true.
`next_offset` reports where that next page starts.
Completed ranges are reused across different ID selections and from matching
Symbols receipts. Observations stay in the collection index; reads return only source blocks. In-progress pages retain
stable offsets; their ranges count as read only after the full selection is delivered.
Explicit `offset: 0` rereads after
context loss. `max_chars` is the response page size, not an
evidence limit. Keep using returned originals instead of rereading them with sed.

All originals and usage remain in `run_dir/report.json`; `report.txt` is a full
diagnostic artifact, not the default context input. A failed collection returns
its error and artifact path. Do not rerun successful collection because usage
accounting failed. Keep useful artifacts, then remove the task's temporary run.
