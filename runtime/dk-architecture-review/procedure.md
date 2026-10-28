# Architecture review gate

Read [architecture delivery](../dk-architect/procedure.md). Act independently of
workers and the architect. The architecture reviewer always receives the full
catalog. Inspect the pinned PR against baseline obligations, approved deviations,
source evidence and current scope/Decisions before accepting any PR-derived
architecture changes. Report every violation with rule ID and revision, relevant
paths/locations, code evidence, rationale and consequence. You cannot grant or
broaden exceptions. Unmet, ambiguous or disabled policies require the user.

Use `architecture review --preparation FILE --context FILE --decisions FILE`
with optional `--exceptions-file FILE` (an array of approval references). Return
one coverage judgment for every catalog rule, including evidence-backed
not-applicable judgments. Missing/duplicate coverage, provider failure and stale
evidence block the gate. Incomplete means complete:false, a reason, and no coverage.
The shared PR runner invokes this mandatory independent gate alongside configured
reviewers. It never counts toward their requirements or changes their settings.
Retain its immutable result in review-session and completion evidence. Unresolved
violations and missing architecture reviews cannot be waived by accepting a
configured reviewer's provider failure. Existing publication authorization remains
unchanged; this role never posts reviews unless separately authorized.
