# Native reviewer handoffs

Follow [private workflow reporting](private-reporting.md). Operational reports, blockers, diagnostics, handles and receipts go only to the invoking user or private caller, never into external text. Pass this governing contract downstream; publication permission does not authorize disclosure.

Use the host's native agent tools to invoke selected reviewer skills. A complete review requires successful host execution, a complete skill report, and coordinator-captured host observations establishing the selected skill's required independent passes. Deeper-look commands require successful host observations when available, or exact completion reports in each original child terminal output when command transport is unavailable. Exit success, aggregate `complete:true`, valid findings JSON, and an empty findings list do not establish execution coverage.

The coordinator is the trusted collector of native observations. The store checks their structure, invocation and input bindings; it cannot authenticate arbitrary supplied JSON or prove semantic adherence to every instruction. Reviewer-written role files or an aggregate claim are not host observations. Missing native child identities or terminal results mean incomplete coverage. Missing command telemetry alone permits the reported-driver path below; reported evidence never becomes a host-observed execution record. Do not launch model subprocesses to replace unavailable native evidence.

Reviewers and independent finding verifiers use the same [invocation contract](invocations.md) as process agents, supporting skills, hooks and drafting. Preserve the custom prompt and skill options, and honor configured `agent.model` and `agent.reasoning_effort` through native launch controls. Verify host support before claiming work. A fixed native role or unavailable model that prevents honoring a request is a blocker; do not silently inherit different settings. Save the requested invocation and observable actual execution settings with the handle. Follow host restrictions on context inheritance when selecting another model.

## Evidence capability preflight

Before substantive reviewer dispatch, claims or review-cycle consumption, resolve
requirements from the installed selected skill and all governing procedures.
Include conditional passes applicable to the pinned change; use the same contract
in the eventual task definition. Do not infer transport support from delegation
alone, a skill report, or an offline fixture. The native coordinator must verify
that the current host exposes the required observations.

Both `scripts/config.py preflight` and `skill-preflight` accept
`--native-reviews REVIEWS.json` alongside `--capabilities HOST.json` and
`--available SKILLS.json`. `REVIEWS.json` maps each selected occurrence's exact
location to its schema-1 `native_review` contract (resolved version and applicable
passes/driver commands). Missing-contract diagnostics return occurrence locations;
Python integrations can enumerate them with `config.calls` or `skill_config.calls`.
For example, a standalone reviewer occurrence can be keyed as:

```json
{
  "dk-workflows:dk-pr-reviewers-run/reviewers/0/0": {
    "schema": 1,
    "skill_version": "1.0.7",
    "passes": [{"role": "toolkit", "drivers": [["python3", "/installed/aspect-driver.py", "--pass", "toolkit"]]}]
  }
}
```

This illustrates the shape, not a complete requirement set for a particular
installation. Supply every required pass and the exact commands with pinned
arguments. Repeated occurrences need separate entries because their options and
conditional requirements can differ. Include independent finding-verification
occurrences explicitly even when they use instruction-only assignments or skills
without a reviewer name. Ordinary tasks do not need evidence contracts.

A host capability document can include:

```json
{
  "delegation": true,
  "native_evidence": {
    "availability": "available",
    "inventory_complete": true,
    "invocation_results": true,
    "child_results": true,
    "tool_observations": true
  }
}
```

These are coordinator-verified current capabilities, not execution receipts.
`inventory_complete` means the host exposes the complete child/tool inventory,
including child identities and parent relationships. `invocation_results` covers
root terminal status and original output; `child_results` covers each required
child's spawn, terminal outcome and separate original output. `tool_observations`
covers exact command/tool-call identity, session, outcome and original output.
All reviews require verified delegation, available native result transport
and root results. Child results are required when the contract has passes. When
`tool_observations:true`, complete inventory is also required and completion uses
strict command observations. When command observations are absent or false,
preflight permits the reported-driver path; the coordinator must verify that
command logs are unavailable before selecting that path at completion. Unknown or false required
capabilities produce `ready:false` and `unsupported_native_reviews` diagnostics,
including missing capabilities and a conditional supported path. Optional selected
reviewers receive the same check. Require `ready:true` before claiming new work.

