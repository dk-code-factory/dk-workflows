---
name: dk-pr-split-commits
description: Delegate splitting or full-series cleanup of a pull request's commits to one separate agent, preserving the final tree and pushing the rewritten series when requested.
---

Use this Codex session and native subagents when available. Use the loaded skill location to resolve the plugin root; do not depend on the working directory.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host codex begin --skill dk-pr-split-commits` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the shared procedure](../../../runtime/dk-pr-split-commits/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
