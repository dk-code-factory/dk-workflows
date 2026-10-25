---
name: dk-pr-iter
description: Verify and fix actionable PR feedback from inline threads, review bodies, and general comments, publish fixes, wait for CI, and run bounded fresh-agent review cycles when asked to iterate a pull request through review.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Before work, read [the lifecycle procedure](../../../common/lifecycle.md), then run its entry command with `--host claude`. Retain its returned token and follow its structured action. Always run its completion command with that token in a finalization step, including on failure or interruption.

Read [the coordinator entry](../../../runtime/dk-pr-iter/procedure.md), then read each linked stage contract before acting in that stage. Give child agents only their assigned role contracts and pinned evidence paths. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.

On preparation refresh, select the active artifact with `prepare --previous-preparation FILE`; follow [the incremental preparation contract](../../../runtime/dk-pr-decisions/references/preparation.md). Preserve review budgets and exact session/coverage gates.
