---
name: dk-pr-decisions
description: Collect and reconcile settled PR decisions, then publish them when authorized with source references.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Resolve configuration for `dk-workflows:dk-pr-decisions` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Use [shared PR evidence](../../../docs/pr/supplied-evidence.md) for analysis and live verification.

Read the complete PR discussion, user instructions and current Decisions. Distinguish settled choices from suggestions, open questions and generated progress. Save exact sources. Ask the user about important unresolved scope choices. Base proposals on evidence and label them as proposals.

Apply the effective `reconciliation_prompt` under [decision reconciliation](../../../docs/decision-reconciliation.md). Reconcile advised/selected alternatives with sourced user and maintainer/admin decisions before dependent edits. Interpret arbitrary user instructions as governing revisions, conditions or exceptions within their scope; no special phrase is required. Reuse applicable sourced user resolutions before asking about a conflict. Keep minimal resolution evidence in the existing private managed result, reference original selections and pass effective commitments and exceptions into fixing and later handoffs. Unresolved conflicts leave dependent work incomplete.

Follow [decision language review](../../../runtime/dk-pr-decisions/references/language.md) for the configured `language_review_passes`, at most three passes. Use the configured `section_heading` when locating and publishing the bounded decision section; consumers read this setting from this skill. When authorized, use `scripts/publish.py` to update only Decisions. Preserve the rest of the title and body. Return structured decisions, open questions, sources and original instruction snapshots.

Keep generated alternatives analysis and selection in private managed results.
Do not add an Alternatives decisions subsection or copy alternatives comparisons,
scores, alternatives or selection rationale into the PR description. Continue
recording sourced scope and behavior agreements concisely. Preserve existing
published decisions unless their revision is authorized. Follow the
[alternatives evidence boundary](../../../docs/alternatives-review.md#fix-workflow-once-per-pr).
