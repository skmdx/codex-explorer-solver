---
name: explore-solve
description: Collect missing source evidence across unread definitions, callers, conditions, or tests. Use direct Symbols queries or targeted reads when they can settle the next decision.
---

Codex is the Solver: it judges guarantees, chooses fixes, and implements/tests them.
Explorer collects the missing source facts; do not delegate the final judgment.

Reuse source already in context. Use Symbols or a targeted read when a known
location or relation can settle the next decision. Use `collect` when that decision
requires searching several unread definitions, callers, conditions, or tests,
or when collection is explicitly requested. Group facts sharing a source area.

1. Obtain `scratch_ref` from Scratch's `create`.
2. Call `collect` directly with `request: {repo, scratch_ref, question,
   evidence_needed: [{fact, scope}]}`. Add settled conclusions as `known_findings`.
   Repository scopes select tracked files; `include_untracked` adds nonignored files.
   For explicit files, including ignored or external files, use `source: "files"`
   and put their paths in each fact's scope. See [request examples](references/agy.md).
3. Pass existing Symbols responses as `navigation: [{tool, result}]` when useful.
   Include `read_symbols` responses to reuse matching source already read.
   Navigation is saved automatically; reuse the returned `navigation_id`.
4. If the index has `next_offset`, use `list_evidence(run_dir, offset)` for more
   locations. Read needed IDs with `read_evidence`. Repeat the same IDs until `complete`.
   Judge from those originals and reuse them. After context loss, set `reread: true`
   on the first call only.
5. On failure, retry with `request: {params_file, updates}`; updates replace only
   specified fields. After using the evidence, delete the Scratch reference.

The direct MCP call waits for completion; there is no polling handle. Sonnet
falls back to Gemini Flash High on usage limits within the `agy.toml` deadline.
For builds and tests, use the Blocking Shell plugin's execution and log facilities.
