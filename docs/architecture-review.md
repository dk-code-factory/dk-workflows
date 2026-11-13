# Incoming architecture review

`dk-pr-review` owns this default-enabled gate, before all configured tasks and
parallel groups. `dk-pr-reviewers-run` used independently is unchanged. The
independent dk-architecture catalog is separate. Resolve the owner's
`architecture` and `permissions.edit_architecture_decisions` options through
skill-owned configuration. Disabling architecture bypasses assessment, execution,
selection and new architecture publications; keep existing evidence.

After inspection, preflight only `--stage architecture` with `config.py
skill-preflight`. Resolve the installed review prompt's native contract and host
capabilities before claims. Do not preflight normal reviewers until alignment
releases them; use `--stage review` and later `--stage finalizers`. Unavailable
native execution/evidence blocks the affected work privately. Helpers never start
Codex or Claude Code, including for recovery.

## Managed assignments

Use `dk.py prepare` and `finish`, with descriptor `kind:"architecture"`,
`stage`, `dependencies` (stage name to accepted task key), optional owner
invocation `options` and `upstream`, and the ordinary explicit `definition`.
The helper constructs assignments, input references and mandatory checklists,
including the owning workflow's retained limits. Architecture analysis is read-only;
publication permissions are checked live rather than copied into each stage.
Selection invocations also receive transient `architecture_scores` calculated by
the helper; pass these with the assignment for confidence assessment. These
derived totals are never saved as a second report.
Supply complete governing original sources, discussion, requirements, Decisions,
preferences and pinned `git` inputs, plus the installed skill resources and
`native_review` contract. Use the same retained run/retry budgets as other managed
analysis. Required native observations, original output and checklist acceptance
remain mandatory. Read compact `resume` and targeted `result` sections in a new
session; reconcile unfinished claims before retrying.

Stages run in this order:

1. `assessment`: return `required:bool`, `reason` and `evidence:[source refs]`.
   Material boundaries, interfaces, dependencies, state, concurrency, recovery or
   operational choices require comparison. Simple changes may skip with evidence.
2. `comparison`: depends on assessment. Execute `architecture.review_skill`
   (default `dk-workflows:dk-architecture-review`). Return the comparison below.
3. `selection`: depends on comparison. Return selection below. A pending choice
   blocks every normal reviewer. Ask the user for the actual choice and retain
   its source; elapsed time supplies no authorization. Bind the new choice source
   as an additional selection input to refresh selection without repeating
   unchanged alternatives analysis.
4. Publish the settled typed `architecture` decision using the existing Decisions
   publisher and journal. This permission covers only Architecture decisions
   under the configured Decisions heading. Preserve other decisions and PR text. Bounded edits exclude fenced/HTML
   examples and recognize indented ATX sibling boundaries; reject an unfinished example; unchanged insertion is idempotent.
   The restricted journal may contain only a body edit, never a title edit.
5. `alignment`: depends on comparison and selection. Compare the current pinned
   implementation; return `alignment:aligned|mismatch|uncertain`, `reason`,
   `evidence` and substantive `required_changes` for mismatch.
6. For mismatch, `verification` depends on alignment and runs in a fresh independent
   native agent. Return `disposition:confirmed|rejected|uncertain`, `reason`,
   `evidence` and `required_changes` when confirmed. Publish exactly those design
   changes as `REQUEST_CHANGES`, with no ordinary findings, through `publish.py`.
   Rejected or uncertain mismatch verification requires resolution privately.

Every report includes `complete:bool`, `findings:[]` and the prepared checklist.
Supply evidence explaining irrelevant checklist items; omissions do not disappear.
Primary reports may satisfy the entire checklist; bounded completion checks reuse
the original analysis. Never use a bare flag or a PR text marker as coverage.

