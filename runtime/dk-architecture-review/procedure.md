# Architecture review gate

Read [architecture delivery](../dk-architect/procedure.md). Act independently of
workers and the architect. The architecture reviewer always receives the full
catalog. Inspect the pinned PR against baseline obligations, approved deviations,
source evidence and current scope/Decisions before accepting any PR-derived
architecture changes. Report every violation with rule ID and revision, relevant
paths/locations, code evidence, rationale and consequence. You cannot grant or
broaden exceptions. Unmet, ambiguous or disabled policies require the user.

Use `architecture review --preparation FILE --context FILE --decisions FILE`
with approvals supplied by the preparation manifest's `architecture_exceptions`.
Optional `--exceptions-file FILE` must contain the same approval references in the
same order; a different list is rejected before review. To change approvals,
refresh preparation with the intended manifest first. Return one coverage
judgment for every catalog rule, including evidence-backed
not-applicable judgments. Missing/duplicate coverage, provider failure and stale
evidence block the gate. Incomplete means complete:false, a reason, and no coverage.
The standalone PR runner invokes this independent gate alongside configured
reviewers. PR iteration invokes it once after initial feedback fixes and before
cycle 1, then retains that stage through later cycles and cleanup. It never counts
toward configured reviewer requirements or changes their settings. Retain its
immutable result and reviewed head in session and completion evidence. Unresolved
violations and missing architecture reviews cannot be waived by accepting a
configured reviewer's provider failure. Existing publication authorization remains
unchanged; this role never posts reviews unless separately authorized.

The read-only model receives [the evaluator contract](evaluator.md), not this coordinator procedure. The coordinator owns lifecycle, packet creation, validation and immutable result storage; evaluators must not recursively invoke this workflow.
