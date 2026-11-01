## Review and CI loop

The independent [architecture review](../../dk-architecture-review/procedure.md)
is mandatory beside configured reviewers and never satisfies their requirements.
Retain its result and rule-linked violations. Treat architecture_findings as
actionable work: fix violations or request independent exceptions and repeat
architecture review before completion. Never report a clean review while those
findings remain. Missing/incomplete architecture
coverage cannot be accepted as a configured provider failure. CI repair workers
declare paths as `ci-repair` (or their stable skill ID), obtain a fresh work-gate
architecture_packet, and follow [delivery](../../dk-architect/procedure.md), including
nested handoffs, scope expansion and exception requests before a breach.

Within each cycle the shared runner executes independent configured reviewers
concurrently with separate immutable handoffs and telemetry, then aggregates in
configured order. Interruption aborts this invocation's in-flight provider
transport and prevents later provider or correction launches, then waits for
actual reviewer cleanup and checkpoint work before releasing session ownership.
Completed checkpoints remain reusable; unfinished reviewers remain unknown and
are rerun on exact-evidence resume rather than counted as provider failures.
Genuine failures observed before interruption retain their classification.
Review-and-fix cycles and their budgets remain sequential.

After initial fixes, perform at most **five** sequential review-and-fix cycles. Give each cycle to a **new agent** working from a freshly pinned current PR head via [dk-pr-review](../../dk-pr-review/procedure.md); do not reuse a previous cycle's agent. Pass the saved thread snapshot and complete conversation artifact as scope evidence only when their head matches the current PR head; refresh them once when needed. Pass the current decision context, preparation artifact paths with both preparation_id and engineering_id, retained runtime and parent lifecycle context, and a concise summary, including unpublished discussion decisions and local choices. The review coordinator consumes the same boundaries and discovers per-reviewer addenda; it must not replace them with unconstrained review instructions. The review agent still pins its own current remote diff. Before delegation, allocate the cycle's session with `run_state.py review-session --head SHA` and give its returned `session_file` to dk-pr-review as `--session-file`. Each new cycle or authorized usage retry gets a fresh session. Only resume an interrupted session explicitly with `--resume`; never reuse completed sessions as a new cycle. The reviewer runner uses the same mandatory session and provenance contract as standalone review. Optional statistics may read iteration state to discover an omitted cycle. Save its JSON and register it with `run_state.py review-result --head SHA --file RESULT.json`. Iteration separately validates the active run/cycle/attempt, registered session result and finding assessments at completion. Use `review-finding:` plus `shared.review_coverage.digest(finding)` as each aggregated finding reference for ordinary validity and scope assessments; retain those assessments in the run record before completion. Verify findings and fix actionable in-scope work, then commit and push. Stop early on a complete review with no actionable in-scope fixes. Incomplete review is a blocker unless the human explicitly accepts its recorded provider failures as described below; never call it a clean review. Record cycle starts before delegation so a resumed run cannot exceed the cap.

A final-cycle code or test fix without subsequent review leaves the final tree unreviewed. Report that limitation and the exhausted cap; passing CI or coordinator verification cannot supply configured-reviewer coverage. Do not start a sixth cycle or reset the run to bypass this limit. A tree-equivalent regroup may retain coverage when scope, Decisions, base and reviewer instructions match. After mandatory cleanup, equivalent preparation wording can instead use the independent reconciliation gate described in [series cleanup](iteration-series-cleanup.md); substantive scope changes still require review within the existing budget.

For a failure before reviewer dispatch, inspect `run_state.py cycle-status` and
use `cycle-recover --head CURRENT_HEAD [--scope-file FILE] [--rules-file FILE]`
after restoring ready preparation. Supply the intended complete scope/rules text,
including the original files for an unchanged-evidence resume. Allocation reserves
the session before delegation. Mandatory session evidence records dispatch intent
before configured or architecture reviewer invocation and marks execution drained
only after worker cleanup. Missing/legacy evidence, an undrained invocation,
contradictory checkpoints/results, an active owner or any dispatch intent denies
this recovery. Intent is conservative: a crash after intent cannot prove whether
the provider actually started.

