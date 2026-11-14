---
name: dk-pr-review
description: Review another person's PR, verify findings independently and post confirmed issues. Approve or merge only when separately authorized.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Reuse the saved repository choice or initialize it through `dk-workflows:dk-repo-onboard` before adoption; preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to this plugin's root directory. Follow [the incoming review recipe](../../../runtime/pr-review.md) and [task state and handoffs](../../../docs/task-state.md). Read this skill's resolved `architecture`, `tasks`, `finalizers`, `limits` and `permissions` through [skill-owned configuration](../../../docs/skill-configuration.md). Execute each list in order, honoring parallel groups, while preserving the recipe's applicability, independent verification, freshness and completion requirements. Use managed skill-occurrence descriptors for list occurrences; the recipe owns repetition. Use `dk-workflows:dk-pr-reviewers-run` to find issues, then a separate native verification pass before publication.

Resolve configuration for `dk-workflows:dk-pr-review` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

For a prepared architecture stage assignment, execute only its generated stage guidance and checklist from the [architecture contract](../../../docs/architecture-review.md). Return original evidence; do not coordinate tasks, publish or recursively invoke this workflow.

For an explicit verification-only assignment, perform only the independent finding-verification procedure on the supplied candidates and pinned context, then return dispositions and evidence. Do not execute workflow tasks/finalizers, edit, publish or recursively coordinate another review.

Keep original reviewer outputs, dispositions and sources, pinned head and target, native agent handles and publication receipts across interruptions. This workflow permits only the restricted Architecture decisions description edit under `permissions.edit_architecture_decisions`; it does not authorize code or commit changes. Complete the [architecture gate](../../../docs/architecture-review.md) before every configured task or parallel group. Pending choices block ordinary review; a verified architecture-only rejection stops without ordinary reviewers or finalizers. Disabled architecture bypasses all architecture stages and publications, retaining evidence. It normally posts verified findings; follow analysis-only instructions and saved policy. Fork workflow approval runs automatically when ready under `dk-workflows:dk-pr-review` option `permissions.approve_fork_ci` (default true), before waiting for CI; when selected in the finalizer list, use `dk-workflows:dk-ci-approve` with original review evidence and current coverage. PR approval and merging each require separate explicit or saved policy authorization and current completion checks.

Give discovery and independent verification agents their prepared checklists. Accept reports only after the shared completion checks; retain original evidence and report optional omissions. Follow [completion checks](../../../docs/task-state.md#completion-checks).

Follow [simplification review](../../../docs/simplification.md): use the live owning workflow selector for managed reviewer composition, retain independent ordinary coverage, and assess bounded recommendations through the existing verification or fix stages. Concrete maintainability benefits do not require a functional bug. Reject or defer insufficient-benefit or out-of-scope recommendations; no broad refactor is authorized.

The default CI finalizer learns flaky-test evidence from failures on this PR and named passes after those failures. Follow [flake learning](../../../docs/ci-flakes.md); repository-wide CI history scans belong to explicitly requested `dk-workflows:dk-ci-flakes` discovery. This does not authorize code fixes or issue creation.
