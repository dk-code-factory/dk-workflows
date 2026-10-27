# Run record and recovery

`run_state.py` stores per-PR run JSON and a separate per-repository flaky-test JSON under `${XDG_DATA_HOME:-~/.local/share}/dk-pr-iter/<host>/<owner>/<repo>/`. Use the PR's base repository for `--repo HOST/OWNER/REPO`, including when its head branch is in a fork. Supply `--pr NUMBER` to run commands and `--repo` to flaky commands. The run record is operational memory, not proof that a remote mutation succeeded.

Commands:

```text
run_state.py --repo HOST/OWNER/REPO --pr NUMBER show
run_state.py --repo HOST/OWNER/REPO --pr NUMBER start-run
run_state.py --repo HOST/OWNER/REPO --pr NUMBER resume-run
run_state.py --repo HOST/OWNER/REPO --pr NUMBER adopt-run
run_state.py --repo HOST/OWNER/REPO --pr NUMBER finish-run --status completed|limited --reason TEXT
run_state.py --repo HOST/OWNER/REPO --pr NUMBER thread --ref ID --head SHA --classification CLASS --evidence URL --reason TEXT [--production-lines N]
run_state.py --repo HOST/OWNER/REPO --pr NUMBER event --key UNIQUE_KEY --kind KIND --head SHA --detail TEXT
run_state.py --repo HOST/OWNER/REPO --pr NUMBER cycle --head SHA
run_state.py --repo HOST/OWNER/REPO --pr NUMBER cycle-retry --head SHA
run_state.py --repo HOST/OWNER/REPO --pr NUMBER review-session --head SHA
run_state.py --repo HOST/OWNER/REPO --pr NUMBER review-result --head SHA --file RESULT.json
run_state.py --repo HOST/OWNER/REPO --pr NUMBER decision --text TEXT --source SOURCE
run_state.py --repo HOST/OWNER/REPO --pr NUMBER repair --cycle N --head SHA
run_state.py --repo HOST/OWNER/REPO --pr NUMBER rerun --cycle N --head SHA --run-id ID
run_state.py --repo HOST/OWNER/REPO flaky --test NAME --signature TEXT --head SHA --outcome fail|pass --attempt ID --url URL
run_state.py --repo HOST/OWNER/REPO flaky-show
```

Feedback classifications use the existing `thread` command and `threads` storage for compatibility. Use the inline node ID, `issuecomment-ID`, or `pullrequestreview-ID` from the appropriate snapshot; only inline node IDs can be resolved. Record informational or duplicate feedback as no-reply events with source evidence. Classifications are keyed by reference and exact head, so a later head can change the classification without losing earlier evidence. `event` keys identify one planned or completed remote action, for example `reply:<thread>:<head>` or `push:<old>:<new>`. Duplicate keys are rejected. Before recording a publication as complete, verify its live remote result. On restart, inspect any uncertain action against GitHub and the PR head before retrying. Never infer completion solely from a local record. A new head invalidates pending CI waits and agent review context; re-pin and reassess outstanding threads before continuing.

`local_decisions` retains settled choices and their sources across interrupted runs. Pass the run record path to `dk-pr-decisions context --local-decisions-file` after each decision, body edit, or head change. Give the resulting verified snapshot to every dependent agent; a stale snapshot cannot be reused.

For completion, record the handoff with the starting, reviewed, and final heads, reviewed tree, target tip, cleanup assessment path, cycle and final-head CI results, and prompt source. Record the title/body check or edit, each inline reply and resolution, each source-linked top-level reply, and each reviewer request as separate events. Record squash-only requests as ignored editorial feedback with their source references. Include the final head in reviewer-request keys, for example `review-request:login:<head>`; check both the record and GitHub's pending requests before sending. Preserve rejected and uncertain requests with their API responses as evidence. Pass the run record path to the completion agent; it must read the full record there.

