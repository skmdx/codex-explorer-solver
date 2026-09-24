---
name: agy-subagents
description: Delegate independent reviews or authorized implementation to Gemini or Claude through blocking AGY MCP calls. Use when requested or when an external review materially helps; use explore-solve for source evidence collection.
---

Use the `agy-subagents` MCP's `run` tool directly. It waits for the final result;
this namespace is excluded from Code Mode. The host sets the 30-minute deadline.
Do not launch AGY through the shell or wrap it in a background job.

Give a self-contained task with the needed facts and deliverable. Keep Claude
review requests concise. Pass `repo` when files are needed; omit it for pure
reasoning. `scratch_dir` is an existing temporary directory outside the repository.

The default model is Gemini High. Use `models` to choose an available Claude or
other explicitly requested model. Respect the requested provider; report a failure
before changing it. Do not shorten deadlines or lower reasoning to work around a wait.

`mode: review` provides read/search tools. `mode: edit` also allows file edits and
commands: use it only for authorized work and name the files to change in the task.

Evaluate the returned advice against the evidence. Inspect delegated edits and
verify their behavior before accepting them. Failed runs retain their response,
error and usage; a partial response is not successful completion.
