---
name: dk-pr-reply-review
description: Reply to GitHub PR feedback in inline threads or with source-linked responses to top-level comments and review bodies. Do not submit standalone reviews or general summaries.
---

Use this Codex session and native subagents when available. Use the loaded skill location to resolve the plugin root; do not depend on the working directory.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host codex begin --skill dk-pr-reply-review` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the shared procedure](../../../runtime/dk-pr-reply-review/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
