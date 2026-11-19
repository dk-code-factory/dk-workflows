---
name: dk-pr-feedback-address
description: Verify and fix actionable PR feedback from inline threads, reviews and general comments.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before workflow configuration consumption, follow [automatic configuration upgrades](../../../docs/configuration-upgrades.md). Resume the original workflow with its permissions after an upgrade; ordinary baseline reads require no delegation.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Resolve configuration for `dk-workflows:dk-pr-feedback-address` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Use [shared PR evidence](../../../docs/pr/supplied-evidence.md) for analysis and live verification.

For caller-supplied alternatives and decision resolutions, follow [decision reconciliation](../../../docs/decision-reconciliation.md). Honor complete user instructions, scoped revisions, conditions and exceptions; do not restore superseded or excluded choices from historical Decisions. Reuse applicable sourced answers, leave unresolved governing conflicts unfinished, and implement assigned reconciled changes with current code and relevant validation evidence or an already-satisfied disposition. Carry original resolution references into the existing result and subsequent handoffs.

Apply [feedback response obligations](../../../docs/pr/feedback-responses.md) to each request. Questions for other participants and discussion requiring no author response need no author reply or resolution. Preserve separately actionable author requests in mixed threads and explicit user instructions to reply. Carry source references and the reason for any no-reply assessment into the existing result/dispositions so later reply tasks do not invent an outstanding answer.

Before implementation, follow [preference retrieval and handoffs](../../../docs/preferences.md#workflow-handoffs-and-reuse) in `implementation` mode. Use a current complete caller handoff or resolve preferences and evaluate conditions directly. Preserve records and applicable paths. Refresh when paths, relevant records or condition context change, including before edits expand to new paths. Include the selection and condition decisions in managed task inputs.

For historical reports, use [deterministic finding resolution](../../../docs/pr/finding-followups.md). Select returned source/finding IDs; never choose an anchor SHA or use prose as proof. Unsupported or unavailable source history remains unknown under the existing authorized fallback.

Record each finding's disposition with evidence and source links. Follow the caller's permissions for pushes, replies and thread resolution. Local fixes alone do not authorize remote changes. Use the publication and reply skills for authorized remote changes. Return tested fixes, dispositions and remaining blockers.

For managed feedback and reviewer-finding fixes, use the prepared checklist to check every finding disposition, assigned deliverable and relevant test. Supply complete initial checklist evidence or finish the caller's recheck with the same agent before reporting accepted completion. Follow [completion checks](../../../docs/task-state.md#completion-checks).

Follow [simplification review](../../../docs/simplification.md): use the live owning workflow selector for managed reviewer composition, retain independent ordinary coverage, and assess bounded recommendations through the existing verification or fix stages. Concrete maintainability benefits do not require a functional bug. Reject or defer insufficient-benefit or out-of-scope recommendations; no broad refactor is authorized.

Resolve the selected role through [bundled behavior](../../../docs/behavior.md) before assignment or direct execution. The main role is `default`; [its shipped prompt](prompts/default.md) is inspectable even with an override. Execute only the resolved role behavior, followed by its resolved addition and separate original assignment instructions. The remaining entry and referenced execution contracts govern every replacement.

Run relevant checks and configured `additional_validation_commands` as argument arrays in the affected checkout. These commands must never launch Codex or Claude Code. Preserve settled decisions and deferred work; return original tested dispositions and evidence.
