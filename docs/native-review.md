# Native reviewer handoffs

The coordinator uses the host's native agent tools to invoke selected reviewer
skills. The host reports agent execution status; each skill reports its own
completion, findings and coverage. Both must succeed before a review is complete.
This trusts the host and selected skill; it is workflow bookkeeping, not an
independent proof that a reviewer obeyed every instruction.

## Launch and wait

1. Resolve the selected skill and pin its task inputs before launch. Prepare
   immutable context and output destinations outside the source checkout. Give
   the reviewer its exact task, pinned source, original guidance, resolved skill
   resources, permitted actions and required completion report. Keep source
   inspection read-only; the selected skill owns its private state and lifecycle.
2. After `start-batch` returns `invoke`, launch that reviewer with native agent
   tools and retain the returned handle against its task key and claim id. Keep
   the mapping in private operational progress, not fingerprinted task inputs.
   Let the skill manage its own nested passes and prescribed resource use.
3. Wait on that handle until the host reports a terminal outcome. Running,
   queued, timed-out observation and progress-only responses are not completion.
   Save the terminal host outcome and the original final skill output. If native
   delegation is unavailable, report incomplete rather than substituting a
   model CLI or fabricating a handle.

## Record the outcome

| Native outcome | Skill report | Task result |
| --- | --- | --- |
| Successful terminal result | Nonempty, complete, valid report | Complete; retain any findings |
| Successful terminal result | Incomplete, missing or malformed report | Incomplete with the reason |
| Failed or cancelled | Any report, including one claiming completion | Failed; retain all available output |
| Running or unknown | Any partial report | Outstanding; recover only after reconciling the original agent |

Interpret reports using the selected skill's format; do not require JSON from a
prose reviewer or infer completion merely from an empty findings list. Preserve
the original bytes, normalize completion/findings, and record the task result
through the existing [task-state contract](task-state.md). Include the handle and
observed native outcome in operational progress. An empty output can be retained
for failed/incomplete work, but cannot be recorded or reused as complete.

Before accepting a result, recapture its governing inputs. A changed task needs
fresh review; a host success for stale inputs does not supply current coverage.
Required reviewers must all complete; successful siblings remain reusable when
another reviewer fails. Completion with findings is distinct from merge readiness.

## Resume and retry

After interruption, inspect the retained claim and native handle first. If the
agent is active, continue waiting. If its status cannot be established, retain
the unresolved claim. Once it has stopped, reconcile any domain publications,
record its actual failure/incomplete output or explicitly recover that claim,
and launch any authorized retry with a new handle. Preserve the prior result and
existing retry/cycle counters. Never reinterpret a later success as evidence that
the earlier attempt succeeded. Current-input task reuse still uses normal cache
validation; CI and publication remain fresh checks on the final head.
