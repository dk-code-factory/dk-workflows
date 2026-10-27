---
name: dk-review-stats
description: Read private reviewer quality statistics across local PR iteration runs, compare configured reviewer skills, and link to retained reports and evidence. Does not run reviews or change reviewer configuration.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host claude begin --skill dk-review-stats` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the shared procedure](../../../runtime/dk-review-stats/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
