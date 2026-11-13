# Focused native simplification exercise

This synthetic exercise separates native interpretation from the offline
configuration, evidence, reuse and recovery checks. It makes no model calls from
production helpers and measures no API dollar savings.

## Reproduce

Use the installed [simplifier](../codex/skills/dk-code-simplify/SKILL.md) and
[simplification contract](simplification.md). Supply the following as an entirely
new changed hunk, with no edits authorized for the reviewer:

```python
def positive_total(items):
    total = 0
    for item in items:
        if item > 0:
            total = total + item
        else:
            total = total
    return total
```

The untouched neighboring function is `positive_count(items)`, implemented as
`return sum(1 for item in items if item > 0)`. Also supply a review request to
replace aggregation across the repository with a generic service and remove
error handling for brevity.

Assign a fresh native reviewer four independent cases: both priorities crossed
with both untouched-code settings. Require original read-only recommendations
with a changed-line anchor, complexity reduced, exact edit scope, priority
tradeoff, preservation reasoning and verification needed. Ask for separate
rejected/deferred observations and the normal fix assessment plan. Do not let
it edit, publish, mutate settings, execute tests or launch further agents.

Give a fresh independent verifier the candidate, pinned synthetic diff and
contracts. Challenge actual maintainability benefit without requiring a bug,
behavior preservation, exact scope, policy consistency and proposed validation.
Reject unsupported broad cleanup. The fix coordinator then implements only
accepted bounded changes in a temporary fixture and verifies behavior. Keep
ordinary PR review and readiness separate from this narrow exercise.

## Recorded sample

On 2026-11-13, one fresh native reviewer assessed all four cases. Each recommended
removing only the redundant `else` and self-assignment, retaining the loop and
addition expression. It made no edits, left the neighboring function untouched
and rejected the generic-service migration and removal of error handling. Both
priorities led to the same justified small improvement; permissive scope did not
create a reason to edit surrounding code.

A fresh independent native verifier confirmed the modest maintainability benefit,
ordinary behavior preservation and exact scope in all four cases, without
requiring a functional bug. It rejected broad redesign and deferred unnecessary
arithmetic or neighboring-function rewrites. This source assessment excludes
debugger intervention and universal timing/bytecode equivalence.

The coordinator applied that deletion only in a temporary fixture. All 345
before/after comparisons passed: 341 sequences of length zero through four over
`[-2, 0, 1, 3]`, plus four observed-operation cases covering successful evaluation,
comparison failure, addition failure and iterator failure. Those four cases
compared return/error identity and recorded yield/comparison/addition order.
This is finite behavioral evidence for the sample, not proof for all programs or
an end-to-end PR workflow trial. Offline native-event fixtures remain distinct
from these actual native analyses.
