---
name: dk-commit-message
description: Draft repository-native Git commit messages from the actual change, repository rules, templates, and recent human commits. Use when asked to write, draft, commit, amend, reword, or fix a commit message. Skip only when final text must be used verbatim.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host claude begin --skill dk-commit-message` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the shared procedure](../../../runtime/dk-commit-message/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
