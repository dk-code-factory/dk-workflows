# Native reviewer handoffs

Use the host's native agent tools to invoke selected reviewer skills. A complete review requires successful host execution and a complete skill report with findings and coverage. This workflow trusts the host and selected skill; it does not independently prove that every instruction was followed.

## Launch and wait

1. Resolve the skill and pin task inputs before launch. Prepare immutable context and output paths outside the source checkout. Supply the exact task, pinned source, original guidance, resolved resources, permitted actions and required completion report. Keep source inspection read-only. The skill manages its private state and lifecycle.
2. After `start-batch` returns `invoke`, launch the reviewer with native tools. Save its handle with the task key and claim id in private operational progress, outside fingerprinted inputs. Let the skill manage its internal passes and required resources.
3. Wait on that handle for a terminal host outcome. Running, queued, observation timeout and progress-only responses are incomplete. Save the terminal outcome and original final output. If native delegation is unavailable, report incomplete. Do not substitute a model CLI or invent a handle.

## Record the outcome

| Native outcome | Skill report | Task result |
| --- | --- | --- |
| Successful terminal result | Nonempty, complete, valid report | Complete; retain findings |
| Successful terminal result | Incomplete, missing or malformed report | Incomplete with reason |
| Failed or cancelled | Any report, even one claiming completion | Failed; retain available output |
| Running or unknown | Any partial report | Outstanding; reconcile the original agent before recovery |

Use the selected skill's report format. Do not require JSON from a prose reviewer or infer completion from an empty findings list. Preserve original bytes, normalize completion and findings, and record through [task state](task-state.md). Save the handle and observed native outcome in operational progress. Failed or incomplete tasks may have empty output; complete tasks cannot be recorded or reused with empty output.

Recapture governing inputs before accepting results. Changed inputs require fresh review. Host success on stale inputs does not provide current coverage. All required reviewers must complete. Completed tasks remain reusable when another fails. Findings in a complete report may still block merging.

## Resume and retry

After interruption, inspect the saved claim and handle. Continue waiting if the agent is active. If its status is unknown, keep the claim unresolved. Once it has stopped, reconcile its publications. Record its actual failed or incomplete output, or explicitly recover the claim. Launch any authorized retry with a new handle.

Preserve the prior result and retry and cycle counters. Later success does not make an earlier attempt successful. Reuse still requires normal cache validation against current inputs. CI and publication require current checks on the final head.
