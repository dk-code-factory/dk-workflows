---
name: dk-issue-alternatives
description: Compare whole-problem implementation approaches from issue requirements, optionally including an existing PR.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this installed plugin root.

The controller injects the common assessment prompt into native assignments and reuses accepted assessment evidence. There is no separate assessment skill to invoke. This skill owns `selection.mode` and the remaining `selection` options in its configuration; the default is `semi-manual`. Configure issue and PR alternatives independently. Pass invocation-local skill options to the helper with `--alternatives-options`.

For PR work, follow [decision reconciliation](../../../docs/decision-reconciliation.md). Consume effective agreements and arbitrary user-directed revisions, conditions and exceptions as scoped inputs. Label conflicts in advised or selected approaches and return them to the coordinator before dependent implementation. Reference original selections and applicable sourced resolutions; do not create duplicate decision records. Automatic ranking still does not prompt; separate unresolved agreement conflicts follow the coordinator's reconciliation policy.

For a prepared managed stage, execute only its generated assignment and checklist, then return original evidence. Do not restart the full process or recursively invoke this skill. Unprepared invocations follow the procedure below.

Resolve [skill-owned configuration](../../../docs/skill-configuration.md), including this skill's [defaults](config.json) and [schema](config.schema.json). Use the already-running native session. Helpers construct assignments, claim tasks and retain original results under the [shared alternatives contract](../../../docs/alternatives-review.md). Unavailable native execution or required evidence leaves affected work incomplete. Never start another model session.

Start with issue requirements, mandatory constraints and repository evidence. Consume the accepted shared assessment before comparing whole-problem approaches. Include an existing PR as an optional candidate, and cover materially distinct feasible approaches. Compare with the evaluation_prompt, applicable persisted rubric and this skill's selection policy. Judge trade-offs by demonstrated consequences and rubric, without a predefined architectural defect checklist.

Use controller-generated handoffs and weighted totals. Every option uses the same pinned target revision; do not discount work already implemented on a PR branch. Return evidence, feasible options, uncertainty and estimate sensitivity. Selection is performed by the current native session: automatic never prompts and leaves unresolved choices pending; manual requires an explicit sourced choice of a feasible evaluated option; semi-manual selects a qualifying winner and asks only when checks cannot settle it.

In dk-pr-fix, pass settled choices and required changes into authorized implementation tasks, apply the selected approaches and run relevant tests. Retaining the current approach needs no corresponding edit. Do not acquire new edit or publication permission from selection. Without a PR, use the same assessment, arithmetic and selection in memory; do not call PR task helpers with a fabricated PR URL. Explicit manual requests refresh requested comparisons while reusing unchanged assessment. This skill replaces the historical dk-alternatives-review whole-problem capability; old managed keys and claims remain recoverable.
