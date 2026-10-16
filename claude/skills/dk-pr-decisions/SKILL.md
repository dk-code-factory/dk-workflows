---
name: dk-pr-decisions
description: Collect PR discussion and description decisions, suggest choices from code or description, explicitly publish or replace entries in the PR Decisions section, and prepare reconciled engineering boundaries for iteration or standalone review.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Before work, read [the lifecycle procedure](../../../common/lifecycle.md), then run its entry command with `--host claude`. Retain its returned token and follow its structured action. Always run its completion command with that token in a finalization step, including on failure or interruption.

Read [the shared procedure](../../../runtime/dk-pr-decisions/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