Review cycle numbers are one through five; use cycle zero for CI after initial thread fixes. A single `cycle-retry` may reuse the last cycle only after an incomplete usage-limited review; its prior attempt remains in the record. `repair` allows five attempts per cycle. `rerun` allows three attempts total per unchanged head, across cycles and Actions run IDs. A rerun does not change the head. The flaky tracker requires a test name, signature, exact head, attempt ID, and link; only a fail followed by pass on the same head qualifies.

For top-level replies, record the source reference, exact source text/update time, reply URL, and verified head in the event detail. A changed source at the same head requires reassessment; preserve the original classification and record the reassessment as a new event with the changed source fingerprint rather than overwriting earlier evidence. Reconcile later comments before retrying an uncertain post. Top-level comments have no resolved flag, so a missing inline thread does not close their work.

## Commit-series cleanup recovery

Use existing `event` entries for cleanup: record the worker handoff, prepared guard state and recovery ref paths, publication attempt/result, and verified artifact path. The `series-assessment:FINAL_SHA` event has kind `series-assessment`, head `FINAL_SHA`, and detail containing the absolute artifact path and `no_op` or `published` outcome. Keep artifacts outside the repository beside the run record. Record failure or interruption separately; absence of a changed head is not assessment evidence. Completion receives this same artifact path and runs the complete gate with it; the review-stage gate alone cannot authorize completion.

Preserve the guard state, original-head recovery ref, original commits, patches, review-loop token, boundary reasons, and CI evidence when cleanup fails or is interrupted. A regroup push records `pending` before publication and `published` only after live verification. On resume, inspect these paths and reconcile GitHub and both remote refs before retrying work. For uncertain or interrupted publication use `split_guard.py reconcile`; it confirms only the prepared final head/tree and unchanged target tip, and never pushes. If it cannot verify publication, report cleanup incomplete and retain all artifacts. Do not delete guard state, refresh its lease, or automatically repeat the push. Reconcile a confirmed publication before creating its assessment; rerun the existing CI waiter on that exact final head and refresh both feedback artifacts and decision context. A changed head or target tip invalidates an earlier assessment and requires fresh review readiness and full-series assessment. After a successful assessment the guard archives its state in `pr-split-history`; retain that returned recovery path and ref. This allows fresh preparation without discarding prior evidence. Use a new artifact path and retain old events.

## Reply controller history

Use [the shared reply controller](../../dk-pr-reply-review/references/reply-controller.md) with this record's path for initial replies, later review feedback, and completion. It adds `reply_workflow` while preserving existing commands, decisions, events, and cycle counts. Reply-workflow state version 2 migrates version-1 mappings using retained receipts, journals, and successful batch evidence; ambiguous provenance remains blocked without guessing from authorship. It preserves original evidence and mappings in append-only `answerRevisions`, including change snapshots, while retaining unrelated run fields, counters, history, and artifacts. The controller owns the canonical reply agent ID, pinned head, current phase, source fingerprints, retained request mappings and verified answers, outstanding/held requests, and publication/rejection state. Completion receives this same record and agent ID; it must coordinate with that agent rather than start another reply agent.

`observations` retains both complete snapshots for every `next` instruction. `history` retains separate inline and top-level batch attempts with immutable batch/verdict/journal paths, digests, phase, head, validation passes, and verified receipts. Corrected and rejected versions remain available. Artifacts live beside the run record outside the repository. The existing `event` command can record handoffs and individual remote actions, but it must not manually advance controller state. Keep exact source text/update times and URLs in the saved snapshots; preserve semantic matches and cross-source evidence in request mappings, including matches to resolved threads.

On restart or a changed head, run the controller's `reconcile` after refreshing preflight and decision context. It verifies live receipts, reconciles pending journals, and directs fresh inline-first assessment while retaining prior verified answers. A new head does not make an already answered request eligible for another reply. Never discard `reply_workflow`, its journals, or rejection history to restart a pass. A final validation rejection stops subsequent reply publication, including completion. Missing/edited controller-published or resolution replies, incomplete evidence, or uncertain duplicate candidates remain blockers until reconciled. Strict failures include the affected URL and category. Evidence-only blocks may be retried with `next` and clear only after strict verification succeeds. Edited/deleted semantic evidence automatically marks all dependent mappings stale and returns still-present requests for inline-first reassessment, including requests whose own text is unchanged. Inspect `stale_answers` during completion; stale mappings cannot satisfy cleanup, duplicate-answer guards, or resolution. Restoring or dismissing a still-present stale request requires independent approval even with zero replies; holds remain outstanding. Reconciliation never replays publications or restores changed semantic evidence from an interrupted batch.

