---
name: dk-pr-fix
description: Review and fix your PR, publish changes and run configured finalizer skills while preserving progress and budgets.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before workflow configuration consumption, follow [automatic configuration upgrades](../../../docs/configuration-upgrades.md). Resume the original workflow with its permissions after an upgrade; ordinary baseline reads require no delegation.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Apply the canonical [alternatives history boundary](../../../docs/alternatives-review.md#manual-alternatives-review) across both PR routes, sessions, heads and guidance versions. Only a direct user invocation of `dk-workflows:dk-pr-review-alternatives` permits a fresh PR comparison. Preserve original choices and unfinished work; historical context still needs current requirements/implementation alignment and independent review, CI and publication checks.

Set PLUGIN_ROOT to this plugin's root directory. Use the current agent and native skill tools. Check ownership with `scripts/pr_route.py --pr PR_URL` under [PR routing](../../../runtime/pr.md). Proceed for your PR or when explicitly asked to maintain another author's PR. Otherwise invoke `dk-workflows:dk-pr-review` before editing code or publishing. PR ownership alone does not grant permission to write or rewrite the branch.

Resolve configuration for `dk-workflows:dk-pr-fix` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Use the read-only [next-action interface](../../../docs/next-actions.md) for bounded current obligations and original recovery references. Native execution, semantic decisions and existing prepare/finish, CI and publication checks remain authoritative; the projection grants no execution or write permission.

Before alternatives comparison and dependent implementation, follow [decision reconciliation](../../../docs/decision-reconciliation.md) using `dk-pr-decisions`' effective `reconciliation_prompt`. Interpret the complete user instruction, including arbitrary revisions, conditions and exceptions to earlier PR decisions, without keyword matching or repeated confirmation. Reuse applicable sourced user answers; otherwise the default asks about conflicts with settled user or maintainer/admin decisions and blocks dependent edits. Supply original resolution/instruction references and effective commitments to the existing fixing agent and later stages. Do not let historical Decisions silently restore a revised or excluded choice. This coordinator duty applies even when Decisions is omitted from the selected plan; it does not invoke omitted skills or finalizers.

For a prepared alternatives stage assignment, execute only its generated guidance and checklist under the [alternatives contract](../../../docs/alternatives-review.md#fix-workflow-once-per-pr). Return original evidence without invoking this workflow recursively or publishing.

For retained historical fix-stage records blocked by changed guidance or removed installation paths, follow [historical fix reassessment](../../../docs/alternatives-review.md#historical-fix-reassessment). Use `dk.py reassess` only with the exact original sourced user instruction authorizing the affected stage and a rebuilt current definition. Complete affected dependents in order within that instruction's scope; preserve original results, claims, run budgets and publication recovery. Reassessment grants no publication, merge or budget renewal permission.

After collecting requirements and repository evidence, settle [initial assessment and alternatives](../../../docs/alternatives-review.md#fix-workflow-once-per-pr) once per PR through `alternatives.py plan --workflow fix`. Pass the original choices and interaction evidence into authorized implementation, apply assigned changes and validate them. The alternatives contract owns selection, historical reuse and explicit reassessment; current alignment, review, CI and publication checks remain live.

Use the authoritative [managed lifecycle and staged guidance](../../../docs/task-state.md#lifecycle-authority-and-guidance-loading) for every task. Follow [the fix recipe](../../../runtime/pr-iter.md) for stage decisions, applicability, consumed evidence and acceptance. The live configured lists select invocations; cached analysis grants no publication permission.

Before committing fixes or entering selected commit cleanup, follow [local commit preparation](../../../docs/pr/commit-preparation.md), including relevant pre-existing staged and unstaged edits under the existing commit authorization. Refresh affected review coverage after code changes.

After review/fix work, follow selected [finalizers](../../../docs/finalizers.md), loading their detail when needed. Custom and empty lists remain authoritative. Apply configured `completion_instructions` within the selected plan; final live checks and reporting remain coordinator duties.

For a selected publisher finalizer, use [dk-pr-approvers](../dk-pr-approvers/SKILL.md) to return its scoped `review_requests` override or discover GitHub review/re-review recipients when none is set, preserving explicit opt-outs. See [repository review selections](../../../docs/configuration.md#repository-review-selections). Pass the final published head, caller permissions and the same upstream/local publisher options. This selection is independent of the runner's native review skills; the publisher owns its supporting invocation.

The default CI finalizer learns flaky-test evidence from failures on this PR and named passes after those failures. Follow [flake learning](../../../docs/ci-flakes.md); repository-wide CI history scans belong to explicitly requested `dk-workflows:dk-ci-flakes` discovery.

Retain a separate explicit merge request supplied directly or through `dk-workflows:dk-pr-iter`, including "check out, iterate, then merge". After the fix workflow and finalization complete, fulfill it through `dk-workflows:dk-pr-publish` under [merge publication](../../../docs/pr/publication.md). Require current clean review, verification, successful CI and satisfied repository requirements even when custom finalizers omit those checks. Default no-merge scope limits permission inferred from iteration alone; it does not revoke that explicit request or require another confirmation. Preserve the exact PR and instruction on resume, subject to later narrower instructions.

Before declaring success, satisfy the lifecycle's [managed workflow completion](../../../docs/task-state.md#completion-checks), including for empty finalizers. Report selected-plan completion separately from current PR readiness.

Follow [simplification review](../../../docs/simplification.md): use the live owning workflow selector for managed reviewer composition, retain independent ordinary coverage, and assess bounded recommendations through the existing verification or fix stages. Concrete maintainability benefits do not require a functional bug. Reject or defer insufficient-benefit or out-of-scope recommendations; no broad refactor is authorized.

Resolve the selected role through [bundled behavior](../../../docs/behavior.md) before assignment or direct execution. The main role is `default`; [its shipped prompt](prompts/default.md) is inspectable even with an override. Execute only the resolved role behavior, followed by its resolved addition and separate original assignment instructions. The remaining entry and referenced execution contracts govern every replacement.
