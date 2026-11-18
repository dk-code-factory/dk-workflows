---
name: dk-pr-review-alternatives
description: Manual entry point for alternatives-only PR review. Run and record a fresh review on demand, including after an earlier completed run.
metadata:
  short-description: Manual alternatives review
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this installed plugin root.

This historical manual entry point delegates the requested analysis to [dk-pr-alternatives](../dk-pr-alternatives/SKILL.md) under [manual alternatives review](../../../docs/alternatives-review.md#manual-alternatives-review). Use the controller-injected assessment and resolve selection settings on `dk-pr-alternatives`. A new explicit request refreshes requested inventory/comparisons while reusing unchanged shared assessment. Retain the original request identity on interruption and preserve completed siblings, native evidence, checklists and budgets. Manual invocation enables alternatives for this invocation without changing saved policy.

Report selected approaches without editing code or commits. Keep comparisons private. Use current alignment, independent native verification and existing publication permissions for any material mismatch. Stop after alternatives and never claim ordinary review, fork CI approval or merge readiness.
