# Engineering preparation

Read this procedure before initial iteration work (including dirty-change handling,
rebasing and initial feedback fixes), and before standalone review. Standalone
preparation is read-only with respect to the repository and GitHub: retain human
answers locally and make any difference from published scope explicit.

## Evidence and invocation

Assemble verified Decisions context with local choices first. Discover the effective
configured reviewer prompts with `run_reviewers.py --context CONTEXT --describe`.
Save each returned descriptor as its own JSON artifact. A missing material procedure
blocks adaptation; do not infer its instructions from a skill name.

Invoke the existing Decisions CLI:

```sh
python3 dk-pr-decisions/scripts/decisions.py prepare --input-manifest MANIFEST.json --output-dir PRIVATE_DIR
```

The manifest contains `mode` (`iteration` or `standalone`), paths `context` and
`decisions`, and an `evidence` object. Paths are relative to the manifest or absolute.
Every evidence category is a nonempty array of original artifact paths:

- `issues`: linked issues/specifications, including their complete source text.
- `feedback`: complete inline threads, review bodies and general comments.
- `choices`: active Decisions, superseded history, unpublished choices and sources.
- `user_instructions`: exact current instructions, constraints and human answers.
- `run_history`: prior assessments, counts, events and operational history.
- `questions`: unanswered questions and affected dependent work.
- `prior_preparation`: previous preparation JSON artifacts on refresh/resume.
  When there is no previous preparation, supply a JSON object such as
  `{"verified_absence": true, "reason": "No previous preparation exists for this run"}`.
  Plain-text absence files are not accepted for this category.
- `rules`: applicable repository rules and conflict resolutions.
- `production`: owning modules, dependencies, callers and analogous implementations.
- `tests`: test layout, fixtures, commands and required coverage.
- `ci`: applicable CI configuration and repository check requirements.
- `reviewers`: one complete effective descriptor JSON per configured reviewer.

For an absent category, supply an artifact stating the verified absence or remaining
uncertainty. An empty issues file can establish that no linked issue exists; it cannot
establish unsupported inputs. Original evidence remains available. Summaries provide
navigation; inspect exact sources for material conclusions and contradictions.

Preparation invokes a read-only semantic analysis. It never publishes or edits code.
Invalid model JSON or analysis fields receive one correction attempt with the error
and prior response. A second invalid response blocks preparation; do not coerce
fields or substitute a local analysis to make the readiness gate pass.
The existing API v2 envelope returns `data.artifact`, `data.preparation_id` and
`data.readiness`. The separately versioned artifact (`preparation_version: 1`) binds
PR URL, base/head, body and complete Decisions hashes to source fingerprints,
reviewer descriptors and analysis. Store it outside the repository; each version has
retained evidence copies. `verified: true` remains a provenance claim only.

## Analysis JSON contract

Return an object with these fields. Evidence references use the input index key,
optionally followed by an exact locator such as `production/0#L42-L65` or
`feedback/0#discussion_r123`. Each conclusion needs inspected original evidence.

`boundaries` maps each of `correctness`, `remediation_depth`, `performance`,
`compatibility`, `complexity` to a nonempty array of conclusions. A conclusion is:

```json
{"text":"Concrete engineering boundary", "agreement":"established", "material":false,
 "rationale":"Why this follows from these sources", "evidence":["user_instructions/0#L1"]}
```

Agreement is `established` (explicit agreement), `interpretation` (grounded routine
choice), or `proposal` (not yet agreed). Do not turn observed behavior into intended
scope. Correctness covers supported inputs/environments, failure cases, threats and
explicit exclusions. Depth names allowed changes and reconsideration triggers.
Performance names relevant workloads/limits and whether measurement or a dedicated
review is justified. Compatibility identifies supported versions, interfaces,
configurations and any permitted breaks. Complexity explains maintenance/diagnostic
cost, acceptable restrictions and justified generalization.

