---
name: dk-pr-reviewers-run
description: Run selected PR reviewers on pinned inputs and reuse complete results when inputs are unchanged.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Persist workflow state only when it reduces total token consumption, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to this plugin's root directory. Use the current agent and native skill tools. Read [task state and handoffs](../../../docs/task-state.md) before managing a task. Follow [the review recipe](../../../runtime/pr-reviewers.md). Standalone reviews and iteration share task identities and the cache.

Resolve configuration for `dk-workflows:dk-pr-reviewers-run` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Capture required native roles and deeper-look commands before invocation. Accept completion only with the claim-bound host execution observations required by [native reviewer handoffs](../../../docs/native-review.md); a successful exit or complete report alone is insufficient. Preserve incomplete attempts and findings when required execution or its evidence cannot be established.

Use resolved `reviewers`, with no default selection. Honor `execution` (`parallel` or `sequential`) and `max_parallel`; null adds no configured cap beyond actual host capacity. Wait for every launched reviewer, retain successful siblings and report optional failures. Read lists with `skill-plan` and prepare managed reviewer occurrences with `selection:"reviewers"` using the exact invocation-local options.
