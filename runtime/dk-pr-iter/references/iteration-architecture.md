## Pre-loop architecture review

Complete initial feedback fixes and refresh current PR scope, Decisions, both
feedback snapshots and ready engineering preparation. Then run:

`dk.py --invocation INVOCATION_FILE --pr PR_URL run-state architecture-review --head SHA`

Run this independent architecture review once per iteration run, immediately before
allocating review cycle 1. The helper reserves dispatch before calling the evaluator,
keeps model work outside the record lock, and retains immutable original preparation
and review references. Read the returned stage artifact and its `review` reference.
A completed stage is reused on resume. A failed, incomplete or uncertain stage blocks
progress; report its evidence without dispatching the architecture evaluator again.
Do not reset the run or review counters to repeat this stage.

Inspect every violation and use `architecture-finding:` plus
`shared.review_coverage.digest(coverage_item)` as its feedback reference. Verify
claims, assess scope and fix valid violations through the existing feedback workflow.
Obtain independent enforcer authorization for an exception and refresh preparation
with its approval. After publication, record `already_fixed` with code or approval
evidence on the current head. Use `unsupported` only for a disproven claim with
specific evidence. Excluded, deferred or pending-decision violations remain blocking.
Do not rerun architecture review to verify fixes. The coordinator verifies each
original finding; configured reviewers then review the resulting current tree.

`cycle` and `review-session` require the completed stage and verified dispositions.
The session allocation binds its immutable reference; give that session file to the
cycle agent. Later cycles and infrastructure recovery run only configured reviewers.
Continue current architecture packet delivery, independent exception handling and
engineering verification throughout implementation, CI repairs and completion.

Keep the original architecture review head visible. Later code, scope or Decisions
changes do not turn its historical result into current-tree architecture coverage.
Cleanup and completion validate that original evidence and current-head finding
dispositions without launching another architecture reviewer. Recheck dispositions
after every head change, including regrouping. Missing or damaged stage evidence
blocks completion and cannot be accepted as a configured provider failure.
