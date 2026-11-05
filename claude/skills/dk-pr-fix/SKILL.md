---
name: dk-pr-fix
description: Review and fix your PR, publish changes and run configured finalizer skills while preserving progress and budgets.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Set PLUGIN_ROOT to this plugin's root directory. Use the current agent and native skill tools. Check ownership with `scripts/pr_route.py --pr PR_URL` under [PR routing](../../../runtime/pr.md). Proceed for your PR or when explicitly asked to maintain another author's PR. Otherwise invoke `dk-pr-review` before editing code or publishing. PR ownership alone does not grant permission to write or rewrite the branch.

Read [task state and handoffs](../../../docs/task-state.md) before managing a task. Follow each stage of [the fix recipe](../../../runtime/pr-iter.md). Keep the run id and counters on resume. Cached results may contain findings; they never authorize publication.

After review/fix work, follow [finalizers](../../../docs/finalizers.md). Run the effective `/skill/PLUGIN:SKILL` list in order, with nested lists as parallel groups. The default ending includes commit cleanup and final CI. Custom lists replace it; report omitted checks rather than silently running them. Final live checks and reporting remain coordinator duties.

Retain a separate explicit merge request supplied directly or through `dk-pr-iter`, including "check out, iterate, then merge". After the fix workflow and finalization complete, fulfill it through `dk-pr-publish` under [merge publication](../../../docs/pr/publication.md). Require current clean review, verification, successful CI and satisfied repository requirements even when custom finalizers omit those checks. Default no-merge scope limits permission inferred from iteration alone; it does not revoke that explicit request or require another confirmation. Preserve the exact PR and instruction on resume, subject to later narrower instructions.