`placement` contains `production`, `tests`, `reuse`, `entrypoints`, `dependencies`.
Each value is a conclusion with an additional nonempty `locations` array naming
concrete candidate files/modules (or explicitly explaining an inapplicable boundary).
Inspect conventions first. Identify the owning subsystem, primary implementation,
existing helpers and fixtures, thin callers/adapters and dependency direction.
Justify new modules by responsibility. Avoid scattered logic, copied helpers,
one-use generic infrastructure and convenient growth of broad modules. Routine
placement remains local; ownership/interface/dependency changes use Decisions.

`test_strategy` is a nonempty array of compact behavior mappings, each with string
fields `behavior`, `existing`, `missing`, `level`, `location`, `command`, `rationale`,
`limitation`, and an `evidence` array. Use explicit `none` where appropriate. Level
is `inspection`, `unit`, `integration`, `system`, or `performance`; use separate rows
when levels establish distinct contracts. `command` may be a documented procedure.

Choose the smallest meaningful coverage that distinguishes correct behavior from
the relevant failure. A pure function may need only units. Wiring, subprocess args,
environment, persistence or serialization may need focused integration through a
temporary repo, real subprocess or controlled service substitute. Use existing system
paths only when essential workflow behavior requires them. Preserve mandatory repo
checks. Reuse existing integration coverage; do not demand integration merely because
units exist or require live network access. Cover meaningful success/failure and
compatibility cases. Avoid fixed counts, invented percentages, exhaustive unsupported
matrices, mirrored implementation tests and duplicate coverage. Performance tests
need a relevant obligation. Record what available checks cannot prove.

`questions` retains every open prior question and resolved contradictions, as objects:

```json
{"id":"stable-question-id", "status":"open", "material":true,
 "statements":"Exact conflicting statements with source locators",
 "affected_work":"Dependent behavior/fixes/review",
 "options":[{"choice":"Option 1", "benefits":"Benefit", "costs":"Cost"},
            {"choice":"Option 2", "benefits":"Benefit", "costs":"Cost"}],
 "recommendation":"Supported recommendation and rationale",
 "proposed_changes":"Scope/Decisions/placement/validation changes",
 "evidence":["feedback/0#discussion_r123"]}
```

Resolved questions also require `resolution` with exact answer or precedence/
supersession evidence and reasoning. Resolution cannot be inferred from silence,
elapsed time, resolved threads, existing code, or a clean review. Preserve exact human
instructions and rationale in the source artifacts. Material proposals or open
material contradictions produce `needs_decision`. Use the existing independent
material-choice check and [decision-question format](../../dk-pr-iter/references/decision-question.md).
Pause affected work; continue independent evidence gathering.

`reviewer_addenda` maps every configured reviewer name to tailored instructions.
Deliver identical shared boundaries and Decisions, applying them to the reviewer's
assigned axes/aspects. Explain actionable correctness cases, performance and
compatibility obligations, settled/open placement, required and existing coverage,
concrete missing integration evidence, and remedies exceeding depth/complexity.
Preserve read-only behavior, finding thresholds and output contracts. Defect reviewers
identify defects and required outcomes; do not make them implementation designers.

`worker_instructions` specifies governing boundaries, selected remedy, principal
code/test locations, reusable helpers/fixtures, behavior/scenarios, commands and
stopping criteria. Workers must reconsider differing ownership, interactions or
coverage discovered during implementation. Record routine adjustments with rationale;
return material changes to the coordinator. `completion_criteria` checks reproduced
behavior, placement, sufficient tests, compatibility and cumulative design cost.
Passing tests and clean reviewers alone cannot establish proportionate complexity.

`sync_required` is a boolean; `sync_reason` explains any pending synchronization.
In iteration, material human-agreed changes must appear consistently in scope prose
and Decisions before dependent work resumes. Routine planning stays local. In
standalone mode retain explicit local differences without publishing; this alone
does not require synchronization. Unresolved mandatory publication produces
`sync_pending` even when a reviewer has no findings.

