---
name: dk-pr-split-commits
description: Delegate splitting or full-series cleanup of a pull request's commits to one separate agent, preserving the final tree, or partitioning oversized PR scope into verified draft PRs under iteration settings.
---

Use this Codex session and native subagents when available. Use the loaded skill location to resolve the plugin root; do not depend on the working directory.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host codex begin --skill dk-pr-split-commits` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the shared procedure](../../../runtime/dk-pr-split-commits/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.

Before coding or review, follow [architecture delivery](../../../runtime/dk-architect/procedure.md). Every child handoff includes its recipient/path packet reference and fingerprint; refresh before expanding scope.

For a PR partition handoff, retain the same single-worker ownership contract and
use [the partition path](../../../runtime/dk-pr-split-commits/references/partition-prs.md).
The separate `partition-prs` helper does not change standalone split/regroup guards.

Partition handoffs include the current per-PR policy fingerprint, exact human
instructions and source. Apply those conditions and record a reasoned assessment;
never turn a single declined proposal into an inferred whole-PR opt-out.
