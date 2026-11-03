---
name: dk-pr-iter
description: Verify and fix actionable PR feedback from inline threads, review bodies, and general comments, publish fixes, evacuate unrelated work and split oversized PRs into drafts under private settings, wait for CI, and run bounded fresh-agent review cycles when asked to iterate a pull request through review.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Begin the lifecycle with `python3 PLUGIN_ROOT/scripts/dk.py --host claude begin --skill dk-pr-iter` (pass common `--parent PARENT_TOKEN` when supplied). Retain `invocation` and use the returned `runtime_root` as PLUGIN_ROOT. Pass `--invocation INVOCATION_FILE` before each routed helper. For direct helpers, supply `DK_HOST`, `DK_UPDATE_PARENT` and `DK_CLI_REFERENCE` from that receipt on every process. Always run `python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE end` in finalization, including on failure. Follow returned diagnostics and actions. See [lifecycle context](../../../common/lifecycle.md) when needed.

Read [the coordinator entry](../../../runtime/dk-pr-iter/procedure.md), then read each linked stage contract before acting in that stage. Give child agents only their assigned role contracts and pinned evidence paths. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.

On preparation refresh, use `run-state refresh-preparation` with the active previous preparation, current context, verified Decisions and both complete feedback snapshots. Follow [operational refresh](../../../runtime/dk-pr-iter/references/operational-refresh.md). Preserve review budgets and exact session/coverage gates.

Fresh workflows use preparation v4, run contract v3, session v3, review-result v4, role-checkpoint v2 and reconciliation v3 and completion-verification v2. Preserve both preparation and engineering IDs. Use returned private workflow paths for all generated evidence and child handoffs. Operational refresh may preserve engineering identity; validate current evidence and retained reviewer bindings before reuse. Resume legacy active runs only through their retained runtime.

Before coding or review, follow [architecture delivery](../../../runtime/dk-architect/procedure.md). Every child handoff includes its recipient/path packet reference and fingerprint; refresh before expanding scope.

After initial feedback fixes, run the once-only architecture stage before allocating
review cycle 1. Follow [the stage contract](../../../runtime/dk-pr-iter/references/iteration-architecture.md).
Retain its result through all cycles, cleanup and completion; do not launch another
architecture reviewer in those stages. Continue current rule delivery to workers.

After preparation and every published code change, check the additions size gate
before CI, review allocation or completion. At 1,000 added lines by default,
follow [PR partitioning](../../../runtime/dk-pr-iter/references/iteration-partition.md).
Use one split worker; preserve review budgets and refresh scope after publication.