Call `scripts/architecture.py --repository HOST/OWNER/REPO --pr URL gate --file
REFS.json`. Refs contains `assessment`, and when required `comparison`,
`selection`, `alignment`, `decision_operation`, plus `verification` and
`review_operation` for rejection. Entries are managed task keys or existing
publication operation paths. Missing/stale/corrupt evidence blocks the gate.
For ordinary `dk-pr-review` task/finalizer definitions, pass these refs in
`definition.options.architecture_evidence`; managed skill occurrences enforce the
gate before claims and recapture it at completion. Include `analysis_only:true`
under execution options when applicable. A skipped gate can release analysis-only
review tasks; an applicable gate cannot publish its required decision and stays
privately incomplete. Ordinary publications remain disabled by the caller.

`aligned` releases normal tasks with the decision's immutable source results as
governing inputs. `architecture_rejected` is a completed architecture-only review,
with `ordinary_review_complete:false`: stop without running ordinary reviewers,
fork CI approval, PR approval or merge finalizers. Never imply code review ran.
Publication permission failures and analysis-only instructions retain private
analysis but cannot claim reconciled architecture publication or completion.

## Comparison and scoring

Return `profile`, `profile_rationale`, `anchors` (criterion to shared rating
anchors explaining 0, 50 and 100), `alternative_coverage`, `evidence` and at least
two `solutions`. Cover every materially distinct reasonable repository-supported
approach, including `kind:pr` and `kind:smallest`; explain rejected/infeasible
approaches without claiming every theoretical solution was considered. If those
approaches coincide, explain that and compare a materially distinct alternative.

Each solution has a unique `id`, `kind:pr|smallest|alternative`, and:

- `approach`, `components`, `pros`, `cons`, `handled_edges`, `unhandled_edges`
  (including consequences), and source `evidence`.
- `production_loc:{min,expected,max,reason}` as nonnegative integer added plus
  deleted production lines relative to the pinned diff base, including executable
  infrastructure configuration and excluding tests, docs and generated output.
- `complexity` and `intrusiveness` (0–100; higher means more), with
  `complexity_reason` and `intrusiveness_reason`.
- `ratings` (criterion to integer 0–100, higher is better), `rating_evidence`
  for each criterion, `feasible:bool`, `feasibility_reason`,
  `material_doubts:[strings]`, `recommended:bool`, `recommendation_reason`.
  An optional `total` must match helper arithmetic exactly.

The framework checklist covers requirements, alternatives, compatibility,
security, concurrency, partial failure, retries, migration, rollback, operational
cost, estimates, scoring, uncertainties and recommendation evidence.

| Profile | edge_coverage | quality | simplicity | low_intrusiveness | low_loc | low_operational_cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| minimal_change_edge_coverage | 40 | 15 | 15 | 15 | 10 | 5 |
| quality | 25 | 30 | 20 | 10 | 5 | 10 |
| minimal_change | 15 | 15 | 20 | 25 | 20 | 5 |

`scoring.profile:auto` asks the architecture agent to assess new features,
existing behavior, tests, CI and maintenance with `profile_selection_prompt` and
choose a built-in profile with rationale. A fixed profile bypasses that choice.
`custom` requires `custom_prompt`, a shared `weights` map of positive integer
percentages summing to 100, shared anchors and normalized evidence-backed ratings
for every option. Helpers calculate sum(weight × rating)/100. Mandatory
requirements are feasibility gates for every profile.

Selection returns `mode:auto`, `confident:bool`, `sensitivity` (confidence under
estimate changes), `evidence`, and `choice` (ID or null). A sole feasible option
also requires supported `rejected_alternatives`. The default auto-pick prompt
cannot override a unique feasible winner, the default 10-point `min_score_gap`,
or unresolved material doubts. Ties, insufficient lead, no feasible option,
uncertainty or disabled auto-pick leave choice pending. Manual selection returns
`mode:manual`, feasible `choice`, exact `user_source`, `reason` and `evidence`.
Every settled selection supplies its final `reason` and `rejection_reasons` map
for every unchosen option, including after a user chooses a different approach.

## Publication and freshness

