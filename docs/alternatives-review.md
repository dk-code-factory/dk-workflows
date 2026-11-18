# Shared PR assessment and alternatives

## Shared assessment

Apply [decision reconciliation](decision-reconciliation.md) during requirements
collection and before dependent implementation. Supply effective commitments and
arbitrary user-directed revisions, conditions and exceptions as scoped inputs.
Keep original selections authoritative for analysis; reference them from necessary
conflict resolutions instead of making duplicate decision records. Advised
alternatives can also conflict with an agreement before selection is settled.

After collecting requirements and repository evidence during initial inspection,
the controller scripts (`pr_assessment.py` and `assessment_alternatives.py`)
inject the assessment prompt into native assignments. There is no separate
assessment skill to invoke or configure. Both alternatives skills use the same
default prompt and consume the same original PR-wide assessment result as historical design context across sessions, routes and guidance changes. Its stable identity is independent of
workflow, run, cycle and head. Persist purpose, scope, scoped rubrics, rationale,
mandatory constraints and existing source references, without copying PR metadata,
discussion, settings or rendered reports.

Each alternatives skill owns `assessment_prompt`, optional `evaluation_prompt`
(default `""`), `scoring` guidance and `selection` settings. Configure
`skill_defaults["dk-workflows:dk-issue-alternatives"].options.selection.mode` for
issue/implementation comparisons and
`skill_defaults["dk-workflows:dk-pr-alternatives"].options.selection.mode` for
architectural PR comparisons. These modes are independent. For example:

```json
{
  "skill_defaults": {
    "dk-workflows:dk-issue-alternatives": {
      "options": {"selection": {"mode": "automatic"}}
    },
    "dk-workflows:dk-pr-alternatives": {
      "options": {"selection": {"mode": "manual"}}
    }
  }
}
```

