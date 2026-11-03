---
name: dk-config
description: Manage private repository rules, scoped architecture rules, ordered PR reviewers, PR size thresholds, per-PR partition decisions and instructions, independent partition modes, and explicit flaky-test or pipeline exceptions at repository or user scope. Use when asked to list, add, change, remove, or refresh these settings.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host claude begin --skill dk-config` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the shared procedure](../../../runtime/dk-config/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.

Use `config pr-size list|set|reset` for the positive additions threshold and
independent `automatic|manual|disabled` evacuation/splitting modes. Report effective
values and sources; use repository or user scope as requested.

Use `dk.py --pr PR_URL config pr-partition show|enable|disable|instructions|clear-instructions|reset`
with PR context before the route. Record explicit whole-PR opt-outs and exact human
conditions; keep a declined proposal separate from disabling partitioning for the PR.
