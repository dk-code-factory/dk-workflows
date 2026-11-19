---
name: dk-pr-review
description: Review another person's PR, verify findings independently and post confirmed issues. Approve or merge only when separately authorized.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Apply the canonical [alternatives history boundary](../../../docs/alternatives-review.md#manual-alternatives-review) across both PR routes, sessions, heads and guidance versions. Only a direct user invocation of `dk-workflows:dk-pr-review-alternatives` permits a fresh PR comparison. Preserve original choices and unfinished work; historical context still needs current requirements/implementation alignment and independent review, CI and publication checks.

Set PLUGIN_ROOT to this plugin's root directory. Use the authoritative [managed lifecycle and staged guidance](../../../docs/task-state.md#lifecycle-authority-and-guidance-loading) for every task and [the incoming review recipe](../../../runtime/pr-review.md) for stage decisions, applicability, consumed evidence and acceptance. The live configured lists select invocations. Discovery requires a separate independent native verification pass when candidates exist.

Resolve configuration for `dk-workflows:dk-pr-review` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Use the read-only [next-action interface](../../../docs/next-actions.md) for bounded current obligations and original recovery references. Native execution, semantic decisions and existing prepare/finish, CI and publication checks remain authoritative; the projection grants no execution or write permission.

For a prepared alternatives stage assignment, execute only its generated stage guidance and checklist from the [alternatives contract](../../../docs/alternatives-review.md). Return original evidence; do not coordinate tasks, publish or recursively invoke this workflow.

For an explicit verification-only assignment, perform only the independent finding-verification procedure on the supplied candidates and pinned context, then return dispositions and evidence. Do not execute workflow tasks/finalizers, edit, publish or recursively coordinate another review.

After collecting requirements and repository evidence, settle [initial assessment and architectural alternatives](../../../docs/alternatives-review.md#incoming-workflow-once-per-pr) once per PR through `alternatives.py plan --workflow review`. Report recommendations without editing code, commits or the PR description. The alternatives contract owns inventory, comparisons, selection, historical reuse and explicit reassessment. Preserve alternatives-only rejection verification/publication and later-head recovery; original design context never replaces current independent review or grants permission. Fork CI approval follows configured permissions; PR approval and merging need separate authorization.

Discovery and independent verification require accepted [lifecycle completion checks](../../../docs/task-state.md#completion-checks); report optional omissions and retain original evidence.

Follow [simplification review](../../../docs/simplification.md): use the live owning workflow selector for managed reviewer composition, retain independent ordinary coverage, and assess bounded recommendations through the existing verification or fix stages. Concrete maintainability benefits do not require a functional bug. Reject or defer insufficient-benefit or out-of-scope recommendations; no broad refactor is authorized.

The default CI finalizer learns flaky-test evidence from failures on this PR and named passes after those failures. Follow [flake learning](../../../docs/ci-flakes.md); repository-wide CI history scans belong to explicitly requested `dk-workflows:dk-ci-flakes` discovery. This does not authorize code fixes or issue creation.

Apply [finding follow-ups](../../../docs/pr/finding-followups.md) to historical reports from bots, people and earlier runs. Independent current-head verification precedes any reminder; after at least one new PR commit since the latest substantive request, reuse an equivalent open inline thread when authorized; otherwise report on a verified current changed line through ordinary review publication. The old report location never selects a top-level reminder. Recent equivalent requests, completed fixes, resolved/rejected/uncertain and explicitly accepted/deferred findings receive no reminder. Formatting-only bot-summary edits retain each unchanged finding's age. For missing/rewritten anchors, require finding-specific evidence of no equivalent open thread or current request, then use an authorized changed-line review candidate; an old bot summary alone cannot suppress a confirmed finding. Assess the review route when no authorized inline-thread reply is available, without expanding permissions. Use deterministic source/finding identities and revision-backed anchors from inspection/planning; never supply a SHA or prose explanation as anchor proof. Preserve original publication journals, including nested review findings; age grants neither posting permission nor clean-review readiness.

Resolve the selected role through [bundled behavior](../../../docs/behavior.md) before assignment or direct execution. The main role is `default`; [its shipped prompt](prompts/default.md) is inspectable even with an override. Execute only the resolved role behavior, followed by its resolved addition and separate original assignment instructions. The remaining entry and referenced execution contracts govern every replacement.
