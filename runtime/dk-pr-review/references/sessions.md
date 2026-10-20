# Review sessions and results

Standalone reviews and iteration cycles use `shared/review_sessions.py` and
`shared/review_coverage.py`. The runner owns execution and result provenance;
workflow callers own their budgets, remediation assessments and completion policy.
Session storage is mandatory and independent of optional reviewer statistics.

Run `run_reviewers.py` with its normal context, Decisions, preparation, scope and
rules files. Omitting `--session-file` creates a fresh external session. Supplying
a new external path creates the session there. Existing paths require explicit
`--resume`, which is valid only for interrupted or incomplete sessions with the
same PR/base/head, preparation, scope, rules and ordered reviewers. Preserve those
input artifacts on interruption. New requests and iteration cycles start fresh;
finished sessions cannot be resumed. Changed evidence requires a fresh session,
within the caller's existing budget. Never reset iteration counters to obtain one.
Iteration uses its next allowed cycle for changed evidence; it does not replace a
bound session under the same cycle/attempt to bypass the cap.

Session filenames ending in `.lock` are reserved for locks. An existing nonempty
file at either derived lock destination blocks the session before writes, preserving
earlier evidence. If an interrupted session from an earlier version has a reserved
name, move only its JSON to a nonreserved filename in the same external directory,
preserve its checkpoint and result files, then explicitly resume with identical evidence.

The session checkpoints each completed reviewer in immutable private storage before
continuing. Resume reuses only completed checkpoints; failed, incomplete, exhausted
or interrupted reviewers execute again. A per-session execution lock rejects
concurrent coordinators. Missing, changed or unwritable session evidence blocks
review; analytics failure merely warns and retains replay inputs.

The runner returns the ordinary aggregate findings and completion flag, plus:

- `review_session`: session `id` and external `path`.
- `review_artifact`: immutable result `path` and `sha256`.
- `executed_reviewers` and `reused_reviewers`: completed providers from this
  execution and from earlier checkpoints, respectively.
- `skipped_reviewers`: exhausted providers and their reasons; other incomplete
  providers remain explicit blockers.

Each result binds session ID, PR, reviewed head/tree, scope, Decisions, base,
reviewer instructions and preparation. All attempts remain available. Coverage
validation accepts only a complete, fingerprinted result with accounted configured
reviewers and the current tree and boundaries. Tree-equivalent commit regrouping
may retain coverage when those boundaries match. A changed tree requires review;
green CI and coordinator verification cannot substitute for it.

Iteration allocates its session paths through `run_state.py review-session`, invokes
dk-pr-review with that path, and registers the runner's JSON using `review-result`.
Its adapter additionally checks active run/cycle/attempt and finding validity/scope
assessments. The shared session/result contract has no iteration-record dependency.
Statistics collect only reviewers actually executed, with distinct invocation
identities, and never serve as the source of cached results.

Earlier iteration callers may still pass `--run-record`; this compatibility entry
point delegates state/cycle checks and automatic registration to the iteration
adapter. It cannot be combined with explicit session options. Completed bound
sessions may return explicitly reported reused coverage through this legacy path.
Mandatory session and result storage remain independent of iteration records.
Optional statistics may read those records to discover an omitted cycle.
Historical version-1 artifacts retain their original completion checks in
the iteration adapter and never supply reusable reviewer checkpoints.


New provider descriptors include a retained execution binding. The package and
source hashes are pinned before preparation, and review resolves that binding
without consulting a replacement registry entry. Private package copies remain
available after host cache deletion. Resource or descriptor changes still block
coverage acceptance. Legacy descriptors continue to use their original checks;
missing legacy resources require fresh preparation rather than a version switch.

Command descriptors retain package resources and standalone entrypoints as well.
They execute a pinned argument vector while preserving ordinary data/output paths.
Both explicit session execution and the completed iteration compatibility path
use these bindings. Native drafting providers and command drafting corrections
also retain their binding across the bounded correction operation.

Reviewer adapters provide valid complete, clean and incomplete JSON examples and
the packaged `scripts/review-result.schema.json` contract. The schema is included
in retained instruction provenance; `validate_result.py --schema` prints it for
provider integrations. JSON structure is checked in addition to pinned file/line
and source-evidence validation. Schema-valid JSON alone cannot establish review
coverage or publication authority.

Validation failures identify JSON field pointers, missing/unexpected fields and
bounded eligible-line samples. One correction remains the limit. Incomplete or
usage-exhausted responses must have empty findings and a nonempty reason; they
cannot pass the completed-result validator or become a clean review.