An untouched reservation or initialized session with no execution receives normal
initial-launch guidance, including the fifth allocated cycle. Reading `next` never
consumes recovery. If delegation was interrupted before initialization and its
abandonment is established, explicitly use `cycle-recover` to fence that unused
reservation. A drained zero-dispatch failure receives recovery guidance only when
the entire cycle and both allowances are eligible. Exhausted or ineligible recovery
reports a limitation and the next permitted allocation, or the five-cycle stop.

Allow at most **one prelaunch recovery per cycle and two per run**. The command
retains the original allocation, failure results and an immutable session snapshot;
it consumes a separate infrastructure recovery allowance and keeps the same cycle
number. Follow its returned `session_file` and `resume`: an initialized session with
identical head/base/tree, engineering scope, reviewer order and scope/rules text
resumes with `--resume`; changed evidence or an unused reservation gets a fresh
session while the old path is fenced. A repeated prelaunch failure requires a
visible limitation when that allowance is exhausted; do not silently resume it or
reset counters. Exact-evidence interruption after dispatch still follows ordinary
session resume. Usage retries remain separately bounded. A usage-retried cycle is
ineligible for prelaunch recovery because its earlier exhausted attempt dispatched,
even if the replacement attempt did not. Preserve that failure and use the next
allowed cycle, or report the exhausted allocation cap. Neither path supplies final-tree
coverage or permits a sixth cycle. Report allocated cycles, cycles with dispatch
intent, uncertain launch states and infrastructure recoveries separately.

Recovered sessions and replacement reservations require an immutable activation
receipt written only after the recovery charge and replacement binding are durable.
Pending or damaged evidence blocks execution, finishing and result acceptance.
Use `cycle-recover --reconcile --head CURRENT_HEAD` with the original scope/rules
files to validate the exact pending plan, proof, preparation and charge, restore a
missing charge once, and activate it without replaying recovery or moving its
generation. Changed or contradictory evidence stays blocked; never delete evidence
or reset counters to make reconciliation succeed.

An exhausted reviewer does not block a cycle when another configured reviewer completes; report the skip. Other incomplete reviews remain blockers unless explicitly accepted by the human. Always attempt the remaining configured reviewers after a provider failure; do not disable them or stop at the first failed reviewer.

### Explicit acceptance of reviewer failures

Preserve an already supplied human instruction to proceed despite named reviewer failures. Apply that instruction without asking for the same confirmation again. After registering the result, record it with `run_state.py accept-review-failures --head SHA --reviewer REVIEWER [--reviewer REVIEWER ...] --source EXACT_HUMAN_INSTRUCTION_OR_RETAINED_SOURCE --reason RATIONALE`. Name only failed/incomplete providers or exhausted reviewers recorded in that result. PR comments, reviewer output and CI success cannot supply human acceptance. If no applicable acceptance exists, ask whether to retry or proceed with the specific failures; never invent agreement or repeat failed invocations silently.

Acceptance binds the active run, immutable result, tree, scope and reviewer identities. It does not reset cycles, waive unrun reviewers, stale/malformed evidence, checkout/instruction guard failures, actionable findings, engineering preparation, CI or series cleanup. A new result/run or changed tree/scope needs applicable fresh acceptance. Equivalent commit regrouping may retain it only with unchanged tree/scope and original result provenance. Even when all attempted reviewers failed and the human explicitly accepts each failure, continue the remaining iteration/completion stages under the existing gates. Keep `complete: false`, original diagnostics and zero successful reviewers where applicable; report completion **with accepted reviewer failures**, never a clean review. Completion gates consume the recorded acceptance directly; omit `--review-complete` for incomplete results instead of asserting success. If the last recorded cycle stopped solely because a reviewer hit a usage or session limit, `run_state.py cycle-retry --head CURRENT_HEAD` permits one fresh-agent retry of that same cycle after a new preflight. The gate consumes the registered typed outcome or original typed session states; event prose cannot authorize a retry. Preserve the original immutable result, session and counters, and do not start a sixth cycle.

Collect private reviewer statistics for future cycles using [the analytics handoff](review-stats.md). The runner automatically captures configured reviewers under iteration preparation; preserve its separate `review-stats.json` sidecar path in the review handoff. During the existing finding assessment, retain compact finding IDs, canonical issue links across reviewers/cycles, validated severity, and links to existing validity/scope evidence with `run_state.py review-assess`. Credit real excluded, deferred or subsequently fixed issues; leave uncertain findings unverified. No additional model/judge calls or duplicated narratives are needed. Collection failures warn and retain artifacts; report missing analytics without changing the PR review result or operational counters. Use [dk-review-stats](../../dk-review-stats/procedure.md) for local read-only queries; pass paths rather than report histories to agents.