Capabilities and requirements are transient reads: refresh them when the host,
selected invocation, installed resources or pinned applicability inputs change.
Do not persist a readiness cache or reset retained work on failure. Preserve
original findings, reports, completed siblings, unresolved handles and consumed
budgets, and reconcile them under the normal resume rules. If no supported host
transport is available, report blocked/incomplete before new review work; do not
launch a model process as a workaround. Preflight cannot establish that future
execution happened; the existing claim-bound completion guard remains mandatory.

## Launch and wait

1. Resolve the skill and pin task inputs before launch. Use responsibility `content-review` for configured reviewers, and `finding-verification` for independent verification. Prepare immutable context and output paths outside the source checkout. Supply the exact task, pinned source, original guidance, resolved resources, permitted actions and required completion report. Keep source inspection read-only. The skill manages its private state and lifecycle.
   Include the complete [preference handoff](preferences.md#workflow-handoffs-and-reuse), retaining matched paths and conditional decisions. Reviewers and finding verifiers apply records only within that scope and request refreshed selection before expanding paths.
   Include a `native_review` contract in the task definition before claiming: schema `1`, resolved `skill_version`, and the complete applicable `passes` list. Each pass has a unique `role` and `drivers`, a list of exact argument arrays for its required deeper-look commands. Derive requirements from the installed skill and referenced procedures; capture those resources. Resolve conditional passes from pinned evidence and retain the applicability decision in governing inputs. An empty pass list is valid only when the selected skill requires no independent children; it still requires successful native root evidence. Do not weaken requirements after execution to match observed work.
2. After `prepare` returns `invoke`, launch the reviewer with native tools. Save its actual handle with the key and claim in private operational progress, outside fingerprinted inputs. Let the skill manage its internal passes and resources.
3. Wait on that handle for a terminal host outcome. Running, queued, observation timeout and progress-only responses are incomplete. Save the terminal outcome and original final output. Collect each required child's actual spawn observation, distinct native handle, parent relationship, successful terminal observation and separate original output. When command logs are available, collect each required driver's successful completed tool observation in that same child session. Otherwise require each pass to include the exact completion report described below in its own terminal output. Reading or printing a driver's instructions does not establish driver execution. If native delegation or required result transport is unavailable, report incomplete. Do not invent a handle or reconstruct completed-session provenance.

## Capture host observations

Use prepare's `native.binding` and `native.requirements` for the active claim; these expectations create no execution evidence. Low-level integrations may still call `native-binding --spec REVIEW.json --claim CLAIM_ID`. Copy binding fields into a private observation object, then populate only observed host facts:

```json
{
  "availability": "available",
  "inventory_complete": true,
  "invocation": {
    "handle": "ACTUAL_NATIVE_HANDLE", "state": "completed",
    "output_sha256": "SHA256_OF_ORIGINAL_FINAL_OUTPUT",
    "receipt": "Original terminal host observation"
  },
  "passes": [{
    "role": "toolkit", "handle": "ACTUAL_CHILD_HANDLE", "parent": "ACTUAL_NATIVE_HANDLE",
    "state": "completed", "output": "Original separate role result",
    "spawn_receipt": "Original native spawn observation",
    "terminal_receipt": "Original terminal host observation",
    "drivers": [{
      "argv": ["python3", "/installed/aspect-driver.py", "--pass", "toolkit", "--base", "BASE", "--head", "HEAD"],
      "handle": "ACTUAL_CHILD_HANDLE", "call_id": "ACTUAL_TOOL_CALL_ID",
      "state": "completed", "exit_code": 0, "output": "Original completed driver output",
      "receipt": "Original native tool observation"
    }]
  }]
}
```

The example omits binding fields for readability; all binding fields are required. Include every applicable role and its drivers in declared order. Host handles and tool-call identities must be distinct. Preserve exact evidence bytes with `finish --native-evidence OBSERVATIONS.json`. Unsupported/unavailable transport remains incomplete. Set `inventory_complete:true` only after observing the complete required inventory.

### Hosts without command logs

After verifying that the host exposes root and child native results but no command
observations, set `tool_observations:false` in this same evidence object. Omit
`inventory_complete` unless actually established, and omit each pass's `drivers`
when command observations are unavailable. Keep every binding, root observation,
pass identity, parent, spawn/terminal receipt and separate original output above.
An omitted `tool_observations` field preserves legacy strict validation; unknown
command availability is not permission to select reported evidence. Available
command transport must use strict validation even if a report would pass.

For each pass with required drivers, ask that native child to include exactly one
line in its own original final output, after actually completing the commands:

```text
NATIVE_REVIEW_DRIVERS_JSON=[{"argv":["python3","/installed/aspect-driver.py","--pass","toolkit","--base","BASE","--head","HEAD"],"state":"completed","exit_code":0}]
```

The list must match that role's captured driver argument arrays in declared order,
with successful completion for every command. A root summary, coordinator-created
marker, other pass's output, failed command or duplicate marker cannot substitute.
Passes without drivers need no marker. This is **pass-reported driver completion**,
distinct from **host-observed command execution**. Any supplied `drivers` still
undergo full strict validation; partial, malformed or failed observations block
completion rather than falling back to the report. Store exact original outputs
and evidence in the existing result artifact; do not add another report registry.

## Record the outcome

| Native outcome | Skill report | Task result |
| --- | --- | --- |
| Successful terminal result with all required native results and valid observed or reported driver evidence | Nonempty, complete, valid report | Complete; retain findings |
| Successful terminal result without established required execution | Any report, including empty findings and `complete:true` | Incomplete; retain findings, original report and diagnostic |
| Successful terminal result | Incomplete, missing or malformed report | Incomplete with reason |
| Failed or cancelled | Any report, even one claiming completion | Failed; retain available output |
| Running or unknown | Any partial report | Outstanding; reconcile the original agent before recovery |

Use the selected skill's report format. Do not require JSON from a prose reviewer or infer completion from an empty findings list. Preserve original bytes, normalize completion and findings, and record through [task state](task-state.md). Save the handle and observed native outcome in operational progress. Failed or incomplete tasks may have empty output; complete tasks cannot be recorded or reused with empty output. The store downgrades a requested completion to incomplete when native evidence is missing, mismatched or incomplete, retaining the originally requested normalized result, findings and exact evidence. Check the returned status. Previously completed reviewer artifacts without this evidence remain retained but cannot supply current coverage.

Recapture governing inputs before accepting results. Changed inputs require fresh review. Host success on stale inputs does not provide current coverage. All required reviewers must complete. Completed tasks remain reusable when another fails. Findings in a complete report may still block merging.

## Resume and retry

After interruption, inspect the saved claim and handle. Continue waiting if the agent is active. If its status is unknown, keep the claim unresolved. Once it has stopped, reconcile its publications. Record its actual failed or incomplete output, or explicitly recover the claim. Launch any authorized retry with a new handle.

Preserve the prior result and retry and cycle counters. Later success does not make an earlier attempt successful. Reuse still requires normal cache validation against current inputs. CI and publication require current checks on the final head.

## Bounded current-host compatibility check

Offline fixture observations test the validation contract, not native skill adherence. For an explicitly authorized native exercise, use one pinned review and one retained cycle outside production state. Resolve the installed reviewer and declare its actual required roles and driver commands. Claim once, invoke the skill through native tools, and retain the real root and child terminal outputs and available tool observations. Do not run model CLIs or invent missing host records.

Record the actual evidence with the original report. A host exposing the complete required observations can establish completion, including a valid empty findings report. A host exposing native child results but no command logs can establish completion using the reported-driver path; label its driver evidence as reported, never host-observed. Missing native identities, terminal outcomes or required driver reports remain incomplete. Check that findings and original outputs remain retained and that the consumed cycle remains unchanged. Record host/skill versions, pins, observed handles, actual returned status and evidence limitations privately. Such an exercise establishes only that transport path; offline fixtures or an aggregate report alone do not prove complete native compatibility.
