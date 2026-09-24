---
name: explore-solve
description: Source investigation with direct lookup or AGY source collection, verified originals, and compact command results.
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

**Only when using AGY**, read [references/agy.md](references/agy.md) before the call.
It contains the invocation, mode choices, long wait, and handling of verified originals.
After the handoff, make Solver decisions from those originals and read only missing
context or source that changed.

For tests/builds with large output, use
`python3 /home/user/codex-work/.codex/es/capture.py --repo REPO --out-dir TEST_RUN -- COMMAND ARGS`.
Use a new output directory outside the target repository and inspect the saved logs
only where the returned exit status and tails do not settle the result.
