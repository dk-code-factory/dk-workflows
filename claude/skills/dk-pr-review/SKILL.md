---
name: dk-pr-review
description: Review another person's PR, verify findings independently and post confirmed issues. Approve or merge only when separately authorized.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Set PLUGIN_ROOT to this plugin's root directory. Follow [the incoming review recipe](../../../runtime/pr-review.md), [task state and handoffs](../../../docs/task-state.md) and effective `dk-config` settings. Use `dk-pr-reviewers-run` to find issues, then a separate native verification pass before publication.

Keep original reviewer outputs, dispositions and sources, pinned head and target, native agent handles and publication receipts across interruptions. This workflow does not authorize changes to the author's code, description or commits. It normally posts verified findings; follow analysis-only instructions and saved policy. Fork workflow approval runs automatically when ready under `incoming_review.approve_ci` (default true), before waiting for CI; use the configured `ci_approve` skill with original review evidence and current coverage. PR approval and merging each require separate explicit or saved policy authorization and current completion checks.
