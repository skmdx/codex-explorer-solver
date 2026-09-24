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
source and callers; split only when they need different source areas. There is no
fixed limit on AGY calls or internal tool steps.

For AGY, use the plugin's `collect` MCP tool. It waits for the result inside one call.
In code mode, start that cell with `// @exec: {"yield_time_ms": 3600000, "max_output_tokens": 4000}`
on its first line so the outer cell also waits for completion.
If the host still yields, resume the same cell with `yield_time_ms: 3600000`.
The collection deadline comes from `agy.toml`; it is separate from the cell's observation wait.
Give the next decision and the missing code facts separately.
Reuse known findings; reopen settled questions only for a source change, new failure, or counterexample.
If known Symbols locations can seed the search, pass their results directly to
`collect.navigation` using [the single-cell example](references/agy.md).
Collection returns an index. Select needed IDs with `read_evidence`; repeat those
IDs without an offset until `complete` is true. Judge from these originals and
reuse them, reading further only for missing or changed source.

Resolve ES to the absolute path of `../../payload/.codex/es` relative to this SKILL.md's
directory. This is the installed plugin's tools directory, not the target repository.
For tests/builds with large output, use
`python3 ES/capture.py --repo REPO --out-dir TEST_RUN -- COMMAND ARGS`, replacing ES with that path.
Use a new output directory outside the target repository and inspect the saved logs
only where the returned exit status and tails do not settle the result.