## Shared flake registry

`flaky` and `flaky-show` retain their existing per-repository file and fail/pass history. They now use shared infrastructure; old history has no workflow/job or verified issue context and remains evidence only. Use [dk-flaky-tests](../../dk-flaky-tests/procedure.md) for verified entries, source evidence, issue lifecycle history, and scan deduplication in the same private tracker. Repository identities are case-normalized; clones share history and forks remain separate. A current open issue and full failure coverage are required at every CI decision, including completion. Record the effective result, raw result, ignored failures with issue URLs, blockers, and shared decision/evidence paths in CI and completion handoff events. Never reuse a saved `known_flakes` label as authorization after a head, attempt, or issue state change.


## Engineering preparation and scope assessment

Before initial changes/rebase/fixes, assemble preparation using
[the Decisions procedure](../../dk-pr-decisions/references/preparation.md). Retain
old run counters and operational history. Save immutable run-history snapshots as
preparation evidence so recording a cycle or event does not invalidate its own input.
The active run record remains mutable; `work-gate` separately detects new local choices.

```text
run_state.py --repo HOST/OWNER/REPO --pr NUMBER preparation --file PREPARATION.json --pr-url PR_URL --head SHA [--expected-parent PARENT_ID]
run_state.py --repo HOST/OWNER/REPO --pr NUMBER work-gate --head SHA
run_state.py --repo HOST/OWNER/REPO --pr NUMBER assess --ref REF --head SHA --preparation-id ID --disposition within_scope|outside_scope|decision_blocked --evidence SOURCE --reason TEXT
run_state.py --repo HOST/OWNER/REPO --pr NUMBER fix-plan --ref REF --head SHA
```

`preparations` retains registration history; `preparation` points to the current one.
Registration checks that existing local choices are included. It may retain a
blocked artifact to record scope holds; only a ready artifact authorizes work.
A fresh run's first valid registration may carry child or migration lineage from a
previous run. Registration history records its `iteration_run_id`; after that first
binding, activations require the expected active parent and reject competing siblings.
Refresh from the current preparation using `--previous-preparation`, including full
reassessment. A parentless artifact cannot replace an existing binding; an explicit
`--expected-parent` cannot substitute for artifact lineage. Repeating a binding is
idempotent only while that same artifact remains active. Registration remains one locked mutation
and preserves the existing run and review budgets.
`assessments` retains immutable scope dispositions keyed by feedback reference, code
head and engineering ID for fresh runs, with exact preparation provenance, sources and rationale. A later preparation can change
scope at the same code head without erasing the previous assessment or classification.
Validity remains in `threads`; an excluded valid defect never becomes `unsupported`.
Only a `fix-plan` action of `fix` permits the fix handoff. It returns principal code/test
locations, test strategy and worker instructions; scope is consumed before the
existing pre-existing-defect size limit. `skip`, `decision_blocked`, `deferred` and
`no_change` preserve their evidence and do not authorize code edits.

Review-cycle starts/retries and CI repair attempts require ready current preparation.
Before local mutations and initial fix handoffs require `work-gate`, plus the existing
live PR scope/head recheck. Missing/stale/unresolved preparation or pending material
synchronization blocks dependent work even with a clean review. Refresh after an answer,
body/head change or material implementation discovery without resetting cycle/repair/
retry counts. Both completion stages additionally require the fingerprinted engineering
verification file documented with preparation; custom prompts cannot skip it.

Both completion gate stages require `--run-record RUN_RECORD.json` pointing to this current mutable record. They reuse `work-gate` to reject changed local choices or a selected preparation that differs from the active registration, even when the code head and immutable evidence snapshots are unchanged.

