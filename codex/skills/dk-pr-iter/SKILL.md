---
name: dk-pr-iter
description: Identify the PR author and your accounts, then fix your PR or review another person's PR under saved publication policies.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Set PLUGIN_ROOT to this plugin's root directory. Follow [PR routing](../../../runtime/pr.md). Follow an explicit request to fix or review. Otherwise run `scripts/pr_route.py --pr PR_URL`, report the selected workflow and invoke `dk-pr-fix` or `dk-pr-review`. Pass original instructions, identity evidence and saved run state. Unknown identity blocks automatic routing. Routing grants no extra permission to publish, approve or merge.

For a combined request such as "check out this PR, run dk-pr-iter, then merge it", carry out the requested checkout safely and retain the explicit merge instruction and exact canonical PR through routing, downstream handoffs, finalization and resume. Pass that permission to either selected workflow. After it completes, fulfill the merge request through `dk-pr-publish` with current clean review, verification, successful CI and satisfied repository requirements. Default `incoming_review.merge:false` and the fix workflow's default no-merge scope do not revoke the user's separate instruction. Do not ask again merely because the workflow was routed or resumed. A later narrower instruction overrides the affected permission; report a concrete blocker if readiness fails. Checking out or iterating alone still grants no merge permission, and merging grants no permission to post `APPROVE`.

Preserve the retained run across sessions. Distinguish interruption resume from a renewed explicit user request for review; pass its exact instruction, source and stable request id downstream. An exhausted run can receive a bounded continuation only under [retained budgets](../../../docs/task-state.md#retained-budgets), without resetting history or treating a fresh session alone as authorization.