Read a skill's effective policy with `alternatives.py --repository HOST/OWNER/REPO
--skill dk-workflows:dk-issue-alternatives policy` (or `dk-pr-alternatives`). Both
use the ordinary user, upstream and repository configuration scopes. Former
`dk-pr-assessment` options and owning-workflow `alternatives.scoring`/`auto_pick`
remain read-only compatibility fallbacks for the corresponding skill. Explicit
canonical values take precedence at their scope; conflicting ambiguous legacy
values require a canonical override. `review_skill` remains available for old
managed-stage recovery. Changed assessment/evaluation guidance and mode-only changes preserve completed
original analysis and choices. Current requirements need live alignment; insufficient
decisions need a direct user invocation of dk-pr-review-alternatives.

Return `purpose`, `scope`, `work_type:small_fix|feature|mixed`, `rationale`,
`mandatory_constraints:[strings]`, `evidence:[source references]`, and `rubrics`.
Each rubric has unique `id`, `scope`, `work_type:small_fix|feature`, `rationale`,
`evidence`, `weights:{criterion:positive integer percent}` totaling 100, and
`anchors:{criterion:{"0":text,"50":text,"100":text}}`. Small fixes normally favor
focused implementation changes and relevant edge-case coverage. Feature rubrics
exclude change-volume ranking, including `low_loc` and `low_intrusiveness`;
design and maintainability are judged by actual consequences. Mixed work assigns
rubrics to its identified scopes. The model selects and explains appropriate
criteria; architectural analysis has no predefined defect checklist.

## Current managed protocol

Use `scripts/alternatives.py --repository HOST/OWNER/REPO --pr URL plan
--workflow fix|review` to discover missing work or accepted original references.
Optional `--options FILE`, `--alternatives-options FILE` and `--upstream` precede
the subcommand. `--options` contains owning-workflow options;
`--alternatives-options` contains invocation-local options of the corresponding
alternatives skill, such as `{"selection":{"mode":"manual"}}`. The former
`--policy-options` input stays readable for recovery. Preserve explicit skill
invocation options downstream as `alternatives_skill_options` when needed;
do not save an expanded settings snapshot.

The helper returns a compact `next_task`. Add or complete its ordinary explicit
`definition` with installed skill identity/resources, original instructions,
complete governing input references, pinned `git:{checkout,target,base,head}` and
actual `native_review` contract. Execute through managed `dk.py prepare` and
`finish` under the existing [task contract](task-state.md). Definitions select
`kind:assessed-alternatives`, owning `skill:dk-workflows:dk-pr-fix|dk-pr-review`,
`stage`, optional `decision` and `dependencies` mapping stage labels to accepted
task keys. The helper constructs assignments, captures inputs, claims work and
records original output and checklist evidence. The already-running native agent
performs assessment, comparison and selection; no separate selector is required.
Unavailable native execution or evidence leaves affected work incomplete.

1. `assessment` has no dependencies. The controller injects its prompt directly;
   no assessment skill needs to be installed or invoked.
2. `inventory` depends on `assessment`. Return `coverage`, `evidence`, and
   `decisions:[{id,origin,scope,rubric,decision,questionable,reason,evidence}]`.
   IDs are stable alphanumeric identifiers with dots, underscores or hyphens.
   Origins are `inherited`, `introduced` or `materially_changed`. Explain which
   architectural decisions are questionable under the applicable persisted rubric.
3. For each questionable decision, `comparison` depends on `assessment` and
   `inventory`, with its `decision` ID. Return `rubric`, `target_revision`,
   `alternative_coverage`, `evidence` and `solutions` using the comparison solution fields below. The helper supplies the persisted
   rubric; do not select another profile or replace its weights/anchors.
   Every option uses the same pinned **target revision**, counting implementation
   work already on the PR branch. Include retaining the current implementation
   (`kind:pr`) and a smallest viable approach (`kind:smallest`). If they coincide,
   explain that and include a materially distinct alternative. Helpers calculate
   weighted totals using the same implementation for both skills.
4. Each `selection` depends on `assessment` and its `comparison`. Return the
   existing automatic or sourced manual selection fields below. The assignment
   receives transient helper totals as `alternatives_scores`. A manual source
   must be an original input path bound in `definition.inputs.user_choice` and cited in
   `evidence`. Keep final rationale and rejection reasons for every unchosen
   option. A choice grants no additional edit or publication permission.
5. `interactions` depends on `assessment`, `inventory` and exactly every settled
   `selection_DECISION_ID`. Return `compatible:bool`, `reason`, `evidence` and
   substantive `required_changes` when incompatible. Check the combination of
   selected approaches and retained decisions. Pending choices or interactions
   block dependent stages.
6. `alignment` depends on `assessment` and `interactions`. The helper also
   supplies the original rubric, choices and comparison references to alignment
   and independent verification, so a fresh native agent can judge the selected
   approaches without reconstructing their dependency chain. Return
   `alignment:aligned|mismatch|uncertain`, `reason`, `evidence` and substantive
   `required_changes` for mismatch. Reference the existing PR metadata artifact
   in `definition.inputs.alternatives_current_pr`; keep original inputs stable.
7. Incoming material mismatch retains the independent native `verification`
   stage, depending on `alignment`, and the existing publication boundary.
   Return `disposition:confirmed|rejected|uncertain`, `reason`, `evidence` and
   substantive `required_changes` when confirmed. Distinct native handles remain
   mandatory. An authorized rejection publishes exactly the verified changes as
   `REQUEST_CHANGES` without ordinary inline findings. Pass the reconciled
   operation to `plan --workflow review --review-operation PATH`. Same-head
   rejection completes alternatives only; after author commits, its original
   required changes become context for ordinary current-head review.

Every report includes `complete`, `findings` and the prepared checklist. Missing,
stale, damaged or incomplete evidence cannot release downstream work. Definitions,
claims, original results, checks, retained limits and receipts use the existing
private task store and publication journals. No completion marker is added.

## Comparison solution fields

Each solution has unique `id`, `kind:pr|smallest|alternative`, `approach`,
`components`, `pros`, `cons`, `handled_edges`, `unhandled_edges` with consequences,
`evidence`, `complexity` and `intrusiveness` integers from 0 to 100 with
`complexity_reason` and `intrusiveness_reason`, `feasible:bool`,
`feasibility_reason`, `material_doubts:[strings]`, `recommended:bool` and
`recommendation_reason`. `production_loc:{min,expected,max,reason}` contains
ordered nonnegative integer added plus deleted production lines against the
pinned target, including executable infrastructure configuration and excluding
tests, documentation and generated output. Estimates inform actual consequences;
feature ranking still excludes change volume.

`ratings` and `rating_evidence` cover exactly the persisted rubric criteria.
Ratings are integers from 0 to 100, higher is better. Optional totals must equal
sum(weight × rating)/100. Native selection returns `mode:auto`, `choice:ID|null`,
`confident:bool`, `sensitivity`, `evidence` and, for a sole feasible option,
`rejected_alternatives`. Sourced manual selection returns `mode:manual`, `choice`,
`user_source`, `reason` and `evidence`. Every settled choice has final `reason`
and `rejection_reasons` for every unchosen evaluated option.

## Selection modes

Each alternatives skill owns `selection.mode`, defaulting to `semi-manual`.

| Mode | Behavior |
| --- | --- |
| automatic | Select a qualifying clear winner. If checks cannot settle it, return null without prompting or implementing that decision. |
| manual | Present evaluated options and require an explicit sourced user choice of a feasible option. |
| semi-manual | Select a qualifying clear winner; ask only when checks cannot settle it. |

Feasibility, confidence, estimate sensitivity, unresolved material doubts, unique
winner and configurable score lead (default 10 points) still apply. The existing
`selection.enabled` compatibility switch disables automatic picking; sourced
manual choices remain available in manual and semi-manual modes. An unresolved
choice retains `choice_pending` and blocks dependent work. Automatic mode-only changes preserve settled selection and original analysis.
A direct dedicated manual request can refresh the affected selection.
When the selected options and original comparisons stay unchanged, interaction
and alignment evidence also remains reusable. Definitions retain only consumed
original evidence references for that check; no choice/status copy is saved.
Added or changed inventory siblings preserve unchanged decision comparisons.
Do not ask for a choice in automatic mode. Elapsed time never supplies a choice.

## Fix workflow once per PR

`dk-pr-fix` uses `dk-workflows:dk-issue-alternatives`. Start from
issue requirements and compare whole-problem implementation approaches, accepting
an existing PR as an optional candidate. Complete shared assessment and initial
alternatives once before the repeated task list with `plan --workflow fix`.
Pass settled choices, interaction evidence and required changes into authorized
implementation tasks together with sourced decision reconciliation and user
exceptions; resolve governing conflicts before applying selected approaches and
run relevant tests. Reuse applicable user answers across sessions. The separate
reconciliation policy governs questions about existing agreements even when
automatic ranking cannot ask for a selection. Retaining
the current approach requires no corresponding edit. Selection does not authorize
editing or publication. Current code review, verification, CI and publication
checks remain independent live boundaries.

## Incoming workflow once per PR

`dk-pr-review` uses `dk-workflows:dk-pr-alternatives`: inventory architecture
throughout the branch, including inherited decisions, then compare questionable
decisions under the shared assessment. Use `plan --workflow review` before its
configured task list. This path reports recommendations without changing code or
commits and never adds a PR description comparison. Architectural recommendations
are governed by the rubric, with existing independent verification and publication
boundaries; this introduces no separate defect-oriented gate.

Both workflows reuse their accepted initial analysis across ordinary
implementation commits, invocations and fresh sessions. Current scope must still
fit retained decisions; changed requirements need live alignment, and insufficient
decisions need a direct user invocation of dk-pr-review-alternatives. Assessment
guidance changes alone do not invalidate historical acceptance.
The helper's read-only next-work output never claims current-head code review,
CI, authorization or publication readiness. Disabling alternatives retains all
evidence and bypasses alternatives stages.

## Manual alternatives review

Only a direct user invocation of `dk-workflows:dk-pr-review-alternatives`
authorizes a fresh comparison. The delegated `dk-pr-alternatives` execution, a
downstream call, another iteration, changed fingerprints and installed guidance
updates do not authorize another comparison. The dedicated entry delegates execution without extending the sourced permission. Preserve the actual request artifact with nonempty `id`,
`source`, exact `instruction`, `skill:"dk-workflows:dk-pr-review-alternatives"`
and `origin:"user"`. The native session validates the direct invocation; these
fields record its source rather than infer permission from PR text.

Both automatic routes consult modern and legacy task history before proposing
new native work. Historical acceptance validates original immutable bytes,
captured fingerprint, native observations and the original completion checklist
without recapturing removed installations. Released context explicitly returns
`design_context:true` and `current_alignment_required:true`. It never adopts old
instructions as current governing rules. Missing/damaged evidence returns
`recovery_required` with its original task key; unfinished claims and checklists
keep their existing recovery action and allowances. Restore missing artifacts and
finish missing deliverables without restarting completed comparisons.

Automatic settings or guidance changes preserve settled original analysis and
choices. Current tasks still assess requirements and implementation alignment
under current sources. Insufficient decisions require the dedicated direct
invocation; historical context is never current code-review, CI, authorization or
publication evidence. Current tasks retain their separate live source and publication boundaries.

Each distinct direct request can refresh requested inventory/comparisons while
reusing the original shared assessment as historical context. Preserve its stable
identity on interruption and completed siblings on resume. A completed stage
cannot rerun under the same identity just because guidance changed. New requests
never renew retained budgets.

Invoke the dedicated entry for a fresh requested analysis; it delegates native work
to dk-pr-alternatives under the same original direct request. Reuse an unchanged
shared assessment. Pass `--request FILE` to `plan`, binding the existing original
request artifact with nonempty `id`, `source` and exact `instruction`; preserve
that actual message identity on recovery. Add current Git pins and evidence to
returned definitions. Requested inventory/comparisons refresh; completed siblings
of the same request resume without repeating work. To change governing shared
assessment inputs, bind an explicit original request in
`definition.inputs.alternatives_reassessment`. Original results remain retained
in task attempts. A new request never renews retained budgets.

Explicit manual completion validates current head and body against alignment's
consumed PR evidence. Automatic entry can reuse that completed analysis as design
context across later author commits. Stop after requested alternatives, without
ordinary tasks, finalizers, code edits, PR approval or merging. An analysis-only
request suppresses publication and cannot claim a completed rejection.

## Historical fix reassessment

For retained `kind: alternatives`, `skill: dk-workflows:dk-pr-fix` stage records,
use `alternatives.py fix-gate` to discover the first unfinished stage. Missing old
installation paths return `reassessment_required` with its stage, dependency keys
and recovery guidance. Restore the original sources for unchanged reuse, or use
the user's explicit reassessment instruction. Keep the original results and
source artifacts; do not invalidate the cache or renew the run's allowances.

Use the original user-request JSON artifact with nonempty `id`, `source` and exact
`instruction`. Verify that instruction authorizes the affected stage and the
consumed current guidance. Rebuild a normal [prepare request](task-state.md#prepare-invoke-and-finish)
with exactly one fix alternatives descriptor, its current installed resources,
complete governing inputs and accepted dependency keys. Retain its original run
and an explicit budgeted review cycle when a run exists. Reassessment cannot use
CI repair/rerun reservations instead of review allowance. Run:

```sh
python3 PLUGIN_ROOT/scripts/dk.py --repository HOST/OWNER/REPO --pr PR_URL --checkout REPO reassess --file REQUEST.json --request-id ORIGINAL_MESSAGE_ID --authorization 'EXACT_USER_INSTRUCTION' --source ORIGINAL_REQUEST.json
```

`--request-id` and `--authorization` must exactly match the original artifact.
The helper binds its bytes, the existing task identity, original immutable result
and current replacement inputs, then returns the usual managed action. It never
launches a skill. Execute native work and finish its checklist through the normal
lifecycle. Identical replay reuses completed work; reconcile an unfinished claim
before explicit recovery with its original run/cycle. Do not manufacture another
request identity to retry an unchanged instruction.

An incomplete replacement can resume with the same binding while its inputs
remain unchanged. If those inputs change, use a new sourced request; the helper
validates the retained complete original through immutable result references in
existing attempts. Previously consumed request identities remain superseded after
later requests. Ordinary saved-key and low-level retries retain the original run
and review-cycle budget; unchanged accepted reuse reserves no additional allowance.
The locked start boundary also rejects changed authorized inputs and superseded
requests on low-level retries. Starting a newer request supersedes the previous
one even if the newer claim has not recorded a result.

Complete affected stages in dependency order. A changed comparison requires new
selection/alignment coverage. Explicitly bind each affected downstream stage
after its prerequisites finish, using the same sourced instruction only if that
instruction covers it. Unaffected upstream analysis and siblings remain reusable;
original sourced choices remain historical evidence. Missing original results
require restoration. Changed request bytes, source, governing inputs or a
superseded binding block reuse; obtain a new explicit request for changed work.
This operation cannot grant publication or merge permission, renew review/CI
allowances, close claims silently or release dependent work before fresh native
execution and checklist acceptance.

## Issue-only evaluation

Before a PR exists, use `dk-issue-alternatives` with the same native
assessment procedure against issue requirements and a selected target branch.
Keep this analysis in memory or existing issue evidence; never fabricate a PR URL
or PR-scoped task record. Native output can be checked with `alternatives.py
--repository HOST/OWNER/REPO --checkout PATH issue-evaluate --assessment FILE
--comparison FILE --selection FILE --target BRANCH`. Assessment uses the same
rubric structure; comparisons include `smallest` and a materially distinct
`alternative` without requiring a PR candidate. The helper validates the pinned
target and calculates totals and choice without creating durable state.

## Compatibility and cost

The historical `dk-alternatives-review` whole-problem capability redirects to
`dk-issue-alternatives`. The former name `dk-issue-implementation-alternatives`
is also a read alias, preserving its saved settings and original task keys. Historical skill names, contract-v2 task
keys, claims, original evidence, siblings, budgets and publication receipts stay
recoverable through the legacy managed protocol below. Old whole-problem results
never count as the new architectural inventory. New managed definitions use
distinct responsibilities, retaining the stable shared assessment key.

Expected savings come from avoiding repeated purpose assessment, rubric selection
and completed decision comparisons in the current and next session. Small task
selectors and references cost less to validate and consume than repeating native
analysis; compact status and targeted original-result reads serve fresh sessions.
Freshness ends at changed governing sources/guidance, relevant dependency changes
or missing evidence. Mode-only changes affect selection; changed choices require
interaction/alignment checks. No new database, settings snapshot, copied report,
completion flag or telemetry store is admitted. Native API dollar savings remain
unmeasured; offline call/process counts establish behavior rather than savings.

## Legacy alternatives recovery

Historical stage keys, claims, results and receipts remain addressable through
`kind:alternatives`, `fix-gate` and `gate`. See the [retained managed protocol](legacy-alternatives.md)
for recovery only. These results do not establish new architectural inventory
coverage. Use `plan` for current workflows.
