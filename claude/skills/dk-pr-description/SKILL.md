---
name: dk-pr-description
description: Draft or update PR titles and descriptions using the diff, templates and agreed scope.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Resolve configuration for `dk-workflows:dk-pr-description` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Keep generated alternatives analysis and selection in private managed results. Do not add an alternatives review section or copy its comparisons, scores, alternatives or selection rationale into the description.

Draft locally and review the text. Publish authorized edits with `scripts/publish.py` after checking the current head, title and body. Follow [the decision record](../../../runtime/dk-pr-description/references/decisions.md) when handling Decisions.

Use [the field conventions](../../../runtime/dk-pr-description/references/field-schema.md) when repository formatting rules leave a choice open.

Resolve the selected role through [bundled behavior](../../../docs/behavior.md) before assignment or direct execution. The main role is `default`; [its shipped prompt](prompts/default.md) is inspectable even with an override. Execute only the resolved role behavior, followed by its resolved addition and separate original assignment instructions. The remaining entry and referenced execution contracts govern every replacement.

Honor `update_title` and `update_body`, preserving disabled fields, Decisions and unrelated sections. Repository formatting governs templates and fallback sections. Publish only authorized reviewed text through the current-head publication contract.
