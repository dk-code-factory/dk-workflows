---
name: dk-pr-fix
description: Find and fix issues in your PR through review, feedback, publication, commit cleanup and final CI, preserving progress and budgets.
---

Resolve PLUGIN_ROOT from this installed skill location. Use the current agent and its native skill tools. Establish ownership with `scripts/pr_route.py --pr PR_URL` under [PR routing](../../../runtime/pr.md). Proceed for your PR, or an explicit instruction to maintain another author's PR; otherwise route to `dk-pr-review` before coding or publication. Ownership classification does not establish branch write/rewrite permission.

Read [task state and handoffs](../../../docs/task-state.md) before managing an invocation. Read [the fix recipe](../../../runtime/pr-iter.md) and follow each stage. Preserve the run id and counters on resume. A cache hit can retain findings and never grants publication authority.
