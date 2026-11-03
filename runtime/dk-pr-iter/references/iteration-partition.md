# PR size gate before CI and review

After initial engineering preparation, after every published code change/rebase,
and immediately before review allocation or completion, check `partition-prs`
using the [single-worker partition contract](../../dk-pr-split-commits/references/partition-prs.md).
Count additions in the complete final PR diff against its pinned merge base, with
rename detection. Tests, docs, generated text and copied prerequisites count;
deletions never offset additions and binaries add no textual lines. Never sum
commit churn or use GitHub's potentially stale addition count as the gate.

Before applying the threshold, inspect `config pr-partition show` with PR context.
A recorded whole-PR disable persists across new commits and resumed runs until
explicitly enabled. It skips assessment and unpublished local candidates. Dispatched
or uncertain writes still require reconciliation; a new preference cannot erase
receipts or authorize completing a stale publication. After reconciliation, use
`partition-prs cancel` only while the full original head is intact; it retains all
drafts and branches. Explicit re-enablement uses guarded `partition-prs resume`,
with current conditional assessment, new manual action approvals and fresh self-review.
See the worker's partial-publication recovery contract.

Record a human's whole-PR opt-out with `disable --reason TEXT --source-file FILE`;
record conditional requests with `instructions --text TEXT --source-file FILE`.
Keep exact wording and source. A decline of only the current proposal is audited
separately and does not disable future partitioning. Never infer a permanent opt-out
from ambiguous feedback. Include the effective preference, source and fingerprint
from `partition-prs check`/`run-state work-gate` in every partition handoff.

If instructions exist, pass them unchanged to the worker and let it decide against
current scope/Decisions/diff. They replace the numeric trigger for enabled behaviors,
including below threshold. The worker records its rationale and cited evidence as
`partition` in the plan or `retain` with `partition-prs assess`. A retain assessment
requires no candidate construction and satisfies the current size gate once
registered. Changed instructions, scope, rules or dependency inputs invalidate its
cache; equivalent history rewrites reuse it. Other engineering constraints still
enter normal preparation. Configured disabled behaviors and manual approvals remain.

The deterministic default threshold is **1,000 added lines, inclusive**. Read the
effective private `pr_size` settings through `config pr-size list`; each key
resolves fork → upstream → user → default. Both behaviors default to automatic.
Disabled skips its assessment and action. Manual builds verified local candidates,
then asks for action-bound publication approval. Automatic completes the guarded
publication under this invocation's authorization. Explicitly declined work stays
in original; another behavior cannot move or copy it implicitly.

Above threshold, delegate one worker through `dk-pr-split-commits`. It first
assesses issue/description/settled-Decision scope and evacuates unrelated changes,
then recounts and assesses coherent related splitting if still oversized. Keep the
primary in-scope deliverable in original; every resulting PR targets the base
repository's default branch. Material ambiguity, unsafe ownership or no remaining
primary deliverable requires a decision before publication. Retain indivisible
oversized changes with a specific reason; do not broaden scope to avoid evacuation.

Register prepared/terminal state with `run-state partition-assessment --head SHA
--state STATE` using the current preparation. `partition-gate --head SHA` checks
that pending work is complete before CI/review. The helper caches assessments and
declines against tree/base/default tip, scope, Decisions, rules, settings and
dependency evidence; equivalent history rewrites reuse the same assessment. Include
the active preparation in `partition-prs prepare --preparation-file FILE` so its
engineering identity is verified. No-op assessments must explain the full scope.

Pending partition work precedes starting the CI waiter on the next retained head.
Iteration passes `ci-wait --run-record RUN_RECORD.json` so the waiter enforces
the same current-preparation partition gate before creating its observer.
Reconcile uncertain partition writes before any competing publication, review or
completion. Publish/verify the new branches and drafts before source removal;
keep recovery refs, exact leases and every operation journal. Reuse verified
branches/PRs on resume. New drafts receive local verification only.

After publication, refresh original scope, Decisions, both complete feedback
snapshots and engineering preparation. Recheck its size with fresh governing
inputs and retain prior explicit declines. Continue only original's iteration;
keep all existing review/repair/rerun budgets. Require configured-reviewer and fresh
CI coverage for the resulting head. Partitioning cannot replace architecture
delivery, the once-only architecture stage, cleanup assessment or completion gates.
If partitioning changes an already-reviewed architecture scope, recheck dispositions
and current catalog/rule delivery; retain the once-only stage and its evidence.