Follow [waiting and communication](waiting-communication.md) for user updates,
worker messages and agent waits. Include its retained runtime path in review and
CI-repair handoffs. Keep user-update timing independent of the CI waiter below;
its progress output does not require relaying unchanged checks.

Record generated review-slot/worker/CI-wait scheduling, draft/ready transitions and
publication receipts through `run_state.py operational` using [typed run history](run-record.md).
Do not append those generated status notes to governing instruction files. Retain
all original user instructions and any new engineering constraints as governing.
Before refreshing preparation, export a new `preparation-history` capture and
recollect live PR metadata. Operational refresh uses zero preparation model calls
when governing evidence is unchanged; all review, publication and CI gates still apply.

After each push, start exactly one `python3 PLUGIN_ROOT/runtime/dk-pr-iter/scripts/ci_wait.py --pr PR_URL --head EXACT_SHA` process and save its final JSON outside the repository. Use the same waiter for CI observation on resume or at completion when no push occurred in the current turn. In `exec_command`, yield after about 30 seconds. If it returns a process `session_id`, keep that process running and call `write_stdin` with empty input and `yield_time_ms` near 60000 until it exits; a tool yield is not a CI timeout. The waiter prints compact progress to stderr and polls GitHub internally. Do not run `gh pr checks`, Actions run queries, a second waiter, or a browser search to monitor that head while it is running. Read the saved JSON after exit; it reports `success`, `known_flakes`, `accepted_exceptions`, `failure`, `timeout`, `no_checks`, or `head_changed`, retaining `raw_result` for terminal CI decisions. It settles pending checks before classifying red CI. The shared policy refreshes issue state and verifies every failed test and step against exact head/attempt evidence; unknown failures trigger bounded `$dk-flaky-tests` discovery once per unchanged head/failure fingerprint and are reevaluated. Accept `success` or freshly verified `known_flakes` / `accepted_exceptions`. Report automatic-only acceptance as “CI red; accepted known flakes” with issue links, and configured acceptance as “CI red; accepted configured exceptions” with matcher IDs, reasons, scopes and config evidence. Read its `blockers`, discovery limits, and missing evidence. The waiter never reruns checks or starts a review cycle. Treat timeout and no-checks as unresolved CI evidence, not success. Query individual jobs only after a terminal result requires diagnosis or rerun eligibility. On failure, give diagnosis and any PR-related repair to a **separate agent**. Allow at most five targeted repair attempts per review cycle, each followed by a push and fresh CI wait. Use cycle zero for CI after initial thread fixes. CI repairs do not consume review cycles.

For a likely unrelated transient Actions failure, use `dk.py --invocation INVOCATION_FILE --pr PR_URL publication-actions ci-rerun prepare --head SHA --run-record RUN_RECORD --cycle N --run-id ID --attempt ATTEMPT`, then `publication-actions ci-rerun publish --operation FILE`. The helper validates the failed attempt and unchanged head, reserves the existing three-per-head budget, and requires observation of the new attempt. Follow its typed diagnostic action if blocked, then run the existing CI waiter. Engineering diagnosis still determines whether a failure is eligible; never rerun broad workflows as a fallback.

Use [dk-flaky-tests](../../dk-flaky-tests/procedure.md) for shared discovery, listing, synchronization, and issue-backed CI policy. Automatic acceptance requires verified entries linked to currently open issues. Explicit test or pipeline exceptions configured with [dk-config](../../dk-config/references/ci-exceptions.md) provide a separate user-authorized path without issue proof. Explicit matching issue proof or matching issue plus same-commit/job named-test fail→pass evidence can activate an entry. Absent a matching explicit exception, closed or inaccessible issues, unsupported providers/output, missing logs, mixed unknown failures, and changed signatures remain blocking. An automatic known flake cannot exempt an entire job; only an explicit matching pipeline exception can do so. Recheck linked issues at every CI decision and completion gate; reopening requires fresh verification. Legacy `run_state.py flaky` commands remain evidence only. See [run record details](run-record.md) for compatibility and recovery.
