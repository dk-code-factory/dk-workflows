# Read-only next actions

Use `scripts/dk.py --repository HOST/OWNER/REPO --pr PR_URL --checkout PATH
next --file REQUEST.json --after 0 --limit 20` to inspect current obligations.
The already-running native session decides and invokes skills. This command never
claims work, reserves a check/cycle/retry, delegates, executes a model, contacts
GitHub or authorizes a write. Prepare/finish and publishers still revalidate live
inputs, permissions, CI and readiness. `selected_complete` describes only the
observed configured work; `readiness_established` is always false.

The request contains:

```json
{
  "skill": "dk-workflows:dk-pr-fix",
  "run": "retained-run-id",
  "options": {},
  "git": {"head": "FULL_HEAD_SHA", "target": "FULL_TARGET_SHA", "base": "FULL_BASE_SHA"},
  "definitions": {},
  "dependencies": {"tasks/1": ["tasks/0"]}
}
```

`options` are the original invocation-local overrides; scoped configuration is
resolved live. Supply `upstream` when applicable. Fix/review owners select current
`tasks`, then `finalizers`; `dk-pr-reviewers-run` selects its current `reviewers`.
For a workflow-bound runner, supply its existing `workflow` selector. Nested
skills remain native assignments; their own managed tasks and evidence contracts
still govern execution and acceptance. No omitted finalizer is restored. Omitting
an override resolves the current configured default, while `finalizers:[]` in
`options` selects none.

Locations such as `tasks/0`, `finalizers/2` and `reviewers/1` refer to flattened
current selections. `definitions` optionally maps these locations to existing
managed definition fields, as accepted by `prepare`. Supply missing/current
definitions for new or replaced skills. Otherwise original retained definitions
supply the source paths, with the owning selection/options rebuilt live. Git
definitions require current full `head` and `target` pins, with optional `base`;
inspection updates those pins in memory before recapture. The caller establishes
their current PR identity. Retained Git pins alone do not prove current scope.

Earlier groups are ordering barriers; parallel siblings can proceed independently.
`dependencies` adds explicit result prerequisites, using another current location
or `key:ORIGINAL_TASK_KEY`. Unknown references and cycles are errors. Optional
terminal failure settles ordering but does not supply an explicit result
prerequisite. Prerequisites do not invent consumed evidence: the native session
still binds the actual governing sources and result artifacts in definitions.

For conditional work, `applicability` maps a location to
`{"key":"ORIGINAL_TASK_KEY","pointer":"/applicability/condition"}`. The pointer
addresses the original normalized report. That task must have intact accepted,
currently reusable evidence. The addressed object contains `applicable:boolean`,
the exact configured `invocation`, a nonempty `reason` and nonempty source
`evidence` references. A sourced false excludes the occurrence. Missing/stale
evidence blocks it; the helper never interprets prose as a skip condition.

Optional `cycle` supplies the intended reviewer cycle. `execution` maps locations
to explicit inspection flags (`cycle`, `retry`, `ci_repair`, `ci_rerun`) for work
whose allowance is owned by its caller. Validation uses a temporary copy of the
existing budget validator, preserving every original counter. The response is
advisory and never allocates an allowance. Use the same retained run/cycle in the
actual managed preparation and reconcile claims before requesting recovery.

`items` is a bounded page of original publication/claim recovery references,
retained design-context references and current task actions. `total` and
`next_after` support pagination (limit 1–100). A blocked occurrence lists up to
20 unmet prerequisites and their full count; resolve the available frontier and
reinspect to see remaining work.
Reinspect after changes: pages are observations, not leases. Invocations contain
only identity/native controls and a full invocation digest; prepare returns the
complete assignment. Assignment/checklist/claim references are JSON Pointers in
the original manifest; results and operations point to original artifacts.

| Action | Native next step |
| --- | --- |
| `invoke_native` | Prepare current inputs; execute the returned native assignment only if admitted. |
| `consume_result` | Read necessary original evidence; findings may remain actionable. |
| `check_completion` | Prepare the original task's bounded completion-only work. |
| `definition_required` | Supply the current definition or restore its governing sources. |
| `blocked_prerequisite` | Complete the named prerequisites or original recovery first. |
| `exhausted_allowance` | Preserve counters and obtain any required explicit continuation. |
| `reconcile_claim` | Inspect the original claim and reconcile its native/domain effects. |
| `reconcile_publication` | Reconcile that original operation read-only; never redispatch it. |
| `inspect_publication` | Inspect the original undispatched intent through its publisher. |
| `restore_evidence` | Restore the missing/damaged original artifact without repeating analysis. |
| `optional_incomplete` | Retain the failed optional result and report the omission. |
| `not_applicable` | Consume its original sourced applicability decision. |

Publication discovery spans retained runs for this canonical PR. `publications`
can additionally name original operation paths, including related push intents.
Pending writes and damaged journals block new work pending original recovery;
verified journals remain unchanged. No observation contacts GitHub or treats a
local receipt as current publication readiness. The alternatives summary uses the
existing once-per-PR gate; historical design evidence never supplies ordinary
current-head review coverage. Use its original next-task and evidence references
when unfinished alternatives need native recovery.

No plan or readiness cache is persisted. One in-memory manifest snapshot avoids
creating even lock/state files on initial inspection. Current configuration,
sources, checks and journal state must be revalidated at execution. Expected
savings are fewer repeated native reconstructions in this session and a fresh
one; native API dollar savings have not been measured.
