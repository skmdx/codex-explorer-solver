# Collection requests

Call `collect` directly, outside Code Mode. The tool waits for completion.
The input has one required `request`, which takes one of three forms.

## Repository search

```json
{
  "request": {
    "repo": "/absolute/repo",
    "scratch_ref": "fern-moon-lake",
    "question": "Which condition prevents adoption of an old completion?",
    "evidence_needed": [
      {"fact": "Adoption condition and writes to the version it checks", "scope": ["src"]},
      {"fact": "Regression coverage for stale completions", "scope": ["tests/**/*.py"]}
    ],
    "known_findings": "The completion carries a saved version."
  }
}
```

`source` defaults to `repository`. Each scope selects repository-relative files,
directories, or globs from Git's tracked list. `*`, `?`, and `[abc]` stay within a
path component; `**` spans directories. `[]` or `["."]` selects the whole list.
`include_untracked: true` adds nonignored untracked files for every scope.
The tool checks each fact's scope before starting the model and exports their union.
Unmatched patterns identify the affected fact; nested repositories are separate inputs.

Optional `navigation` is an array of `{tool, result}` pairs. Copy the actual Symbols
MCP response into `result`, including `structuredContent`, without reconstructing it.
Current Symbols responses carry absolute locations; no workspace wrapper is needed.
Include existing `read_symbols` responses to reuse matching full-line read receipts.
The tool saves navigation automatically and returns `navigation_id`; pass that string
as `navigation` for later collections. Omit navigation when no useful seed exists.
Do not repeat a Symbols query just to populate it. Failed responses remain errors.

## Explicit files

Use `source: "files"` with the same required fields, but put explicit file paths
in every fact's `scope`. These may be repository-relative or absolute, including
ignored files, Git hooks, and external files. Empty scopes and globs are not accepted.
This mode sends the files for reading and has no `navigation` or `include_untracked`.

Both source modes accept `model` for a single-call override and `encodings` as a
path-to-codec mapping when automatic encoding detection needs correction.

## Saved requests and retries

For large input, save the inner request object as UTF-8 JSON inside the directory
returned by Scratch's `create`. Call:

```json
{"request": {"params_file": "/absolute/scratch/request.json"}}
```

Inline failures also save the request under `scratch_ref` and return `params_file`.
Retry with that path and optional `updates`, containing only changed request fields.
Values, including lists, are replaced whole; paths keep their original meaning.
The tool saves corrections and deletes the file after a validated collection.
Failures preserve it. Invalid scratch references return `params_save_error`;
cleanup failures return `params_delete_error` without discarding a validated result.
Normal fields cannot be mixed with `params_file`; saved requests cannot nest retries.

## Selected originals

The collection index contains locations and observations, not semantic proof.
It returns the first 40 locations. When `next_offset` is present, pass it as
`offset` to `list_evidence(run_dir, offset)`; IDs remain stable across pages.
No `next_offset` means the index is complete. Unresolved facts are returned with
the collection even when locations span pages. Usage and attempt history stay
in `run_dir/report.json`; read them only when needed for diagnosis or accounting.
Use `read_evidence(run_dir, ids)` for the originals needed to judge the next decision.
It checks source hashes, combines overlapping lines, and preserves whitespace and
line endings for patching. Repeating the same IDs continues until `complete` is true.
`max_chars` controls page size. Completed ranges are reused across ID selections
and matching Symbols receipts; incomplete pages remain available.
To reread after context loss, set `reread: true` on the first call, then continue normally.

Full evidence and usage remain in `run_dir`. Use saved diagnostic files only when the
index and selected originals leave a question unanswered. Once they are no longer
needed, call Scratch's `delete(refs=[...])`; it removes evidence, navigation and retry
files together. Active consumers are skipped; inspect deletion errors.
