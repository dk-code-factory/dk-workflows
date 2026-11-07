# Repository instructions

Read [DESIGN.md](DESIGN.md) before changing workflow coordination or persistence.

- Optimize the complete workflow's token consumption, including the next run in
  a new session. Do not add durable state unless it avoids more model work than
  creating, validating, loading and explaining that state costs. Explain that
  saving and its freshness boundary in the design and change description.
- Reuse valid completed analysis across sessions. Preserve existing cache keys,
  fingerprints, original results, completed siblings and recovery information.
  Do not clear caches or rerun reviewers just to simplify implementation.
- Let deterministic helpers construct handoffs, capture inputs, claim work and
  record results. Agent instructions should describe decisions and native skill
  execution, rather than repeat a storage protocol.
- Persist only the minimum needed to avoid repeated analysis or uncertain
  external actions. Use references to existing evidence. Do not save copies of
  derivable status, rendered summaries, whole conversations, settings snapshots
  or telemetry by default. Temporary calculations belong in memory.
- Resume with compact status and targeted evidence reads. Never load an entire
  manifest or transcript just to discover outstanding work. Changed governing
  inputs still require affected work to be refreshed; savings cannot justify
  stale coverage or skipped authorization, publication or CI checks.
- Keep publication intent, receipts and bounded retry/CI progress: they avoid
  repeated agent recovery and duplicate external actions after interruptions.
- Test unchanged fresh-session reuse, partial recovery and relevant-input
  invalidation for persistence changes. Helper/process counts are proxies, not
  measured token savings. Report native token evidence separately when available.
- Maintain prompts in `codex/skills/`, then run `scripts/sync_skills.py`. Run
  `scripts/check.py` and the relevant offline tests; use `scripts/test.py` for
  complete validation. Tests must not make authenticated model or GitHub calls.


## Codex and Claude execution boundary

It is strictly forbidden for production code to launch Codex or Claude Code. This applies to runtime helpers, production scripts and integrations, whether execution is direct or indirect through subprocesses, shell commands, wrappers, background jobs or other launchers. Do not add a separate model-session or agent runner.

Skills and their referenced runtime guidance must never instruct, recommend or provide executable examples for launching Codex or Claude Code. This includes fallback, retry, recovery and workarounds for unavailable native tools or execution evidence.

The already-running Codex or Claude Code session owns skill execution and any supported native delegation. Python helpers validate configuration and evidence, manage task claims, retain state and budgets, and perform their documented Git and GitHub operations. They do not start Codex or Claude Code sessions.

If required native execution, delegation or evidence is unavailable, report the affected work as blocked or incomplete. Never launch Codex or Claude Code as a substitute.

Preserve this boundary when changing the [invocation contract](docs/invocations.md), [native execution evidence](docs/native-review.md), production code or either skill distribution.
