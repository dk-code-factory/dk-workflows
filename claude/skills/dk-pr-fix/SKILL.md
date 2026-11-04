---
name: dk-pr-fix
description: Review and fix your PR, publish changes, organize commits and check final CI while preserving progress and budgets.
---

Set PLUGIN_ROOT to this plugin's root directory. Use the current agent and native skill tools. Check ownership with `scripts/pr_route.py --pr PR_URL` under [PR routing](../../../runtime/pr.md). Proceed for your PR or when explicitly asked to maintain another author's PR. Otherwise invoke `dk-pr-review` before editing code or publishing. PR ownership alone does not grant permission to write or rewrite the branch.

Read [task state and handoffs](../../../docs/task-state.md) before managing a task. Follow each stage of [the fix recipe](../../../runtime/pr-iter.md). Keep the run id and counters on resume. Cached results may contain findings; they never authorize publication.
