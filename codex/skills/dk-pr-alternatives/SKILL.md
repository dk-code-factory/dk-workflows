---
name: dk-pr-alternatives
description: Inventory branch architecture including inherited decisions, then compare questionable decisions under the shared PR rubric.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this installed plugin root.

The controller injects the common assessment prompt into native assignments and reuses accepted assessment evidence. There is no separate assessment skill to invoke. This skill owns `selection.mode` and the remaining `selection` options in its configuration; the default is `semi-manual`. Configure issue and PR alternatives independently. Pass invocation-local skill options to the helper with `--alternatives-options`.

Follow [decision reconciliation](../../../docs/decision-reconciliation.md). Consume effective agreements and arbitrary user-directed revisions, conditions and exceptions as scoped inputs. Label conflicts in advised or selected approaches and return them to the coordinator before dependent implementation. Reference original selections and applicable sourced resolutions; do not create duplicate decision records. Automatic ranking still does not prompt; separate unresolved agreement conflicts follow the coordinator's reconciliation policy. This read-only assessment does not authorize edits.

For a prepared managed stage, execute only its generated assignment and checklist, then return original evidence. Do not restart the full process or recursively invoke this skill. Unprepared invocations follow the procedure below.

Resolve [skill-owned configuration](../../../docs/skill-configuration.md), including this skill's [defaults](config.json) and [schema](config.schema.json). Use the already-running native session. Helpers construct assignments, claim tasks and retain original results under the [shared alternatives contract](../../../docs/alternatives-review.md). Unavailable native execution or required evidence leaves affected work incomplete. Never start another model session.

Consume the original shared assessment. First inventory architectural decisions throughout the branch, including inherited decisions. Label each inherited, introduced or materially_changed; identify scope, applicable rubric, sources and whether the decision is questionable. Judge questionability using the rubric and optional evaluation guidance, without a predefined defect checklist. Explain inventory coverage and limits.

Then compare materially distinct alternatives only for questionable decisions. Include retaining the current implementation. Evaluate every option against the same pinned PR target revision, including implementation work already on the branch. Use the same persisted rubric across every option in a comparison. Native assessment, comparison and selection run in the current session; no separate selector agent is needed.

Keep each decision comparison and sourced selection as separate managed tasks. Resume incomplete work without repeating completed siblings. Check interactions among all selected approaches before downstream handoff. Automatic selection never prompts or implements an unresolved decision; manual requires explicit sourced choice; semi-manual asks only when automatic checks cannot settle the choice. Pending choices keep dependent stages blocked.

In dk-pr-review, report selected approaches without editing code or commits. Preserve existing independent verification and publication boundaries for design-change recommendations. Selection introduces no defect-oriented gate and grants no publication permission. Automatic initial analysis runs once per PR; explicit manual requests refresh requested analysis while reusing unchanged shared assessment. Keep comparisons and scores private.