## Human answers and synchronization

Preserve the exact answer and rationale; add a Decision or explicitly replace one,
retaining history. Draft and review affected description prose with dk-pr-description.
Pass that body as `publish --description-file BODY --description-review-state TOKEN`.
The Decisions publisher composes the single final body, retaining active Decisions,
replacement history and unrelated content; it compares live head/title/body, journals
intent, performs one guarded edit and verifies the result. Preserve the title unless
an agreed change requires using the description workflow to change it separately.
Do not write scope and Decisions with competing body writers. Describe intended
behavior without claiming unfinished implementation exists.

On failed/uncertain publication retain `sync_pending`, inspect live state and reconcile
before retrying; a verified already-applied body must not be written again. Reconciliation verifies only the earlier attempt: newer requested choices or reviewed prose keep the current request pending until separately published. Refresh
the context and preparation after publication or a human answer even at unchanged
code head. Local answers and assessments survive resumes and scope changes.

Consumers must validate readiness **and all fingerprints**, including user choices,
rules and reviewer prompt sources. Review/initial-work/completion gates reject stale,
unresolved or pending preparation. Changes invalidate dependent preparation; refresh
without resetting cycle, repair, retry or publication history. A custom completion
prompt cannot bypass these gates. Standalone reviewer skills invoked directly remain
outside this preparation workflow.

## Work selection and completion evidence

Use `run_state.py assess` for a feedback reference/head/preparation ID, then obtain
`fix-plan --ref REF --head SHA` before assigning a fix. Only `action: fix` permits
that handoff. An excluded valid defect produces `skip`; a material hold produces
`decision_blocked`. The historical validity classification remains unchanged. The
pre-existing 20-line limit is applied after scope, never as an exemption from it.

Before the review or complete completion gate, inspect the implemented behavior,
placement, sufficient test evidence, compatibility, and cumulative design cost
against `analysis.completion_criteria`. Save a verification file outside the repo:

```json
{"verification_version":1, "preparation_id":"CURRENT_ID", "pr_url":"PR_URL",
 "head_sha":"CURRENT_SHA", "checks":{
   "behavior":{"satisfied":true,"rationale":"Reproduced the required behavior",
     "evidence":[{"path":"/absolute/path/to/evidence","sha256":"FILE_SHA256"}]},
   "placement":{"satisfied":true,"rationale":"Owning module and callers inspected",
     "evidence":[{"path":"/absolute/path/to/evidence","sha256":"FILE_SHA256"}]},
   "test_sufficiency":{"satisfied":true,"rationale":"Required behavior mappings established",
     "evidence":[{"path":"/absolute/path/to/evidence","sha256":"FILE_SHA256"}]},
   "compatibility":{"satisfied":true,"rationale":"Supported and permitted changed contracts checked",
     "evidence":[{"path":"/absolute/path/to/evidence","sha256":"FILE_SHA256"}]},
   "complexity":{"satisfied":true,"rationale":"Cumulative abstraction and test costs justified",
     "evidence":[{"path":"/absolute/path/to/evidence","sha256":"FILE_SHA256"}]}
 }}
```

Evidence may include focused inspection reports, exact code artifacts, and test
results; retain them with their fingerprints. Mark unestablished criteria false and
report their limitations. A clean review or green CI cannot substitute for the
assessment of complexity, placement, compatibility or sufficient coverage.
Pass `--preparation-file PREPARATION.json --verification-file VERIFICATION.json --run-record RUN_RECORD.json` to
both completion stages. The gate reads the current mutable run record and requires its active preparation and local choices to match; an immutable history snapshot cannot substitute for this check. Missing, false, stale or changed evidence blocks the gate.
The coordinator and completion agent consume the same verification and refresh it
when preparation or implementation changes, preserving earlier versions.
