---
name: agy-subagents
description: Delegate independent reviews or authorized Claude edits through blocking AGY MCP calls. Use explore-solve for source evidence collection.
---

Use `run` directly, outside Code Mode. It waits for the final result.
Obtain `scratch_ref` from Scratch's `create`; delete it after using the results.
Its directory must be outside the target repository.

Pass one `request` with `task` and `scratch_ref`:

- Review (default): add `repo` when files are needed. State the target scope,
  question, and expected result, for example: "Review the uncommitted changes in
  src/cache.py for stale-value reuse; return concrete findings with locations."
  Read/search tools are available; commands and edits are not.
- Edit: add `mode: "edit"` and required `repo`. Describe the authorized change
  scope and expected behavior. The scope can name files, directories, or a feature;
  exact filenames need not be known before investigation. The agent can edit and
  run checks, but does not commit or push.

Normally omit model selection. Reviews use the configured order and automatically
switch on usage limits or unavailable models. Only when a particular review model
is needed, use `models` and set `preferred_model`; this is not a fixed-model guarantee.
Editing uses the configured Claude model only, with no per-call override or fallback.
If it fails or is unavailable, inspect partial changes and continue in the parent
agent. Do not retry the edit through Gemini or bypass the tool through the shell.

Evaluate advice against the evidence and verify delegated changes before accepting
them. A completed invocation is not proof that the requested behavior works.
Report the actual model and unresolved problems; inspect attempt logs when needed.
Responses up to 6000 characters arrive together. If `next_offset` is present,
continue with `read_report(run_dir, pointer="/response", offset=next_offset)`.
Use `/usage` or `/attempts` only when those details matter. `max_chars` defaults
to 6000 and accepts 1000–20000 to balance detail and round trips.
