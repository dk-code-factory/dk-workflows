---
name: dk-pr-reviewers-run
description: Run selected PR reviewers on pinned inputs and reuse complete results when inputs are unchanged.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Reuse the saved repository choice or initialize it through `dk-workflows:dk-config` before adoption; preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to this plugin's root directory. Use the current agent and native skill tools. Read [task state and handoffs](../../../docs/task-state.md) before managing a task. Follow [the review recipe](../../../runtime/pr-reviewers.md). Standalone reviews and iteration share task identities and the cache.

Resolve configuration for `dk-workflows:dk-pr-reviewers-run` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Resolve required native roles and deeper-look commands from the installed skill, then run [evidence capability preflight](../../../docs/native-review.md#evidence-capability-preflight) before claims, review-cycle consumption or substantive dispatch. Unknown or unavailable required host observation support blocks new work; delegation alone is insufficient. Capture the same requirements before invocation. Accept completion only with the claim-bound host execution observations required by [native reviewer handoffs](../../../docs/native-review.md); a successful exit or complete report alone is insufficient. Preserve incomplete attempts and findings when required execution or its evidence cannot be established.

Use resolved `reviewers`, with no default selection. Honor `execution` (`parallel` or `sequential`) and `max_parallel`; null adds no configured cap beyond actual host capacity. Wait for every launched reviewer, retain successful siblings and report optional failures. Read lists with `skill-plan` and prepare managed reviewer occurrences with `selection:"reviewers"` using the exact invocation-local options.

When native child results are available but command logs are verified unavailable, use [reported driver completion](../../../docs/native-review.md#hosts-without-command-logs): each required pass reports its exact declared commands in its own original terminal output. Keep native identities and successful terminal observations mandatory, distinguish reported evidence from host-observed commands, and validate any supplied command observations strictly.

Pass each selected reviewer’s required/optional status and any per-task checklist overrides to preparation. Give it the prepared assignment and checklist, and obtain checklist acceptance before accepting coverage; handle any recheck with the same agent. Report every best-effort omission. Follow [completion checks](../../../docs/task-state.md#completion-checks).

Follow [simplification review](../../../docs/simplification.md): use the live owning workflow selector for managed reviewer composition, retain independent ordinary coverage, and assess bounded recommendations through the existing verification or fix stages. Concrete maintainability benefits do not require a functional bug. Reject or defer insufficient-benefit or out-of-scope recommendations; no broad refactor is authorized.
