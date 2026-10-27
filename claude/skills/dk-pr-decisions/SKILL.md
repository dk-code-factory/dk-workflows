---
name: dk-pr-decisions
description: Collect PR discussion and description decisions, suggest choices from code or description, explicitly publish or replace entries in the PR Decisions section, and prepare reconciled engineering boundaries for iteration or standalone review.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Before work, read [the lifecycle procedure](../../../common/lifecycle.md), then run its entry command with `--host claude`. Retain its returned token and follow its structured action. Always run its completion command with that token in a finalization step, including on failure or interruption.

Read [the shared procedure](../../../runtime/dk-pr-decisions/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.

On preparation refresh, select the active artifact with `prepare --previous-preparation FILE`; follow [the incremental preparation contract](../../../runtime/dk-pr-decisions/references/preparation.md). Preserve review budgets and exact session/coverage gates.

Fresh workflows use preparation v3, session v2, review-result v3 and completion-verification v2. Preserve both preparation and engineering IDs. Use returned private workflow paths for all generated evidence and child handoffs. Operational refresh may preserve engineering identity; validate current evidence and retained reviewer bindings before reuse. Resume legacy active runs only through their retained runtime. A Decisions `metadata_conflict` blocks dependent work: read its retained proposal and explicitly reconcile; never merge or replay publication automatically.
