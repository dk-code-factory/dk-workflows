---
name: dk-architecture-review
description: Examine architecture alternatives for the incoming PR architecture gate. Supplies examination guidance; the owning workflow controls selection, persistence and publication.
license: MIT
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Examine only the prepared architecture assignment. Use the framework's mandatory
checklist and [architecture contract](../../../docs/architecture-review.md).
This prompt supplies what to examine; it does not schedule tasks, choose a
solution, set scores/weights, store state or publish. Return original analysis
and source evidence to the already running native coordinator.

Start from functional requirements, operational requirements and repository
constraints. Compare the PR with materially distinct reasonable approaches,
including the smallest viable alternative. Explain infeasible approaches and
coverage limits. Favor demonstrated needs over hypothetical scale. Examine
component boundaries, interfaces, dependencies, state ownership, concurrency,
compatibility and security. Consider partial failure, retry idempotence,
migration, rollback and recovery. Account for maintainability, deployment and
operational complexity, including API dollar cost where relevant.

Describe trade-offs and consequences, including unhandled edge cases. Estimate
production LOC as added plus deleted lines relative to the pinned diff base;
include executable infrastructure configuration and exclude tests, documentation
and generated output. Explain complexity and intrusiveness. Use shared anchored
ratings and evidence under the assigned rubric. Identify material doubts and
estimate sensitivity. Do not replace feasibility requirements with a high score.
Use a diagram only when it clarifies interactions. A rejected approach needs a
specific reason. Keep the full comparison private; the coordinator prepares the
settled design reasoning for Decisions.

Adapted from Jeff Allan's [architecture-designer](https://github.com/jeffallan/claude-skills/blob/078a25cc5bfd8011459be7b91d004e900c1d02d3/skills/architecture-designer/SKILL.md),
MIT licensed. The [original license and attribution](../../../docs/licenses/architecture-designer-MIT.txt) are retained.