Use `architecture.py decision --comparison KEY --selection KEY --body CURRENT_BODY`
to construct a bounded body edit, then review its exact public text. After an
author update, add `--impact KEY` to reuse the unchanged accepted selection
under current nonmaterial impact. Use the
existing `publish.py` edit-pr journal, never a second publisher or blind retry.
The helper returns `decision_type:architecture`; decision entries distinguish
these from scope/behavior decisions. Publish chosen approach, rationale,
considered alternatives, scores and rejection reasons. Keep full comparison
reports, workflow handles, receipts and blockers private. Gate verification checks
the exact bounded transformation and the reconciled receipt, then reads the
live PR head and current selected subsection. Removal or changes block reviewer
release pending publication. An unrelated body edit preserves an intact decision
only after current nonmaterial impact bound to that metadata evidence; later
external edits require assessment again. Reuse journal paths
for read-only recovery and duplicate prevention.

After a change, prepare `impact` against the retained comparison and selection
(or the retained assessment for an architecture skip) and current sources. Assess
applicability, feasible choices, confidence, estimate sensitivity and any sourced
user choice; nonmaterial means these remain valid. Supply `definition.inputs.architecture_current_pr` as a reference to the existing
current PR metadata evidence (including `body` and `headRefOid`); no second body
snapshot is needed. Only evidence-backed `impact:nonmaterial` permits historical alternatives
reuse; material or uncertain impact requires refreshed affected analysis. Keep
accepted assessment and selection results when impact is nonmaterial; run only
impact and current alignment. Include `impact` in the new alignment dependencies
and gate refs. The impact task must reference the retained selection when reusing
it, or assessment when reusing a skip. New selections after policy or user choice
changes depend on comparison and impact. All stages use original immutable result
references, without rebinding unchanged analysis through another invocation.
The helper keeps impact bound to the exact selection it originally assessed when
that selection is later refreshed; it does not treat historical context as current
selection coverage. Missing original sources block recovery. Exact result
bindings distinguish a new impact attempt from an older result under the same
task key; alignment cannot start with an unassessed retained selection.
Always run current implementation alignment after author changes. Changed scoring
or review guidance refreshes comparison, including changed prompt contents under
the same skill identity; historical reuse recaptures that guidance. Changed auto-pick policy refreshes only
selection. Keep originals, completed siblings, claims, budgets and publication
recovery references. Treat the workflow's Decisions edit as self-produced only
when reconciled journals match the exact before/after body lineage. Trace successive
own edits back to the original governing body; stop at an external edit. Ambiguous
or circular lineage blocks normalization privately. External edits still require
current impact assessment. Use `architecture.py governing-body --body FILE --operation FILE` to recognize
that exact self-produced body deterministically. Capture normalized governing context
without the recognized self-produced subsection; retain the live body as a
publication input, not a copied analysis snapshot.

The bounded editor recognizes [CommonMark heading and HTML block boundaries](https://spec.commonmark.org/0.31.2/#html-blocks), treating block HTML and list containers as opaque. Complete unknown HTML tags
start blocks only outside an open paragraph; code-starting list items do not
create lazy paragraph continuations that hide a root heading. Quoted HTML
attributes retain their complete-tag grammar, and list-contained HTML/fences
retain block state so closing them cannot create a false paragraph. Only eligible
markers interrupt paragraphs: ordered lists start at 1 and empty items cannot
interrupt. Boundary scanning stops at the nearest heading and avoids rescanning
previous Setext headings.

Sibling task preparation and resume share prerequisite validation and one live PR
read within each construction/capture boundary. A separate boundary and every
subsequent command recapture live evidence; no readiness result is saved.
Read body files without newline translation. Public option descriptions are inline
prose paragraphs so inserting them cannot absorb a following indented sibling
into a generated alternatives list; full private comparisons retain formatting.

Offline acceptance establishes reuse, invalidation and recovery behavior. Native
API dollar savings remain unmeasured; see the [design](../DESIGN.md#incoming-architecture-gate).
