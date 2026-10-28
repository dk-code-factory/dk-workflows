---
name: dk-pr-review
description: Review a GitHub pull request using a pinned remote diff, settled decisions, repository rules, and configured reviewer skills.
---

Use this Codex session and native subagents when available. Use the loaded skill location to resolve the plugin root; do not depend on the working directory.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host codex begin --skill dk-pr-review` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the shared procedure](../../../runtime/dk-pr-review/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.

On preparation refresh, select the active artifact with `prepare --previous-preparation FILE`; follow [the incremental preparation contract](../../../runtime/dk-pr-decisions/references/preparation.md). Preserve review budgets and exact session/coverage gates.

Fresh workflows use preparation v3, session v2, review-result v3 and completion-verification v2. Preserve both preparation and engineering IDs. Use returned private workflow paths for all generated evidence and child handoffs. Operational refresh may preserve engineering identity; validate current evidence and retained reviewer bindings before reuse. Resume legacy active runs only through their retained runtime.

Before coding or review, follow [architecture delivery](../../../runtime/dk-architect/procedure.md). Every child handoff includes its recipient/path packet reference and fingerprint; refresh before expanding scope.
