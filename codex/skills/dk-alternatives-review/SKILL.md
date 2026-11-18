---
name: dk-alternatives-review
description: Compatibility entry for dk-issue-alternatives and retained whole-problem managed assignments.
license: MIT
metadata:
  short-description: Whole-problem alternatives compatibility
---

For a new whole-problem comparison, execute `dk-workflows:dk-issue-alternatives` with the supplied issue requirements and optional candidate PR. For an architectural PR comparison, execute `dk-workflows:dk-pr-alternatives`. Retained prepared assignments continue through their legacy managed contract and original task keys.

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this installed plugin root.

For new whole-problem comparisons use [dk-issue-alternatives](../dk-issue-alternatives/SKILL.md), the renamed capability. Start from issue requirements and consume the shared purpose assessment, persisted rubric and evaluation guidance. For new architectural PR analysis use [dk-pr-alternatives](../dk-pr-alternatives/SKILL.md), which inventories inherited and changed decisions before comparing questionable choices.

Retained historical managed assignments still execute their exact prepared contract under [legacy recovery](../../../docs/alternatives-review.md#legacy-alternatives-recovery). Preserve task keys, claims, original evidence, siblings and budgets. Legacy whole-problem results do not establish new architectural inventory coverage. Architectural judgment uses the applicable rubric and supplied guidance; do not impose a predefined defect checklist on new analysis. Keep full comparisons private.

Adapted from Jeff Allan's [architecture-designer](https://github.com/jeffallan/claude-skills/blob/078a25cc5bfd8011459be7b91d004e900c1d02d3/skills/architecture-designer/SKILL.md), MIT licensed. The [original license and attribution](../../../docs/licenses/architecture-designer-MIT.txt) are retained.
