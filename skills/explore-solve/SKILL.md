---
name: explore-solve
description: Collect missing source evidence when investigation spans unread definitions, callers, conditions, or tests across multiple files; capture large build/test output for focused inspection. Small questions and known source locations can use direct lookup.
---

Codex is the Solver: it evaluates guarantees, constructs race scenarios, chooses fixes,
and implements/tests them. Explorer collects definitions, callers, state changes,
conditions, and test locations. Do not delegate the Solver's final judgment.

## Choose the investigation

Reuse source already in context. For small questions, use Symbols or targeted reads
directly. Use AGY for large unread source searches or when explicitly requested.
Ask it for the missing evidence, not the entire user task. Group requests that share
source and callers; split only when they need different source areas.
Collection stops locally after 24 observed tool steps by default (override with
`max_steps`). This includes repairs and model fallback within the call. AGY may
have already started further work; this is not a hard remote token/spend cap.
At `step_limit`, inspect existing evidence and unresolved facts before deciding
whether more collection is needed. `resume(resume_id)` reuses the saved request,
source snapshot and AGY conversation, with a fresh finite step allowance. Do not
automatically resume at the limit. Changed source requires a new collection.
Usage can be unknown when interrupted; never interpret it as zero.

Collection starts with Sonnet and resumes on Gemini Flash High if Sonnet reaches
a usage limit. The harness handles this fallback within the same deadline.

For AGY, call the plugin's `collect` MCP tool directly. The host exposes this
namespace outside code-mode cells, so collection returns only on completion,
failure, or cancellation. The collection deadline comes from `agy.toml`.
Give the next decision and pair each missing fact with its source scope in
`evidence_needed: [{fact, scope}]`. The harness validates and unions these scopes.
Failed requests return a saved `params_file`. Retry with that reference and, when
needed, `updates` containing only corrected arguments. The tool saves corrections
and deletes the request after success. No manual file editing is needed.
After evidence and logs are no longer needed, call `cleanup` directly with no
arguments. It deletes this MCP session's collection directories and argument files,
including failed requests. Inspect reported save/deletion errors; active paths are skipped.
Reuse known findings; reopen settled questions only for a source change, new failure, or counterexample.
If known Symbols locations can seed the search, pass their results directly to
`collect.navigation` using [the example](references/agy.md). To reuse navigation
across collections without resending its payload, call `remember_navigation`
once and pass the returned `navigation_id` as `navigation`. Include existing
`read_symbols` results so matching originals are not returned again.
Collection returns an index. Select needed IDs with `read_evidence`; repeat those
IDs without an offset until `complete` is true. Judge from these originals and
reuse them, reading further only for missing or changed source.

Resolve ES to the absolute path of `../../payload/.codex/es` relative to this SKILL.md's
directory. This is the installed plugin's tools directory, not the target repository.
For tests/builds with large output, use
`python3 ES/capture.py --repo REPO --out-dir TEST_RUN -- COMMAND ARGS`, replacing ES with that path.
Use a new output directory outside the target repository and inspect the saved logs
only where the returned exit status and tails do not settle the result.
