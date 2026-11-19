---
name: dk-pr-decisions
description: Collect and reconcile settled PR decisions, then publish them when authorized with source references.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before workflow configuration consumption, follow [automatic configuration upgrades](../../../docs/configuration-upgrades.md). Resume the original workflow with its permissions after an upgrade; ordinary baseline reads require no delegation.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Resolve configuration for `dk-workflows:dk-pr-decisions` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Use [shared PR evidence](../../../docs/pr/supplied-evidence.md) for analysis and live verification.

When default behavior is selected, include the effective `reconciliation_prompt` under [decision reconciliation](../../../docs/decision-reconciliation.md). Reconcile advised/selected alternatives with sourced user and maintainer/admin decisions before dependent edits. Interpret arbitrary user instructions as governing revisions, conditions or exceptions within their scope; no special phrase is required. Reuse applicable sourced user resolutions before asking about a conflict. Keep minimal resolution evidence in the existing private managed result, reference original selections and pass effective commitments and exceptions into fixing and later handoffs. Unresolved conflicts leave dependent work incomplete.

Follow [decision language review](../../../runtime/dk-pr-decisions/references/language.md) for the configured `language_review_passes`, at most three passes. Use the configured `section_heading` when locating and publishing the bounded decision section; consumers read this setting from this skill. When authorized, use `scripts/publish.py` to update only Decisions. Preserve the rest of the title and body. Return structured decisions, open questions, sources and original instruction snapshots.

Keep generated alternatives analysis and selection in private managed results.
Do not add an Alternatives decisions subsection or copy alternatives comparisons,
scores, alternatives or selection rationale into the PR description. Continue
recording sourced scope and behavior agreements concisely. Preserve existing
published decisions unless their revision is authorized. Follow the
[alternatives evidence boundary](../../../docs/alternatives-review.md#fix-workflow-once-per-pr).

Resolve the selected role through [bundled behavior](../../../docs/behavior.md) before assignment or direct execution. The main role is `default`; [its shipped prompt](prompts/default.md) is inspectable even with an override. Execute only the resolved role behavior, followed by its resolved addition and separate original assignment instructions. The remaining entry and referenced execution contracts govern every replacement.
