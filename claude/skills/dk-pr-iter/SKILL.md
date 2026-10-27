---
name: dk-pr-iter
description: Verify and fix actionable PR feedback from inline threads, review bodies, and general comments, publish fixes, wait for CI, and run bounded fresh-agent review cycles when asked to iterate a pull request through review.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host claude begin --skill dk-pr-iter` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the coordinator entry](../../../runtime/dk-pr-iter/procedure.md), then read each linked stage contract before acting in that stage. Give child agents only their assigned role contracts and pinned evidence paths. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.

On preparation refresh, select the active artifact with `prepare --previous-preparation FILE`; follow [the incremental preparation contract](../../../runtime/dk-pr-decisions/references/preparation.md). Preserve review budgets and exact session/coverage gates.

Fresh workflows use preparation v3, session v2, review-result v3 and completion-verification v2. Preserve both preparation and engineering IDs. Use returned private workflow paths for all generated evidence and child handoffs. Operational refresh may preserve engineering identity; validate current evidence and retained reviewer bindings before reuse. Resume legacy active runs only through their retained runtime.
