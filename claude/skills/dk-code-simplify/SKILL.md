---
name: dk-code-simplify
description: Recommend bounded, behavior-preserving simplifications of supplied PR changes without editing code or publishing.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to this installed plugin root. Resolve standalone options through [skill-owned configuration](../../../docs/skill-configuration.md), using this skill's [explicit defaults](config.json) and [option schema](config.schema.json). Managed calls obey the owning workflow's governing priority and scope even when provider options or additional instructions differ. Follow [the simplification contract](../../../docs/simplification.md). Use the current native session and supported native delegation; helpers never execute models.

Review the pinned PR changes and supplied governing evidence. Return recommendations only. Do not edit code, run formatters, commit or publish. Preserve observable behavior, public contracts, compatibility, error handling, concurrency, recovery and necessary operational guarantees. Apply the repository's actual conventions; do not import language or framework preferences from examples elsewhere.

Look for avoidable nesting, redundant conditions or data transformations, confusing names, duplicated reasoning and abstractions that add more indirection than they remove. Explain the concrete reading, debugging or maintenance burden reduced. Retain useful abstractions, explicit control flow and separation of concerns. Fewer lines or personal style alone do not establish simplicity.

With `priority:change-first` (default), favor small, easily reviewed improvements, then resulting code clarity. With `priority:code-first`, favor clearer resulting code, then minimize the necessary change. Neither priority authorizes broader scope. With `allow_untouched_code:false` (default), recommend edits only within changed hunks. When true, include directly related surrounding code or call sites only when needed for a concrete simplification of this PR. Anchor every cycle to the supplied PR changes, never to a growing set of earlier recommendations. Exclude broad redesigns, repository-wide cleanup and unrelated modernization.

Return explicit completion and the existing findings/evidence/checklist contract. Each actionable finding identifies a changed-line anchor, the complexity reduced, proposed edit scope, priority tradeoff, behavior-preservation reasoning and verification needed. Separate out-of-scope opportunities into `deferred_observations`, not actionable findings. Return an empty findings list when no sufficiently useful bounded improvement exists. Functional bugs are not required for a concrete maintainability recommendation. The coordinator independently verifies findings or assesses them through the existing fix stages; these recommendations grant no edit or publication permission and do not replace ordinary review coverage.

This reviewer has no mandatory child passes or driver commands. Managed callers predeclare the installed version with an empty `passes` list and retain actual terminal native invocation observations, as required by [native reviewer handoffs](../../../docs/native-review.md). Receive and satisfy the prepared checklist before completion.

Behavioral references: [Anthropic's simplifier](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/code-simplifier/agents/code-simplifier.md) and [Sentry's simplifier](https://github.com/getsentry/skills/blob/main/skills/code-simplifier/SKILL.md). Their focus on clarity and behavior preservation informs this recommendation-only contract.