Explicit repository/user CI exceptions are managed by [dk-config](../../dk-config/references/ci-exceptions.md), separately from automatic flakes. Record `accepted_exceptions` with the underlying `failure`, matcher IDs, reasons, scopes, config source fingerprints and exact job/attempt evidence. Completion reloads the current configuration; removed entries or changed sources invalidate saved acceptance.


## Reviewer analytics

The separate [reviewer statistics ledger](review-stats.md) records configured reviewer
invocations and assessment history without changing cycle, retry, repair or rerun
counters. Record `review-assess --file LINKS.json` after ordinary classification/scope
assessment; the compact links reuse its evidence and validated severity. Preserve the
runner's sidecar path in handoffs and retain replay inputs when analytics fails.

## Iteration runs and required review provenance

`iteration_run` identifies the active or terminal request. `start-run` archives the
terminal record in `run_history` and resets only review cycles, usage retries,
per-cycle repairs, review-session bindings and review-result registrations.
Publications, reply-controller state, decisions and per-head CI rerun limits remain
available. `resume-run` requires
an active run and preserves its identity and counters. `adopt-run` gives legacy
history an identity without clearing counters; record its actual outcome explicitly.
Only a later user request permits a new budget. Never finish/restart within one
request to evade the cap. Keep active status on interruption. Finished runs cannot
pass completion or authorize more prepared work.

Iteration keeps budgets and active run/cycle/attempt checks here. Allocate a
session using `review-session --head SHA` after recording its cycle; this returns a
stable external `session_file` for that cycle and usage retry. Give that path to
[dk-pr-review](../../dk-pr-review/references/sessions.md) with `--session-file`.
Use `--resume` only after interruption with identical evidence. The runner produces
mandatory review provenance for both standalone and iteration reviews independently
of this record. Optional statistics may read the record to discover an omitted
cycle. Save its JSON and register it through `review-result --head SHA --file FILE`.
Registration checks the allocated session, pinned head and current preparation.
`review_results` links immutable artifacts to the active run, cycle and attempt.
Each cycle/attempt keeps its allocated session. Changed preparation, scope or rules
requires the next allowed cycle and a fresh session; an exhausted cap remains a
visible coverage limitation. If execution finished before result registration,
save a JSON object whose `review_artifact` is that session's `results[-1]` and pass
it to `review-result`. This registers the already completed evidence without
another provider execution or cycle. Preserve the session and immutable artifact.
Classify and assess findings using `review-finding:` plus
`shared.review_coverage.digest(finding)` as their references.
For review events, include the iteration run ID in the unique key; events retain
`iteration_run_id`, so an older run cannot authorize a usage-limited retry.

Both completion stages validate the latest registered result against the active
run and current tree, base, scope/Decisions and reviewer instructions, and require
validity/scope assessments for every finding. Missing, incomplete, changed or stale
results yield `review_coverage_invalid`, even with `--review-complete` and green CI.
An in-scope actionable finding blocks completion. Proven tree-equivalent regrouping
may reuse coverage when the other boundaries match. A final-cycle fix changing the
tree requires a visible limitation rather than completion or a sixth cycle.
The CLI includes verified review provenance or a `review_limitation` reason.
Report actual reviewer execution in this invocation separately from reused earlier
results, including reviewed heads/trees and all skipped reviewers with reasons.

The iteration adapter still validates fingerprinted version-1 completion coverage
against its original active-run/latest-cycle, PR/tree/scope, reviewer accounting and
finding-assessment guards. Legacy evidence never becomes a session checkpoint or
grants a fresh budget. Existing runner `--run-record` callers route through a thin
compatibility adapter that allocates/registers shared sessions; new callers use the
explicit session/result contract.


## Accepted reviewer failures

