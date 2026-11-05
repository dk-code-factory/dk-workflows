---
name: dk-pr-reviewers-run
description: Run selected PR reviewers on pinned inputs and reuse complete results when inputs are unchanged.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Set PLUGIN_ROOT to this plugin's root directory. Use the current agent and native skill tools. Read [task state and handoffs](../../../docs/task-state.md) before managing a task. Follow [the review recipe](../../../runtime/pr-reviewers.md). Standalone reviews and iteration share task identities and the cache.

Capture required native roles and deeper-look commands before invocation. Accept completion only with the claim-bound host execution observations required by [native reviewer handoffs](../../../docs/native-review.md); a successful exit or complete report alone is insufficient. Preserve incomplete attempts and findings when required execution or its evidence cannot be established.
