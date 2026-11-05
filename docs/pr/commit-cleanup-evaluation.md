# Commit cleanup evaluation

Baseline: `0f927a9d01066080665be1ab7e8bd20eb38ae1d5` (refreshed main).

Evaluate each candidate against the last accepted version. Retain a candidate
only when applicable correctness checks pass and its bounded native exercise
demonstrates the stated benefit. Equal results are inconclusive and are dropped.
Dropped candidates cannot become prerequisites for subsequent experiments.

## Hypotheses and gates

| Candidate | Hypothesis | Required evidence |
| --- | --- | --- |
| 1. Separate cleanup guidance | Moving cleanup to its own document reduces instruction reads. | Fewer instruction bytes, full per-commit assessment, unchanged ownership, recovery, tree and publication safeguards; both host distributions and callers updated. |
| 2. Plan before rewriting | Explicit boundaries, source mapping and messages reduce repeated rewriting. | Valid mixed/fixup boundaries and reviewed final tree with fewer applications or a corrected boundary error; coherent series unchanged. One application and at most one concrete correction. |
| 3. Deterministic inspection helper | Read-only JSON inspection reduces agent inspection operations. | Independently checked pins, unique merge base, tree, ordered metadata, parents, messages and changed paths; no repository mutation; fewer native inspection operations; patches still read. |
| 4. Reuse unchanged assessments | History-sensitive assessment specs avoid duplicate semantic work. | Zero new assessments on unchanged rerun; message, history, target and governing-context invalidation; current lease/publication checks; resulting-series coverage after rewrite. |
| 5. Validate according to inputs | Exact executable-input evidence avoids redundant local tests. | Content-only test reused; history-dependent, changed and incomplete evidence rerun; final tree and affected boundaries checked; fresh CI for rewritten published heads. |
| 6. Prepared and blocked outcomes | Explicit outcomes prevent false publication completion. | Accurate preparation-only completion, blocked always incomplete, publication-required preparation incomplete, interrupted publication reconciled without duplicate push; no-op and published cases preserved. |

The helper must not fetch, create recovery refs or rewrite history. Ambiguous
history or ownership blocks rewriting. Outcome changes are local to cleanup and
do not change the global task-state schema.

## Protocol

Use real temporary repositories, isolated worktrees and local remotes. Run one
fresh before/after native-agent pair per candidate and one final combined pair,
at most 14 fresh runs. Keep observable models/settings equal. Agents receive
actual guidance and raw task artifacts without expected answers or prior findings.
Retain guidance hashes, assignments, original outputs, complete success/failure
traces, repository snapshots and receipts privately outside the repository.

Measure instruction bytes read, inspection/Git calls, rewrite applications, test
executions and assessed outcomes. These are bounded observations, not token or
general speed measurements. No open-ended tuning: drop failed or inconclusive
candidates. Commit a sanitized, reproducible results summary.

Cover coherent no-ops, mixed commits, fixups, unchanged reruns, history-only
changes, dirty worktrees, ambiguous ownership, changed target/lease, tree mismatch,
incomplete test evidence and interrupted publication. Check interactions in the
final pair; remove the responsible candidate on failure and rerun affected
deterministic checks.

## Decision ledger

Candidate decisions are recorded below as the bounded experiments complete.

| Candidate | Decision | Evidence and limitations |
| --- | --- | --- |
| 1 | Keep | Native instruction reads: 15,161 → 13,750 bytes. Both arms assess all nine source commits, preserve the reviewed trees, keep the coherent series, and block dirty/mixed-author rewrites. Recovery refs and unchanged remote leases independently verified. One pair; application and test-count differences are not attributed to document separation. |
| 2 | Drop | Both fresh arms use one application, produce the same valid three-commit result, and retain the coherent series unchanged. No demonstrated application reduction or boundary error corrected; equal outcomes are inconclusive. Proposal removed before candidate 3. |
| 3 | Keep | Native inspection operations: 48 → 32; recorded Git calls including helper internals: 69 → 67. Agents read source and resulting patches and preserve trees and coherent history. Twelve deterministic checks include a later completeness refinement rejecting shallow history and disabling implicit promisor fetches; the combined pair uses that final helper. These are operation counts, not speed or token claims. |
| 4 | Drop | Both arms perform zero new semantic assessments on the unchanged event and invalidate after message, history, target and context changes. Both inspect the changed remote lease and block future publication. Existing task-state guidance already supplies the behavior; one fewer overall assessment in the after arm reflects an extra initial content/history probe in before, not an unchanged-rerun benefit. Proposal removed. |
| 5 | Drop | Each arm executes one content test and one history test. After rejects the invalid receipts and reuses fresh content evidence across matching boundaries, but the controlled exercise demonstrates no avoided execution versus before. No test savings claimed. A malformed application in after is traced and recovered. Proposal removed. |
| 6 | Keep | Before cannot represent completed preparation (beta: null outcome, incomplete); after returns prepared/complete. Publication-required theta remains prepared/incomplete; dirty gamma becomes blocked/incomplete. Both reconcile the original interrupted push with one original send. After retains its verified receipt but blocks completion without fresh CI evidence. Successful published paths remain covered by the production publication and full-recipe suites; native reconciliation alone does not establish CI readiness. |

## Completion gates

Commit surviving candidates separately with tests and decision evidence. Retain
shared evaluation support separately only when surviving experiments need it.
Run focused helper/state/publication tests and the complete offline suite under
Python 3.12, package/link/whitespace/message/changed-skill checks and host parity.
Use version 3.3.0 if the CLI or expanded outcomes survive, otherwise 3.2.2 for
guidance changes; do not bump if every candidate is dropped. Update the PR to the
actual scope, push, and verify final-head CI. Leave it open and draft if all
candidates are dropped or required validation remains incomplete. Never merge.