`accept-review-failures --head SHA --reviewer NAME [--reviewer NAME ...] --source EXACT_HUMAN_INSTRUCTION_OR_SOURCE --reason TEXT` appends `accepted_review_failures`. Each entry retains the active iteration ID, immutable review-artifact reference, reviewed head/tree/scope, exact reviewer names, human source and rationale. Apply prior applicable human acceptance without asking again. Only recorded provider failures/exhaustion can be accepted, after all configured reviewers were accounted for. Evidence/checkout/instruction guards and unrun reviewers cannot be accepted. History survives restart, but a different active run/result/tree/scope does not inherit permission. Gates preserve `complete: false` and return accepted failure names plus their source records; report these separately from successful/reused reviews. Findings, CI, preparation and series cleanup remain mandatory.

Incremental preparation children carry an expected parent ID. Registration compares it with the active preparation under the run-record lock; reconcile concurrent siblings instead of overwriting the newer active artifact. Typed operational snapshots and zero-call refresh behavior follow the preparation contract.

## Prepublication cleanup coverage

`precheck-cleanup --head REVIEWED_SHA --candidate-head CANDIDATE_SHA --split-state FILE`
requires a fresh ready preparation and complete feedback/Decisions pinned to the
still-published reviewed head in an isolated checkout. Keep the candidate in the
owned repository checkout. `cleanup_prechecks` retains immutable comparisons
separately from `review_reconciliations`. Version-1 review results require their
exact reviewed scope and a strict version-1 comparison, using existing run/cycle
provenance without inventing a session or attempt. The receipt binds the active
run, available cycle/attempt and session provenance, original review result,
original and current preparations/scopes, candidate head/tree/base, remote lease
and stable guard identity. Pass its artifact path to the review-stage gate and
the regroup push/no-op assessment. Persisted disagreement remains blocking; interrupted
comparisons grant no coverage. No cycle or reviewer execution is added.

After cleanup, refresh final-head evidence. Version-2 review provenance uses
`reconcile-review --head FINAL_SHA --series-assessment FILE`; version-1 coverage
keeps its exact-scope verification. Completion requires applicable final coverage
and current verification/CI. A precheck cannot replace them.
Report review readiness separately from cleanup and completion readiness.

## Recovery into the repaired runtime

Legacy active runs without `contract_version: 2` must continue through their original retained runtime. This recovery procedure applies to compatible contract-v2 runs only.

First verify that the old invocation and all its children have stopped. Inspect
their retained lifecycle records with `update.py inspect --host HOST`, and
explicitly complete each stopped child's token before its parent using the normal
`complete --host HOST --token TOKEN --reason TEXT` command. Preserve lifecycle
records; never delete them or infer liveness from age or a launcher PID alone.

Enter the repaired installed package normally and use its returned retained
`runtime_root`, `completion_script` and new lifecycle token. For a run already marked `contract_version: 2`, resume the existing
active iteration with `resume-run`; do not start a replacement run or reset the
five-cycle budget. Validate the retained reviewer packages, original session and
result, original preparation and evidence copies, current preparation, candidate,
guard state/remote lease, recovery ref and base before continuing. The repaired
runtime orchestrates recovery while original reviewer packages remain pinned.

For an unpublished candidate, refresh reviewed-head evidence in an isolated
checkout, obtain the prepublication receipt and rerun the review-stage gate before
the guarded push or no-op assessment. For a pending push with its receipt, use
`split_guard.py reconcile` to check the same candidate against the remote; never
retry an uncertain push or refresh its lease automatically. Missing/corrupt
provenance or disagreement remains a blocker. After verified publication, refresh
final-head preparation, reconcile against the original review with the published
assessment, refresh engineering verification and exact-head CI, and run the
complete gate. Retain the original artifacts and counters throughout recovery.


New `iteration_run` records carry `contract_version: 2` and a private `workflow_dir`.
Fresh runs require preparation v3. `resume-run`, work and preparation activation
reject legacy active runs with an instruction to use their retained runtime; they
never rewrite those records. After a terminal legacy run, normal `start-run` archives
history and preserves cross-run publication, choice and rerun records as before.
Before allocating a review cycle, validate retained reviewer bindings. A failed
validation consumes no new cycle and never refunds previously consumed cycles.
Operational preparation refresh preserves assessment/result/verification reuse by
runtime-computed engineering ID while retaining original preparation references.
