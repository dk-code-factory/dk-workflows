---
name: dk-ci
description: Check CI for the current PR commit, preserve wait deadlines and classify failures using verified flaky tests or exceptions.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Resolve configuration for `dk-workflows:dk-ci` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Run `python3 PACKAGE_ROOT/runtime/dk-ci/scripts/ci_wait.py --pr PR_URL --head SHA`, using the full lowercase head SHA. Keep one observer per PR and head across interruptions. Use the resolved timeout, poll interval, classification timeout, absent-check grace and pending-extension settings. Pass invocation-local options with `--options FILE`; explicit CLI flags are deliberate overrides. Resume the same process when a tool yields. Read the current head and check attempts from GitHub.

The helper returns `success`, `known_flakes`, `accepted_exceptions`, `failure`, `timeout`, `no_checks` or `head_changed`. Only the first three satisfy CI, with current evidence. Restore access before using `--retry-denied`. Diagnose failures from the helper's retained current-PR evidence and code inspection. Preserve the caller's repair and rerun budgets.

Default [flake learning](../../../docs/ci-flakes.md) records named failures on the current PR and looks up relevant open issues; it never scans repository Actions history automatically. After an observed failure, successful checks at the same head can supply named pass evidence. Fresh successful heads do no flake investigation. Targeted issue lookups use `dk-workflows:dk-ci-flakes` scoped `max_issues`; history limits belong to that skill's explicit active discovery. Preserve supplied `--upstream` and pass local flake overrides separately with `--flake-options FILE`. Use `dk-workflows:dk-ci-flakes` for saved-evidence inspection or an explicitly requested broader scan. Zero repair or rerun budgets disable those attempts without weakening CI acceptance.

Read the final JSON object's `result` on stdout: accepted outcomes exit 0; other terminal outcomes exit 1. Progress JSON goes to stderr. Helper exceptions exit 2 with `{"result":"error","message":"..."}` on stderr and no final stdout result. Preserve the error and treat CI as blocked.

For failed CI, follow [CI recovery](../../../docs/ci-recovery.md). Inspect every failed workflow's jobs and relevant logs against the PR diff. Automatically recover a supported unrelated, retryable failure without asking again; PR-caused failures need repair and ambiguous failures remain blocked. Use the native diagnosis handoff from `runtime/dk-ci/scripts/ci_retry.py inspect`, then `prepare` with the same current decision and your report. Diagnose every failed job, including jobs omitted from displayed checks. The helper validates evidence and prepares bounded publication intents; it does not make the semantic diagnosis or dispatch retries.

Publish each eligible operation with `scripts/publish.py`; it reruns failed jobs and required dependencies while preserving successful jobs. Reconcile pending operations without sending again, including after interruption. Observe the current head with the verified operation through `ci_wait.py --rerun-operation FILE`, then continue the original task only after current checks pass. Preparing or dispatching a retry never satisfies CI. Handle every affected workflow within the retained allowance; report remaining blockers precisely.

Preserve the caller's retained budgets. Pass its resolved rerun limit as `--automatic-limit` (default three, zero disables automatic recovery); publication intents reserve slots across sessions at the same PR head. A user's explicit request for specified failures can authorize one retry per specified workflow through `user_retry` even after automatic exhaustion. Bind that authorization to its stable request ID, source and exact run/attempt; do not reset automatic counters or treat the request as unlimited permission. See [CI recovery](../../../docs/ci-recovery.md) for the request format and resumption rules.

For managed CI and repair tasks, follow the prepared checklist and same-agent completion checks. Checklist acceptance never replaces current exact-head CI evidence, repair validation or authorized publication checks. Follow [completion checks](../../../docs/task-state.md#completion-checks).
