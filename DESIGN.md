# Workflow design

## Objective and state admission

The agent decides and invokes native skills. Python handles deterministic reads,
input capture and recovery. Optimize total API dollar cost across the current task
and subsequent runs, including a new session with no inherited conversation.
Count all attributable coordinator and descendant responses, preparation, waiting,
finalization and result consumption. Price each response's fresh input, cache reads
and writes, and output including reasoning using its observed model, service tier
and context size. Token reductions alone do not establish savings. Unknown prices
or incomplete telemetry make a cost comparison inconclusive.

Durable state is justified only when its expected avoided API cost exceeds
the cost to prepare, maintain, validate and consume it. A proposed state
field must identify the repeated work it avoids, its consumer on a fresh-session
resume, its freshness boundary and its failure/recovery behavior. Convenience,
audit completeness and speculative future use alone are insufficient reasons.
This admission rule applies to new fields and changes to existing state.
Reject designs that save preparation in the current session by increasing
reconstruction or context-reading work in the next one. Evaluate both sessions.

Keep original completed analysis, findings, dispositions, explicit acceptances,
input fingerprints, active claims, bounded budgets and publication intent and
receipts. These avoid repeated review, lost decisions, duplicate launches and
costly recovery after uncertain writes. Do not persist derived status, rendered
summaries, duplicate transcripts, default telemetry or whole settings snapshots.
Read current CI and publication readiness at their existing boundaries.

Complete-workflow cost exercises use test-only frozen scenarios and readers. The
already-running native session executes the actual selected skills and their
supported descendants; local Git/GitHub transports control external responses.
The scenario retains both complete default plans, applicable alternatives,
simplification, verification, fix cycles, finalizers and recovery obligations.
Instruction-only duties cannot substitute for these skills. Freeze a historical
pre-change baseline and candidate before trials, including resource closure,
assignments, starting host instructions/tool schemas, controls and quality
expectations. Keep every completed arm, even when it regresses or is inconclusive.

Read the native host's parent-child graph independently of usage records. Every
attributable turn needs matching start/terminal observations, actual controls,
advancing cumulative counters and per-response usage. Price each unique response
once, including reasoning within output, cache durations and context charges.
Missing descendants, terminal evidence, controls, prices or matching frozen
conditions make comparison inconclusive. Report recurring initial and fresh-session
work together; keep implementation, PR review and experiment orchestration spending
separate. Preserve the established correctness and dollar gates.

Measurement artifacts are explicit opt-in test evidence, not production telemetry
or another workflow state store. Private artifacts reference original host traces,
Git pins, result artifacts and journals rather than copying conversations or
settings into workflow state. Their consumer is the independent experiment verifier
and a later session resuming an unfinished arm. Retaining these references avoids
repeating paid completed native trials and reconstructing uncertain external
actions; expected avoided cost exceeds hashing, validating and targeted loading.
Freshness ends at changed frozen inputs, incomplete host inventories or damaged
original evidence. Such changes invalidate the comparison, never erase a completed
arm or authorize rerunning it. Ordinary tests use synthetic host records and local
transports only. Helpers neither launch model sessions nor manufacture native
completion. No measured savings follow from the fixture implementation alone.

The test reader may exclude a copied ancestor event prefix only when the host
graph, original parent identity and already validated ancestor turn inventories
corroborate it, with no own usage or counter evidence before the child's settings
boundary. Replayed timestamps do not prove ownership. Filtering is transient;
original hashes, all own turns and independent response/terminal checks remain
required. Re-account retained raw observations after a reader correction instead
of repeating paid native work, preserving the original failure and identifying
the corrected reader source. No normalized transcript or production state is
added. Existing original-evidence references provide the avoided reconstruction
and trial cost benefit within their unchanged freshness boundary.

Public measurement artifacts may retain pseudonymous graph identities, original
trace hashes, actual per-turn controls and per-response token vectors. These are
opt-in report data, never default workflow state or copied transcripts. An offline
verifier checks every price, unique response and closed graph without needing
private conversations, account identities or publication journals. Keeping these
small pricing inputs lets later sessions audit prices with one offline command,
avoiding repeated model consumption of private host histories and reconstruction
of response ownership and pricing controls. Original trials are never repeated
for repricing. Original host evidence, frozen conditions and independent
quality judgments remain separate requirements; arithmetic verification cannot
establish them. Changed raw evidence or pricing invalidates the affected report,
while preserving its original result and requiring no repeated native analysis.

Fork-CI approval binds reviewed source coverage to the current PR head and live
target branch tip. The PR REST base SHA may be historical; retain it in the
existing context and match workflow-run base metadata to that SHA, alongside
the existing PR, ref and repository checks. Context changes still invalidate
readiness, and every dispatch rechecks the live reviewed pins. This adds no
durable state, resets no analysis and preserves publication intent and receipts.

Direct merge requests after standalone review enter a read-only fork-CI bridge
before waiting for CI or preparing the merge. The helper discovers current gates,
checks policy/source pins and constructs the native incoming-review or approval
handoff. The already-running session resumes supported managed work, completes
current evidence, approves eligible attempts, observes CI and returns to the
original authorized merge. Unbound standalone outputs never become fabricated
managed coverage. Pending journal operations are returned for read-only
reconciliation before discovery or new writes, except an uncertain review
request with a current explicit `no_resend` disposition. The bridge, approval
and publication boundaries share that existing live disposition check; changed
review-request evidence blocks continuation without rewriting the pending intent.
Malformed retained review evidence, including nested legacy specs and reports,
returns the incoming-review recovery handoff with a diagnostic under current policy
rather than supplying approval coverage or usable overrides. Explicit invocation
options and current defaults require complete effective-option validation before
every handoff, including reconciliation and CI observation without remaining
gates. Unavailable retained checkouts, pinned Git objects and local capture sources
also return that handoff with the underlying failure and prerequisite restoration
step. Normalize failures only while reading/reconstructing local retained evidence;
non-Git process failures and typed publication diagnostics retain their own blocked
or reconciliation paths. Restoring the original sources permits unchanged analysis
reuse without refreshing snapshots, rewriting results or retrying publication.
Retained overrides govern only fully accepted evidence; rejected evidence
recovers under the current explicit invocation or defaults. Listed and unlisted
pending review requests share the live no-resend check, preserving their original
head binding and intent rather than requiring a fabricated verified receipt.
Existing review definitions, results,
fingerprints, publication intents and receipts retain their original
freshness boundaries; changed governing inputs refresh only affected analysis.
No durable field, summary, snapshot or continuation store is added: the existing
private merge request and original instruction sources supply the continuation.
This avoids reconstructing an alternate approval protocol and repeating valid
managed analysis across sessions. Native API dollar savings are unmeasured;
offline handoff/recovery tests establish behavior, not measured cost savings.

Fork-CI discovery first enumerates the reviewed head's pull-request runs rather
than repository-wide history. Verify each listing's stable total count and
complete pagination before dispatch or a no-op. At GitHub's 1,000-result search
boundary, use the unfiltered listing with the same count and completeness checks
rather than accepting possibly truncated coverage. Existing per-run association,
readiness rechecks and publication recovery remain unchanged. This adds no state
and avoids repeated unrelated-history reads on ordinary finalization and
fresh-session resume; no native API dollar savings are claimed.

PR merging likewise binds the reviewed target to the live branch tip, rather
than historical REST or GraphQL base commit metadata. Match the target branch
name across REST and GraphQL and recheck its live tip after observing CI as well
as before. Keep head binding, repository readiness, review and CI requirements,
pre-dispatch checks and exact merge-commit parent verification unchanged. No
state or analysis reset is needed; existing approval and publication receipts
remain reusable within their original freshness boundaries.

Successful merge CI may include GitHub's terminal skipped or neutral checks,
which gh groups into its skipping bucket. Require at least one passing check,
accept only SKIPPED/NEUTRAL in that bucket, and reject other nonpassing buckets
or unknown states. REST and GraphQL repository readiness must still be clean
and review requirements satisfied; skipped checks cannot override those gates.

## Task module boundaries

Task coordination uses these dependency directions:

- `task_primitives` owns canonical JSON hashing, identity compatibility, file
  reads and the existing private atomic-write helper. It does not load workflow
  configuration or task state.
- `task_capture` owns exact resource and Git input capture. It accepts a built
  spec and returns the existing components, fingerprint and provenance. Its Git
  context is transient and shared only within the existing validation boundary.
  It does not select workflows or interpret live configuration.
- `task_policy` builds specs, validates current instruction/configuration and
  invocation selections, then calls raw capture. This validated capture is used
  independently at prepare, status and finish; moving code never removes a live
  check. Configuration may use its spec builder without importing storage.
- `task_storage` owns the existing private directory, manifest validation,
  locking and immutable result reads. It depends on primitives and filesystem
  helpers, not capture, configuration, native evidence or workflow selection.
- `task_runtime.Store` retains the existing task admission, reuse, checklist,
  native-evidence and allowance rules above storage. `Tasks` and workflow owners
  continue to select assignments and native work. Storage never makes those
  decisions.

`state` remains a compatibility import facade over these implementations. New
internal consumers import their owning layer directly. Existing public functions
and `Store` methods retain their arguments and responses. Private instrumentation
hooks belong to their implementation modules; they are not a second protocol.

The extraction preserves contract-v2 identity mappings, fingerprint components
and bytes, provenance, storage locations, private permissions, lock scope, atomic
writes, result envelopes, original attempts, claims and bounded allowances. No
migration, task redefinition, cache clearing or new durable record is needed.
Frozen pre-extraction captures and retained state test exact compatibility;
fresh processes reopen original results and incomplete siblings without repeating
completed analysis. Existing admission and drift checks still protect claims and
completion, while publication retains its independent live boundary.

The expected cost benefit is reducing repeated source reconstruction when a native
session must change or diagnose one responsibility. Named interfaces let it read
the relevant layer while retaining existing compact recovery and command-local
capture sharing. There is no extra state to create, validate or reload in this or
a fresh session. This benefit ends when the task needs evidence from other layers;
those reads still count. Native API dollar savings are unmeasured. Offline
compatibility and recovery tests establish behavior, not a dollar comparison.

## Managed task interface

### Structured recovery diagnostics

Task and configuration boundaries add the shared `diagnostic` envelope while
retaining existing status, action, reason and exit behavior. Authoritative
conditions use a `ValueError`-compatible typed diagnostic; projections use
existing structured capture, claim, result, execution and publication facts.
Classification never matches explanation text. Untyped legacy failures retain
unknown provenance and dispatch certainty rather than guessing a recovery route.

Supported actions distinguish commands, native tasks and human input. References
identify original task/claim/result or configuration/publication evidence.
Uncertain external writes direct read-only reconciliation of their original
operation; a local input failure establishes nothing about an earlier write.
Configuration recovery retains its original request, intents and receipts.
Diagnostics cannot authorize recovery, replenish allowances, bypass native
evidence or replace current CI/publication checks. Native tasks execute in the
already-running session.

Diagnostics are transient projections, never saved summaries or a second recovery
registry. Existing task identities, capture bytes, original results, completed
siblings, claims, counters and journals keep their formats and validity rules.
Targeted actions avoid repeated interpretation of helper-specific prose during
this run and fresh-session recovery, without additional durable-state creation,
validation or loading cost. Freshness ends when governing inputs or referenced
state change; callers inspect again before acting. Offline preservation and
recovery tests establish behavior; native API dollar savings are unmeasured.

`scripts/dk.py prepare --file REQUEST.json` accepts explicit task definitions.
One helper call builds specs, captures current inputs, claims eligible work and
returns `invoke`, `reuse`, `recheck`, `failed` or `recover_required`. It includes expected native
observation bindings for invocations that require them; it never invents host
evidence or invokes a model. `finish` uses the retained task key and claim to
validate current inputs and record original output and explicit completion.

Store the definition and occurrence selector with the existing task entry,
atomically with its claim. That small record avoids reconstructing assignments,
resource maps and input paths in a new session. It contains references to the
existing governing sources, not copies of those sources. Keep referenced inputs
in stable private locations while their results remain useful. Missing sources
block reuse; retained definitions do not replace current instructions or scope.

`resume` regenerates specs from these records and recaptures live sources. It
returns compact status with finding counts and result references, preserves
PR-wide unfinished claims and marks legacy entries needing a supplied definition.
`result` reads only the requested validated result section. Full evidence remains
available when assessing findings, dispositions or recovery. Resume is discovery
of saved work; the coordinator must still select the current required task set.

Run-scoped resume discovers ordinary skill-list tasks and reviewers as shared PR
analysis, just like ordinary task definitions. Only hooks, finalizers and workflow
completion are filtered by their retained run occurrence. This avoids rebuilding
hidden task selectors or repeating valid analysis after a new-session resume.
Existing definitions, results, fingerprints, claims and counters are unchanged;
live recapture still governs freshness. No new state or measured cost claim is
introduced.

`prepare --compact` omits an already retained assignment for reuse, recheck,
failure and unresolved-claim responses; new invocations still receive their full
assignment. `finish --compact` omits the unchanged assignment on rechecks. These
are presentation choices only: all live capture, claim, checklist and budget
checks still run. The unabridged commands recover the full assignment in a fresh
session. No response cache, new command registry or durable field is introduced.
The avoided work is repeated model consumption of known assignments. Savings end
when the caller needs the assignment and must retrieve it; both reads count in
native measurements.

The low-level spec/start/record API remains compatible. Preparing a task through
the managed interface can attach a definition to an existing reusable entry
without invoking its reviewer, resetting budgets or changing its fingerprint.

## Read-only next actions

`dk.py next --file REQUEST.json` projects the current skill-owned task and
finalizer groups (or a review runner's reviewer groups) into a bounded page.
The request names the owning skill, invocation-local options, retained run and
optional cycle, plus definitions for not-yet-retained work. Current configuration,
not the saved entry list, selects required coverage. Retained definitions supply
assignment sources only; selection and options are rebuilt live. Removed tasks
remain historical evidence; empty/custom finalizers stay authoritative.

Locations such as `tasks/0` identify current configured occurrences, not new
durable task identities. Explicit prerequisite locations or original task keys
are supplied as inspection inputs. Earlier ordered groups are implicit barriers;
parallel siblings remain independent. Cyclic or unknown prerequisites are rejected.
Native applicability decisions can exclude work only through an intact, currently
accepted original result whose applicability record binds the exact selected
invocation and supplies a reason and evidence. The helper makes no semantic
applicability decision. Missing or stale evidence leaves work blocked. Optional
terminal failure is reported without satisfying an explicit dependency on its
result. Every selected unresolved occurrence remains visible.

Actions distinguish `invoke_native`, `consume_result`, `check_completion`,
`definition_required`, `blocked_prerequisite`, `exhausted_allowance`,
`reconcile_claim`, `reconcile_publication`, `inspect_publication`,
`restore_evidence`, `optional_incomplete` and `not_applicable`. Assignment, result,
checklist, claim and journal references point to original evidence. The existing
alternatives gate is consulted without launching or requesting a comparison;
historical design context never supplies current code coverage. Pagination bounds
both recovery and task output. No original finding bodies, assignments, progress
logs or full manifests appear in routine output.

Inspection reads one in-memory manifest snapshot without creating state files,
reserving claims, check rounds or allowances. Budget validation runs on an
in-memory copy through the existing validator. PR-wide claims and publication
operations are inspected across runs: uncertain writes direct read-only recovery
to their original journal, never another dispatch. Prepared intentions also point
to their original operation for inspection. No live GitHub requests are made.
The current native session decides whether and how to act. Inspection cannot
authorize a write, allocate a review cycle, launch a model, delegate, or establish
CI/publication readiness. Prepare/finish and publishers revalidate inputs and
readiness at their existing boundaries; an observation is never a lease.

The expected saving is avoiding repeated native reconstruction of current ordered
obligations and recovery on this run and a fresh session. Original task definitions,
results, claims, budgets and journals remain the only durable coordination state;
no rendered plan, readiness flag, settings snapshot, second index or scheduler is
added. Freshness ends when configuration, governing inputs or evidence changes,
or when a concurrent claim/publication changes the observed state. Reinspect as
needed and use targeted original-result reads. Offline reuse/recovery tests prove
behavior, not measured API dollar savings; native dollar savings are unmeasured.

## Task completion checklists

Every managed assignment includes a deterministically generated checklist, plus
custom stable-ID items. The definition's `checklist` sets `items`, `max_checks`
(default three additional checks) and `required`. Selected reviewers pass their
required/optional status as definition `required`; other tasks default mandatory.
Explicit checklist overrides affect checklist acceptance only. Low-level tasks
without managed definitions retain their existing behavior and fingerprints.

A complete primary report may include every checklist item with evidence. Validate
that report against the entire prepared contract and accept it in the same
transaction as its unchanged original output and valid native observations.
Acceptance uses the existing primary-result reference and complete outcome with
zero rounds; no duplicate checklist artifact or new state field is needed. Resume
and replay validate that immutable primary report against the current contract.
Missing checklist evidence reserves the first follow-up. A malformed supplied
checklist rejects the submission without changing the claim or consuming checks.
The initial report never consumes a check. Each follow-up verifies every item against
the original assignment, finishes omissions and supplies evidence references or
an unfinished reason. Success stops early; exactly N unfinished follow-ups fail
mandatory work or accept best-effort work with every omission reported. This
never accepts failed execution, an incomplete primary report, missing native
evidence, stale CI or missing publication checks.

The existing task entry holds only the checklist contract, governing fingerprint,
original-result reference, bounded reserved rounds, response references and final
outcome. A digest recognizes replayed initial submissions; per-round response
hashes reject conflicting replays. Immutable responses share the existing result
artifact directory. This state avoids repeating completed analysis, rechecking
completed rounds, reconstructing pending checks or accidentally extending their
allowance in a fresh session. No conversation, settings snapshot, secondary
manifest or rendered status is saved. Compact resume supplies the pending round,
consumed count and targeted response references; full reports are explicit reads.

Acceptance is fresh only for the same governing fingerprint, checklist contents
and intact original result and final checklist evidence. Checklist changes reset
acceptance alone, preserving native output, attempts, review budgets and unchanged
siblings. Relevant task-input changes still require refreshed primary work.
Findings from follow-ups are derived from immutable response artifacts and
appended to primary findings for retrieval, status and CI approval. Verification
captures every contributing artifact. On checklist-only refresh, retain references
only to earlier responses containing findings; this avoids losing completed
analysis or repeating its discovery. These references remain fresh only for the
same primary result and governing fingerprint. Missing response evidence blocks
acceptance. No additional findings copy or merged report is persisted.
Preparing a retained result lacking a checklist reserves completion-only checking
instead of launching its reviewer. The same agent receives follow-ups; after an
interruption the coordinator reconciles retained evidence without repeating the
original analysis. Pending rounds remain reserved until their response is saved;
claims and uncertain external writes still require their existing reconciliation.

The fix coordinator uses a managed `completion` occurrence under its retained run.
Its helper captures the live selected finalizer plan and configuration sources,
building only that plan's items, including an empty plan. This avoids reconstructing
final duties after interruption and prevents checklist instructions from restoring
disabled finalizers. Plan/source changes refresh affected completion evidence;
completion tasks are noncacheable, and current PR, CI and publication checks
remain independent live boundaries.

## Reuse and extension points

Preserve contract v2 task identities and content fingerprints. Governing inputs
include exact instructions, installed resources, relevant preferences, consumed
context and Git content. Content reviews survive tree-preserving history changes;
history-sensitive tasks, CI and publication retain their stronger boundaries.
Resource or semantic changes invalidate only affected work. No blanket reset or
schema bump accompanies this refactor.

Hooks and finalizers use the same managed lifecycle and shared occurrence-spec
builder. Keep their existing identities, ordered groups and per-task receipts.
A resumed group keeps completed siblings and reconciles unfinished claims.
Removing extension points or replacing them with one fixed plan would discard
useful configuration and could repeat completed work. The coordinator still
owns ordering, dependency checks and native execution.

Preference handoffs are generated deterministically. Reuse prior conditional
decisions only when their complete record, matched paths and consumed condition
context are unchanged. Ask the agent only for new or affected condition decisions.
Store only the resulting task-specific handoff already consumed by the task;
unrelated catalog changes must not invalidate it.

## Settings and storage

Repository review selections use the existing skill-owned scope records: native
skills remain with the reviewer runner, and GitHub recipient defaults belong to
the publisher. Resolve recipients only for a new authorized request at fix
finalization; explicit concrete recipients take precedence as a whole selection.
The read-only `dk-pr-approvers` skill returns overrides exactly, including empty
lists, or discovers appropriate recipients for null from current PR review and
repository ownership evidence. Its helper constructs only the override/discovery
handoff; the native session owns semantic selection. The selected publisher calls
it as supporting work, without restoring omitted finalizers. Keep its evidence
in the existing caller task result and bind consumed resources/inputs there;
unchanged selection can be reused only while override, PR/head and consumed
review/ownership sources remain current. No extra cache, settings snapshot or
execution-state field is introduced. The existing publication intent retains
the resolved concrete recipients, avoiding
repeated selection and duplicate dispatch during fresh-session recovery. Settings
changes affect new preparations and the publisher's managed option freshness;
they never redirect retained intents or clear original review results, siblings,
claims or receipts. Live head and publication checks remain mandatory. Native
API dollar savings have not been measured.

Ordinary CI classification in both PR fix and incoming review learns from the
current PR's failed checks. It records exact named outcomes and queries issues
for those tests, refreshing only their linked issue evidence. It never enumerates
repository Actions history automatically. Successful current-PR runs collect named
passes only after a failure has been observed at that head; aggregate green jobs
are not pass proof. Fresh heads without failures do no flake investigation.
Successful checks contribute even while an independent workflow remains failed.
Explicit `dk-ci-flakes` discovery retains bounded repository history/issue scans.

Reuse the existing repository registry, observations, issue snapshots, scan claims
and immutable attempt artifacts; add no durable store, schema or copied report.
The historical reader stays unchanged so its content fingerprint and original
cache keys remain intact. Original broad-scan claims remain discoverable and
reusable; new PR lookups depend on the failure fingerprint and issue limit, not
historical lookback/run limits. Interrupted lookups retain their original claim;
explicit discovery can gather missing evidence without clearing that claim.
Named pass collection reuses the same validated attempt cache, including across
fresh sessions. It also validates successful reports for previously failing steps;
an incomplete cached report refreshes only that attempt without changing the
historical reader or cache identities. Unrelated setup logs do not invalidate
named-test evidence. Its returned learning status is transient; only original outcomes
and artifacts remain in the existing stores. Missing/corrupt artifacts require affected recollection; changed
head/attempt/workflow/job identities select distinct evidence. Open-issue proof,
failure signature and live CI checks still govern acceptance independently.

Expected API dollar savings come from keeping unrelated history/logs and issue
records out of agent handoffs and avoiding repeated reconstruction on the next
session. Deterministic helpers provide bounded compact pages and targeted reads;
local status creates no verification credential. Cached original artifacts avoid
recollection, while selected issues and current attempts are checked live.
These savings end when governing inputs change or retained evidence is missing;
offline reuse/recovery tests do not establish measured native API dollar savings.

CI infrastructure recovery keeps native diagnosis in the existing failed-attempt
publication intent, with references to the already retained job/log evidence and
a fingerprint of the PR changes, checks and attempts. The deterministic helper
returns its discovery handoff transiently; it never saves a second failure index,
PR diff, rendered summary or effective settings. A fresh session can reuse the
original diagnosis when recaptured inputs are unchanged. Changed PR content,
base, checks, attempts or missing/corrupt evidence require affected diagnosis
again. Native agents assess semantic unrelatedness against the actual diff;
log strings alone do not establish that boundary.

Diagnosis fingerprints and their live start/end comparisons use head/base SHAs,
refs and repository identities, excluding repository activity counts, timestamps
and profile metadata. Unrelated repository activity therefore preserves valid
analysis across sessions; changed revision identities still refresh diagnosis.
Attempt-source fingerprints likewise retain repository/user identities without
their descriptions or profile fields. Recollected source and evidence JSON
paths remain evidence references, while semantic fingerprints bind normalized
source contents, jobs and log archives rather than those JSON paths. This avoids
repeating diagnosis after unrelated metadata changes on a fresh CI observation;
changed source identity, jobs, logs, PR revisions or checks still refresh it.
This adds no durable state and avoids repeated native diagnosis for unchanged
PR changes and failure evidence. Existing original captures and preparations
remain retained; changed governing helper inputs require affected diagnosis to
be refreshed in the same intent. Native savings remain unmeasured.

Normalize accepted PR URL casing and trailing slashes before capture, matching
publication identity. Canonical-input fingerprints stay unchanged; equivalent
spellings reuse diagnosis without new state or history resets. The existing
revision, checks and evidence boundaries still govern freshness.

Each retained diagnosis uses the existing capture field for its workflow's input
fingerprint. Dispatch recaptures the PR changes and that workflow's checks and
validates the original source/archive references before an unsent retry. A sibling
workflow's retry does not stale this diagnosis. No extra input copy or store is
needed; unchanged fresh-session reuse avoids repeating native diagnosis, while
changed relevant inputs block dispatch until diagnosis is refreshed in the same
prepared operation. Earlier preparations remain intact through existing history.

Existing ci-rerun intents reserve automatic slots per PR/head and preserve the
minimum retained allowance, including incomplete operations. No separate budget
store is introduced. A new explicit user retry retains only its request identity,
authorization source and exact run/attempt in that intent; it permits one retry
of each specified workflow without clearing automatic reservations. Same-attempt
preparation returns the original intent even when wording changes. Pending sends
remain reconciliation-only. These small records avoid repeated diagnosis and
duplicate external dispatch in this and fresh sessions; live head/attempt and CI
checks still govern publication and readiness. Offline regressions establish
reuse, partial recovery and invalidation, not measured native API cost savings.

Lower allowances tighten an existing prepared intent before reservation checks;
later settings cannot restore its broader limit. Explicit authorization can reuse
that unsent operation without erasing its automatic reservation, derived from the
already retained original preparations. Preparation and dispatch share the existing
dispatch lock. Pending operations keep their original intent and reconcile even
after limits tighten or diagnosis evidence disappears.
Persist a tighter allowance before refreshing GitHub metadata or attempt evidence,
so a failed read or interrupted refresh cannot restore the earlier limit on a
fresh-session resume. The existing intent and preparation history retain this
minimum without new fields or stores; unchanged limits add no preparation.

Validate an explicit retry identity before changing an unsent operation, so a
rejected authorization leaves its evidence and allowance intact. Refresh current
PR metadata and the failed-attempt snapshot when preparing that same unsent
operation again. Existing preparation history retains the original evidence;
unchanged preparation creates no new snapshot. This avoids a fresh session
reconstructing a replacement after a title or body edit, without another state
field or store. Head, attempt and diagnosis freshness still gate dispatch;
pending sends retain their original expectations for reconciliation.

Equivalent recollection can replace missing or corrupt evidence references on an
unsent retry without changing its semantic capture. Keep healthy original
references and diagnosis unchanged; retain replaced evidence in the existing
preparation history. This adds no state fields or stores and avoids reconstructing
a replacement operation after evidence loss in a fresh session. Current inputs
and replacement evidence still require dispatch validation, and pending operations
only reconcile.


Confirmed push rejection reuses the existing operation's `not_applied` state.
The push publisher validates only the original send failure: positive nonzero
exit status and one unambiguous exact-ref rejection in porcelain or conclusive
legacy Git output, with no conflicting status or successful response/receipt.
Initial failure handling and later reconciliation settle it under the existing
operation/dispatch locks. Retain the request, intent and original failure intact;
no new journal, cache, setting or copied evidence is needed. Terminal reconciliation
reports non-delivery without observing a later branch tip as this push's success.

The freshness boundary for that historical outcome is its immutable original
request and send evidence, independent of later remote changes or credentials.
Uncertain failures remain pending. Identical fresh preparation excludes terminal
operations and rechecks the remote and candidate tree; dispatch still checks the
captured exact lease without silently refreshing it. This avoids repeated model
reconstruction and futile reconciliation in this and fresh sessions while
preserving unrelated completed analysis and all original recovery evidence.
Offline fresh-process, partial recovery, concurrency and changed-input checks
establish behavior, not measured native token or API dollar savings.

Uncertain review requests retain their original pending intent and first failure.
Small creation/dispatch timestamps, available native run/session references and
safe transport diagnostics live in that existing operation. They avoid repeated
attempts to reconstruct which invocation failed and prevent later read failures
from replacing dispatch evidence. Legacy missing provenance remains unknown.
Inspection and a single current `no_resend` disposition retain only reviewer
sets, historical event/review references and a PR/head binding, avoiding repeated
agent reconstruction in a fresh session. Unchanged inspection/disposition is
idempotent. Freshness ends when this evidence changes; live reads revalidate it
before reuse. No receipt or proof of non-delivery is manufactured from absence.
The original pending request remains a duplicate barrier for every new review
request, including changed action IDs. Independent replies may proceed under
their own live checks; other actions require current disposition or reconciliation.
No new store, workflow fingerprint or analysis cache is introduced. These are
recovery savings by design, without measured native token evidence.

Use one read facade for workflow and selected PR-domain settings. Load only the
requested sections, preserve each domain's documented precedence and return
source references. No persisted effective-settings cache is necessary. A common
scope helper and private storage primitives replace repeated loader/write logic.

Centralize storage location resolution. Keep existing on-disk locations and locks
as compatibility addresses: relocating live publication journals or silently
preferring an empty new directory could duplicate writes, lose reuse or bypass
retained budgets. New bookkeeping belongs in the existing PR manifest; no
parallel session database or resume-summary files are introduced.

## Agent guidance

The managed lifecycle authority is `docs/task-state.md`. Entry skills route the
request and identify the selected workflow; recipes define stage decisions,
applicability, consumed evidence and acceptance. They link to the lifecycle for
capture, claims, native execution, recording, completion and recovery instead of
restating those procedures. Domain contracts still own their authorization,
publication, CI and native-evidence requirements.

Instruction reading is staged within the already-running native session. Load
necessary common and selected-stage guidance when entering that work, reuse
unchanged guidance already in context, and reload it after context loss. Reading
is separate from governing-source capture: the complete existing resource maps,
including indirect references, are supplied before prepare even when their prose
will be read later. Live capture at prepare, recording and final coverage keeps
its existing semantics. No reference parser, generated instruction bundle or
saved read-status index is introduced. Missing or uncertain dependencies block
cacheable claims; changed identities, selected sources, options or relevant
resources refresh affected work and preserve original results and siblings.

The expected API-cost saving is avoiding repeated lifecycle interpretation in
entry and recipe text during initial execution and fresh-session recovery.
Necessary context-restoration reads still count, and every source remains fully
captured. There is no durable state to create, validate or reload. The saving's
freshness boundary is unchanged governing guidance and task inputs; context loss
requires the relevant prose again. Offline behavior and instruction-byte
comparisons are diagnostic evidence, not measured API dollars. Complete native
workflow cost and finding-quality measurement remains separate.

New task definitions declare execution evidence in the existing `native_review`
field using schema 2 and a required boolean `required`. With `required:true`,
the resolved `skill_version` and complete applicable `passes` remain mandatory.
With `required:false`, only `schema` and `required` are accepted; contradictory
execution requirements are rejected. The declaration controls preflight,
assignments, checklists, completion and reuse regardless of task or skill names.
An incomplete schema-2 declaration fails validation before a claim. Native
bindings and independent host-observed/reported-driver validation stay unchanged.

Schema 1 and absent declarations are explicitly legacy syntax: preserve their
existing name-based inference, captured bytes, fingerprints and task keys.
Do not upgrade retained definitions or accepted results automatically. New
callers use schema 2, including ordinary tasks that require no evidence. Both
preflight and task capture validate the same declaration. Changing a declaration
changes only that task's existing governing fingerprint; original results,
completed siblings, claims, budgets and recovery records remain retained.

This extends an already captured contract rather than adding a state field,
store, requirement map or migration. Fresh-session consumers read the retained
declaration through existing assignments and capture, avoiding repeated native
interpretation of names or reconstruction of missing requirements. Expected
avoided API cost comes from refusing unsupported execution before substantive
work and reusing unchanged accepted evidence. Its freshness ends at changes to
the declaration, installed resources, governing inputs or claim pins. Live CI,
publication and authorization checks remain separate. Offline regressions can
establish compatibility and recovery; native API dollar savings are unmeasured.

The task-state contract owns the lifecycle protocol. Skill prompts and recipes
refer to it once, use the managed commands and describe only stage decisions.
Keep native execution validation, scoped permissions, original evidence,
publication reconciliation and final-head CI requirements. A compact result index
does not substitute for reading the evidence needed for a decision.

Feedback response assessments distinguish concrete author requests from optional
discussion and questions to other participants. Retain source references and the
brief no-reply reason in existing task results/dispositions so a later finalizer
or fresh session can reuse the assessment without reconstructing the discussion.
Reuse ends when governing instructions, discussion or relevant code/validation
change. This adds no registry, status cache or publication receipt; pending writes
still reconcile through the existing journal. Token savings are not measured.

Feedback reply writers bundle all needed answers to one original source before
publication, keyed transiently by canonical PR, source kind and database id.
Each new body links that source, names its author and pairs faithful feedback
excerpts with answers. Different sources and inline routing remain distinct.
Repository agent instructions are not adopted for reply tasks, even through
coordinator/publisher handoffs or copied earlier-stage guidance; code, validation
and discussion remain factual evidence. Direct user instructions, private reply
configuration, package contracts and host priority retain their authority.
This is a task-local guidance boundary, not a saved source-policy mutation.

No new state, cache key, fingerprint format or grouping registry is needed.
Existing task resource bindings refresh affected reply work when these contracts
change; original results, completed siblings and budgets remain intact. Existing
publication intents retain their exact body, source and receipts across resume,
including legacy attribution, rather than being rewritten for the new format.
Grouping remains fresh only for the current discussion, obligations and supported
answers; publication authorization and live checks remain independent. Bundling
avoids generating and recovering multiple replies for the same source in this
and fresh sessions without new durable loading/validation costs. Native API dollar
savings and complete native adherence are unmeasured; offline publisher scenarios
establish exact-body retention and recovery, not semantic grouping decisions.

Evidence preflight resolves each selected reviewer occurrence's installed contract
and checks current host observation capabilities before claims or cycle consumption.
The requirements map and capability document are transient inputs to a read-only
helper, not a readiness cache. Their freshness ends when the host, installed
resources, selected invocation or pinned applicability inputs change. Early refusal
avoids spending a model review on transport that cannot satisfy completion; no
measured token savings are claimed. Failed preflight preserves existing results,
claims, completed siblings and budgets. Completion still requires actual claim-bound
host observations; capability assertions never replace execution evidence.

Native result transport can establish independent review completion without child
command logs. Root and required child identities, parent relationships, successful
terminal outcomes and original outputs remain mandatory. When command transport
is available, retain complete-inventory and exact successful command-observation
validation. Otherwise the trusted coordinator explicitly records unavailable tool
observations in the existing evidence artifact and each required child's original
terminal output reports completion of its exact declared drivers. This is reported
driver evidence, never host-observed command execution; any supplied observations
still receive strict validation. Missing or failed native results cannot fall back.

No new cache, store or status field is introduced. The existing immutable native
evidence artifact retains the transport distinction and original child reports,
avoiding repeated review/reconstruction on a fresh-session resume. Its freshness
remains the existing claim, inputs, resources/version and requirements binding;
changed governing inputs stale affected work without discarding siblings, claims,
budgets or original results. Native API dollar savings have not been measured.

## Acceptance

- An unchanged task prepared from a new process reuses its original result and
  launches zero reviewers, without reconstructing its definition in the model.
- Repeated resume does not write state, grow artifacts or include progress and
  original-output bodies. Result retrieval is explicit and section-specific.
- Partial failure preserves completed siblings; recovery retains run/cycle
  identities, budgets and uncertain publication intent.
- Relevant instructions, resources, preference decisions and Git changes stale
  affected work. Unrelated preferences and history-only cleanup preserve eligible
  reuse. Missing or damaged evidence remains incomplete.
- Checklist acceptance requires every prepared item with evidence, in the primary
  report or bounded follow-ups. Preserve early success, exact exhaustion, replay
  safety and fresh-session recovery without replacing original evidence.
- Existing manifests, fingerprints, settings, publication journals and low-level
  commands remain usable. No cache reset or new default reviewer is introduced.
- Offline checks establish behavior, helper-call reductions and bounded output.
  They do not establish exact native token or dollar savings. Native measurements
  must count preparation, resume, downstream work and result consumption in both
  existing and fresh sessions; preserve regressions and unavailable telemetry.

## Skill-owned configuration

Bundled options and schemas live beside each skill. Private scope overrides keep
their existing stores. Reads merge defaults, scopes and invocation-local options
without persisting an effective-settings cache. No model executable is launched.
Ordered invocation lists express task/finalizer selection and explicit parallel
groups; skill instructions own applicability, iteration and completion.

Feedback replies use one replaceable `reply_prompt`, with its explicit default
in the owning skill's `config.json`. Existing scope resolution supplies the prompt
to the native skill; no runtime launcher or extra prompt store is introduced.
Existing managed option/resource bindings refresh only affected reply work when
the prompt changes. Publication intents keep their original exact body for
reconciliation even after a prompt edit. No durable state or cache identity is
added; original results, completed siblings, budgets and receipts retain their
existing freshness boundaries. Native API dollar savings are unmeasured.

Managed descriptors may select a skill-owned list occurrence by owner, list kind
and index. Retain that compact selector with the existing replay definition so
fresh sessions can regenerate the exact assignment without reconstructing lists.
Live selection, resolved options, relevant policies and resource bytes are checked
on prepare, resume and finish; changed selections stale affected results. Repeated
reviewers have distinct occurrence claims; standalone and iterative review share
stable reviewer identities. Finalizers additionally bind the retained run. Existing
claims, original results and budgets remain available for deliberate reconciliation.
Legacy low-level selectors remain compatibility adapters; they do not create a
second task registry or execution engine.

Skill-owned CI approval adds only hashes of effective option values to the
existing evidence document. Recomputing those small fingerprints detects changed
local/scoped selections on resume without storing expanded settings or provenance
copies. Hook reconciliation retains the current phase snapshot and original task
results, without an additional unconsumed list-history ledger. Configuration and
completion reads use the existing centralized storage resolver.

Resolved plan reads expand only the requested list into ordered occurrence
handoffs with inherited invocation controls and live leaf options and sources.
They are returned on demand, never persisted. This avoids repeated agent reads
and reconstruction in both current and fresh sessions; their freshness ends when
any selected invocation, scope or installed configuration changes. Prepare,
resume, finish and external-action checks still read their own live inputs.

Publication preparation accepts upstream and invocation-local options, resolves
an omitted merge method once and retains only that concrete method in the existing
intent. Recovery reconciles that original intent regardless of later defaults.
Automatic PR flake lookups read the flake skill's live options at the lookup
boundary. New lookup identities depend on the failure fingerprint and issue
limit; lookback and run limits govern explicit history scans. Compatible legacy
scan identities remain reusable, while old results and incomplete claims stay
retained. This reuses the existing scan registry and evidence rather than saving
another settings snapshot.

Retry, CI repair and rerun limits accept zero. Existing run counters and minimum
retained limits still govern recovery; changing settings cannot replenish them.
Review-cycle allowances remain positive and keep their explicit continuation
contract. These changes add no durable store or claim of measured token savings.

## Repository instruction source selection

Repository guidance defaults to all discovered instruction sources within each
file's documented scope and the host's priority rules. An absent policy or legacy
null-policy reset marker uses `all` with empty includes/excludes immediately,
without a source-choice question, configuration request or native delegation.
Explicit repository-only human/all/custom overrides remain supported, with exact
includes/exclusions; reset restores the default. Ordinary reads enforce the
effective policy. The current inventory is reconstructed deterministically, not
stored. All skills and referenced handoff/admission guidance use this boundary.
Hosts may already have loaded repository or ancestor rules; this policy controls
optional dk-workflows adoption and cannot unload context or override host priority.
A conflicting host rule is disclosed and affected behavior remains incomplete
when a requested explicit exclusion cannot be honored.

The dedicated onboarding skill owns requested discovery and explicit refreshes.
It runs read-only in the current session and returns a structured private inventory
of all discovered sources, rules with scope/provenance and authority, repository
facts, inspected settings/preferences, conflicts, gaps and completion. Unreadable
or uninspected evidence makes discovery incomplete rather than silently absent.
The report is constructed in memory from current reads and is never a
governing-source copy. Its freshness ends at changes to repository identity,
effective policy/content, inspected settings/preferences or host context. Ordinary
workflows use the effective policy without requiring onboarding or its full report.
Configuration admission still binds the onboarding prompt as a governing resource.

No new durable field, store, default-policy record, cache or schema is added.
Only explicit overrides and existing configuration-request decisions/receipts are
saved; the existing null-policy reset record is interpreted as the default. Those
records avoid reinterpreting explicit choices and repeating uncertain writes in
fresh sessions. Removing mandatory default-choice explanation, clarification,
delegation and persistence reduces expected complete-workflow API dollar cost in
both the current and next session, without adding a record to create, validate,
load or explain. Native API dollar savings remain unmeasured.
Inventory discovery and targeted content reads remain live costs in both sessions.
Discovery preserves Git's raw pathname bytes and supplements its ordinary ignored
directory pruning with guidance-specific ignored-path queries. Ignored automation
and contributor sources therefore remain subject to the same choice and handoff
checks without enumerating every ignored dependency file. This adds no stored
inventory; selected ignored sources share the existing content freshness binding.
Filesystem decoding also preserves checkout-root whitespace and raw bytes.
Request JSON escapes only filesystem surrogates, retaining existing valid-Unicode
hash bytes and private-store readback. Host ancestor freshness follows the original
requested checkout before normalizing source discovery to the Git root. These
boundary corrections add no state or cache and retain the same freshness inputs.
Implicit source-policy identities retain the requested checkout directory before
Git-root discovery, matching configuration requests for originless repositories.
This keeps existing private addresses and content freshness bindings unchanged.
Literal Unix backslashes remain filename bytes in source references. Handoff
checks keep exact selected lexical spellings separate from resolved-target
exclusions, preserving explicit exclusions and rejection of unsafe source links. Safe
selected directory aliases therefore retain their exact adoption spelling. These
corrections add no state and preserve existing content bindings and reuse.
Directory handoff checks visit each lexical spelling independently and bound link
cycles by resolved ancestors on that branch. An adopted alias cannot hide an
unselected canonical source in a sibling input or enclosing resource bundle.

The freshness boundary is the current repository identity, effective policy and
selected source contents. New sources follow the default all-source mode or the
explicit saved mode and exclusions. Changed/reset choices or selected content
require affected governing inputs and analysis to be refreshed. Task definitions retain only the checkout
and selection/content binding digest; existing capture validates that binding and requires rebuilding stale assignments
and handoffs. This preserves task keys without deleting original outputs,
resetting budgets or discarding unaffected siblings. Old summaries and copied
handoffs cannot promote excluded sources back into rules; retain them only as
historical evidence and rebuild affected governing handoffs from selected original
references. Missing or malformed evidence blocks applicable reuse. Default-selected
sources use the same binding bytes as an explicit all-source policy, preserving existing all-source fingerprints. Checkouts without discovered
sources or an explicit override retain legacy fingerprints. Legacy unbound tasks
with discovered guidance require affected handoffs to be rebuilt, retaining their
original results, siblings and budgets rather than silently reusing stale guidance.
Source checks cover governing resources as well as instructions and inputs.
Explicit custom exclusions and unsafe source links remain visible to admission;
resolved aliases cannot override an exclusion. Unsafe aliases remain rejected
when their regular target is independently selected; the alias does not exclude
that adopted target without an explicit exclusion. Tasks without Git pins use the
caller's repository checkout for their existing binding, then retain that binding
for later sessions. Configuration admission also captures the source-selection
contract and preserves ancestor `AGENTS.md` freshness checks independently of
optional in-checkout source selection. Changed ancestor instructions require
fresh admission under the host's existing instruction precedence. These checks
add no durable fields or copies of source content.

Unchanged fresh-session reads reuse the choice and valid results. Configuration
request intent/receipts supply partial-write recovery without a second journal.
Offline acceptance covers the read-only default, reuse, change/reset, excluded cached
guidance and relevant-input invalidation; it establishes behavior, not measured
native savings or host enforcement of source exclusions.

## Dedicated configuration admission

Saved dk-workflows changes enter through `dk-config`, which hands a stable request
ID and configuration-only assignment to a fresh supported native agent. The main
agent relays clarification and reconciles existing native execution after an
interruption. `dk-preferences` mutations use the same agent and retain their
existing semantic admission rules. Inspection/explanation delegate with a transient assignment and no request ID;
that agent and ordinary workflow reads use existing helpers without creating
request history. Ordinary workflow reads do not delegate. Helpers do not launch or emulate
agents; unavailable native delegation blocks execution. PR work, source edits,
installation and other plugins' settings are outside this assignment.

A new private local Git repository at the centralized state root's
`configuration-history` retains exact asks, necessary clarification excerpts,
normalized typed operations, changed setting leaves, semantic assessments and
admission fingerprints. This is deliberately separate from PR task manifests:
configuration decisions span repositories and PR lifetimes, and reusing the exact
user choice avoids reconstructing its meaning or asking again in a fresh session.
The record is admitted for those consumers, not for audit completeness. Live
settings, preference IDs, domain histories, task fingerprints, budgets and review
caches stay in their existing stores. No transcript, effective-settings snapshot,
rendered summary, status index or model telemetry is retained. Preference admission
context is hashed live rather than copied from the existing catalog. Whole-workflow operations retain sparse edits plus their exact input binding,
rather than copying unrelated live settings. Setting diffs contain only changed
leaves (one independently applicable record for a preference). The retained-operation
selector reconstructs exact preparation inputs deterministically from the existing
store and changed values, avoiding model reconstruction on a fresh-session resume.

Candidates are found deterministically across applicable scopes, then assessed for
semantic contradictions by the native agent. A prepared fingerprint binds the
exact operations, observed values, clarification excerpts, applicable earlier
decisions, settings and governing resources/instructions. Settings resources and
applicable repository instructions are rediscovered on resume, including newly
created instruction files. An unchanged assessment can be reused across sessions;
changed inputs require affected configuration admission again. Configuration
admission conservatively includes inherited scope settings and bundled contracts,
since semantic conflicts can cross setting keys; this does not invalidate or clear
any existing workflow analysis. Fresh sessions use compact `resume` and filtered
`history`, then fetch asks/decisions/operations explicitly as needed. Default
history is bounded to twenty requests and excludes full asks and changed values.

Supersession references individual setting decisions, never a whole request.
Successful replacements/narrowings retire only those decisions; scoped exceptions
retain broader intent. Cancelling preserves the request and commits. Existing
settings without history remain valid with unknown original intent. Explicit user
replacement already resolves a conflict; agreed concrete changes need no second
approval. Deterministic overlap checking is only a floor for native semantic
admission. Helpers cannot prove the agent's interpretation or user authorization.

Existing user-facing writers require a request ID bound to the exact normalized
operation and context. Typed adapters resolve destinations and preserve validators,
precedence, stable IDs, domain history and original lock addresses. A request can
cover independent stores; changes to the same store must be combined in one
operation. Under the history lock and sorted store locks, all operations and
relevant inputs are revalidated before the first write. Commit each write intent
to private Git before changing live settings, verify readback, then commit its
receipt. These minimal progress facts prevent duplicate writes and repeated agent
recovery after interruption. No remote is configured; Git hooks/global settings
are excluded from helper Git operations.

Recovery compares original before/after hashes under locks. Already observed desired
values with an intent acquire their missing receipt without another write;
completed siblings keep their receipts. Remaining writes require unchanged original
preconditions and admission. Unexpected edits block recovery; changed governing inputs require fresh admission
of the original operations while preserving intent and completed receipts; no blind retry, rollback or overwriting of concurrent changes occurs.
An interrupted Git commit is completed before further configuration writes. Applied
requests return their original outcome without replaying settings after later edits.
`resume` itself is read-only, reports stale/reusable admission and identifies
uncertain operations; `apply` performs reconciliation. If recovery is blocked, the
agent reports the retained request ID and affected work as incomplete.

Offline acceptance covers fresh-process admission reuse, request-bound writers,
clarifications, unresolved conflicts, explicit replacements, partial supersession,
scoped exceptions, cancelled history, legacy intent, duplicates, concurrent edits,
new governing instructions, partial application and interruptions between writes
and Git receipts. Native dispatch fixtures check handoff and unavailable-tool
behavior without model/GitHub calls. They establish neither native adherence nor
measured token savings. No new native execution or token evidence accompanies this
change; helper/process counts remain proxies only.

## Decision reconciliation

The Decisions skill owns a configurable `reconciliation_prompt`. Its native
policy reconciles sourced PR agreements with advised and selected alternatives.
The default asks the user about contradictions with settled user or repository
maintainer/admin decisions only when current instructions and an applicable
retained user resolution do not already settle the conflict. Interpret arbitrary
user directions by meaning and host instruction priority, including revisions,
conditions, exceptions and exclusions; no fixed phrases or keyword parser gate
this authority. Apply clear directions within scope without asking for redundant
confirmation. Preserve unaffected commitments and historical evidence.

Fix entry supplies effective commitments and exceptions before comparison;
after advice/selection the coordinator reconciles concrete conflicts before
dependent implementation. Use the selected Decisions task when applicable;
the coordinator owns this duty when the configured plan has not reached it or
omits it, without invoking omitted skills or restoring finalizers. Ranking-mode
questions remain with selection policy; a separate agreement-conflict question
uses reconciliation policy. Native skills interpret evidence and user meaning;
helpers only build, capture and validate ordinary managed handoffs.

Retain only necessary conflict resolutions in existing private managed results,
referencing original decisions, advice/selections and exact user instruction or
answer artifacts, with scope and conditions. Selection remains authoritative for
its analysis; no duplicate decision store, rendered commitment list, settings
snapshot or new task identity is introduced. Pass consumed original sources and
resolution references into fixing and later review/finalization assignments.
Their generated effective commitments must not restore superseded or excluded
choices from the public PR body. Decisions and alternatives-plan checklists
require sourced reconciliation; assigned implementation needs current code and
validation evidence or an already-satisfied disposition, not initial alignment.
Unresolved governing conflicts leave dependent work incomplete.

The small source bindings and actual user resolution avoid repeating native
conflict analysis and user questioning after interruption or a new session.
Reuse survives ordinary commits/cycles while the subject, scope, conditions and
governing evidence apply. Changed relevant directions, choices or conditions
refresh affected reconciliation only; missing evidence blocks applicable reuse.
Retained answers remain historical evidence when surrounding sources change and
must be checked and rebound before use. Explicit user reconsideration/revision
can authorize the existing sourced reassessment path without another question;
resume alone cannot. Preserve original cache keys, results, siblings, claims,
budgets and publication receipts. Current review, CI, authorization and remote
checks remain live. Offline policy transport, fresh-process reuse, partial
recovery and invalidation tests do not measure native judgment or API dollar
savings; expected avoided repeated analysis must exceed evidence handling cost.
See [decision reconciliation](docs/decision-reconciliation.md).

## Shared purpose assessment and architectural alternatives

### Canonical PR history boundary

Automatic alternatives discovery must consult both routes and historical
architecture/whole-problem identities before proposing native analysis. Completed
comparisons and sourced choices are historical design context, not current code
coverage. Validate their immutable result bytes, original captured fingerprint,
native evidence and original completion checklist without recapturing installed
guidance. Code pushes, rebases, route changes and package updates cannot turn
those results into another automatic comparison. Reconstruct outstanding stages
from the original dependency chain; missing/damaged artifacts require restoration,
and unfinished claims or checklists require their existing recovery action.

Only a sourced direct user invocation of `dk-pr-review-alternatives` can request
a fresh comparison. Its stable request identity is retained through interruption;
ordinary iteration, downstream skill calls and changed fingerprints are not that
authorization. Current requirements still require native alignment with retained
decisions. Insufficient decisions require that explicit invocation rather than
automatic reconsideration. Current review, authorization, CI and publication
remain separate live checks.

Use a read-only adapter over existing task entries and original artifacts. Add no
completion flag, registry, copied report or rendered resume plan, and change no
fingerprint bytes, task keys, claims, budgets or publication recovery records.
This avoids repeated paid comparison and reconstruction across routes and fresh
sessions without introducing durable-state loading costs. Historical reuse ends
at damaged/missing original evidence; its applicability to current requirements
is a separate live native decision. API dollar savings remain unmeasured.

During initial inspection, after requirements and repository evidence collection,
the Python controller injects the common assessment prompt into a native
assignment. There is no installed assessment skill or separate model runner.
The ordinary PR-wide assessment identity
`pr-purpose-assessment` is independent of workflow, run, cycle and head. Both
`dk-issue-alternatives` in fix and `dk-pr-alternatives` in incoming
review consume the same original accepted result. Keep only purpose, scope,
selected scoped criteria/weights, explicit 0/50/100 anchors, rationale, mandatory
constraints and existing source references in that result. No copied PR metadata,
discussion, settings snapshot, rendered summary, database or completion flag is
added. Missing sources or damaged original evidence block reuse.

The alternatives skills own their assessment/evaluation guidance, scoring options
and selection policy. `pr_assessment.py` and `assessment_alternatives.py` construct
native assignments with the same default assessment prompts, requirements and
scoring contract. They do not invoke an assessment skill or launch model sessions.
`pr_assessment.resolve` reads the invoked alternatives skill's scoped settings;
`selection.mode` can differ between issue and PR alternatives. A shared original
assessment remains historical context across installed guidance changes. Changed
requirements need live alignment; insufficient decisions need a direct user
invocation of dk-pr-review-alternatives. Mode changes never repeat settled
assessment or selection automatically. Former assessment-skill and owning-workflow settings
remain read-only fallbacks, with canonical skill settings taking precedence at
their scope. Ambiguous conflicting legacy values require an explicit canonical
override. No settings file is rewritten on read. Small fixes normally favor focused implementation
changes and relevant edge coverage. Feature ranking excludes code-change volume
and assesses actual design and maintenance consequences. Mixed work identifies
scopes with applicable rubrics. Native judgment identifies questionable decisions
under those rubrics and evaluation guidance, without a predefined architectural
defect checklist. Helpers validate structure, calculate weighted totals and build
handoffs; the already-running native session assesses, compares and selects.
There is no selector agent or model-session launcher.

Fix alternatives starts from issue requirements and compares whole-problem
approaches, accepting an existing PR as an optional candidate. Incoming alternatives
first inventories architectural decisions throughout the branch, including
inherited decisions, with inherited/introduced/materially_changed origin labels;
it then compares alternatives for questionable decisions, including retaining the
current implementation. Every option is evaluated against the same pinned target
revision, counting implementation work already on the PR branch. Both skills
share rubric enforcement and arithmetic. Per-decision comparisons and settled
choices have separate managed identities; completed siblings survive interruption.
Comparison definitions retain their original immutable inventory reference when
the selected decision is unchanged, so added or changed siblings do not repeat
unaffected comparisons. Interaction definitions likewise retain original selection
and comparison references while the selected option and comparison are unchanged;
automatic mode-only changes do not repeat settled selection, compatibility or alignment work. These small
consumed evidence references avoid paid native recomparison/reconstruction across
sessions, without copying decisions or storing derived status. Missing originals,
changed decision contents, selected options or comparisons end that reuse.
Check interactions among selected approaches before downstream handoff. Alignment
and independent verification receive original rubric, choice and comparison
references deterministically, avoiding native reconstruction of the dependency
chain while retaining source freshness. Fix passes
selected approaches and required changes into authorized implementation tasks and
relevant tests; retaining current code needs no corresponding edit. Incoming
review reports without code edits. Existing independent verification, review
publication and current-head coverage boundaries remain intact, without an added
defect-oriented architectural gate.

Each alternatives skill defaults to semi-manual selection. Automatic applies existing
feasibility, confidence, estimate sensitivity, unresolved-doubt and score-gap
checks (default 10 points); failure returns an unresolved choice without prompting
or implementation. Manual requires an explicit sourced choice of a feasible
compared option. Semi-manual selects a qualifying clear winner, otherwise asks.
Manual source paths are bound and cited in the original selection evidence, with
rationale and every rejected option's reason. Selection grants no additional
permission. Unresolved choices retain choice_pending behavior. A direct dedicated
manual request can refresh selection; automatic settings changes preserve settled
choices. Explicit changed choices require affected interaction and alignment checks.

Initial automatic alternatives completes once per canonical PR across both routes.
Only direct user invocations of dk-pr-review-alternatives bind an original request
to fresh alternatives. The request retains its stable id, source, exact instruction,
invoked skill and user origin; downstream calls and iteration cannot supply that
authorization. Reuse the original shared assessment as historical context. Changed
requirements need live alignment and that dedicated invocation when retained
decisions are insufficient.
Original attempts, claims, fingerprints, sibling results, budgets and publication
receipts remain retained. Ordinary implementation commits do not repeat initial
analysis. Current review, CI and publication readiness still recapture live inputs.
Issue-only invocation performs the same native assessment and evaluation against
the issue and selected target branch in memory, without fabricating a PR record.

New architectural responsibilities are distinct from historical whole-problem
stage identities. Old whole-problem results remain historical context; they cannot establish current
architectural inventory or code-review coverage and cannot trigger a second
automatic comparison. Historical skills and low-level managed gates remain
recoverable under their existing keys and stores; no cache reset or migration is
performed. `dk-issue-implementation-alternatives` is a read alias for
`dk-issue-alternatives`; its task keys and explicit saved settings retain their
original addresses. The removed `dk-pr-assessment` name survives only as the
historical managed identity/settings fallback, never an installed skill or native
execution requirement. The old whole-problem skill is retained as a compatibility entry for
`dk-issue-alternatives`. New discovery uses compact `plan` output,
then targeted original evidence reads, rather than loading a manifest in the
native coordinator. The legacy contracts below document recovery, not the new
initial inspection procedure.

Expected avoided API cost comes from eliminating repeated purpose/rubric analysis
and completed per-decision comparisons in the current and next session. Reusing
small definitions and original evidence references costs less to capture, validate,
load and explain than repeating that native work. Historical acceptance ends at missing/damaged original results, native evidence,
checklist evidence or unresolved claims. Its current applicability ends at changed
requirements, with live native alignment and independent current review required.
Guidance and mode changes alone never launch another automatic comparison.
No current authorization, publication receipt validation or CI check is cached.
Offline fresh-process, partial recovery and invalidation tests establish behavior;
process counts are proxies and native API dollar savings remain unmeasured.

Original alternatives checklist recovery uses the existing primary result and
original checklist under the store lock. A completion-only store operation checks
the immutable primary, captured fingerprint, native evidence, original claim and
bounded checklist rounds before reserving or accepting a response. It cannot
start primary work or replace a report. The managed controller selects this path
only for retained alternatives checklist completion or its replay; current-code tasks
keep their ordinary live capture boundary. Preparing original recovery keys in
one group validates all originals before reserving any checks. No new state,
fingerprint or allowance is introduced. This avoids repeating completed analysis
when an installation changes or disappears while its checklist is unfinished.
An expected entry digest binds the transient assignment snapshot to the locked
operation; a concurrent replacement requires a fresh handoff rather than mixing
two primary results. This digest is never persisted.

Legacy whole-problem history predates the shared purpose/rubric assessment. A
dedicated fresh request may establish that missing shared assessment before its
new comparison. The shared assessment alone cannot switch the retained chain;
once a requested modern chain starts, both automatic routes consume it ahead of
legacy-only context. Original legacy records and pre-rename request bindings
remain intact and recoverable, without a second canonical-history registry.
When an older PR already has unfinished chains on both routes, preparation
follows the same retained-route choice as discovery. The other original chain
does not invalidate that recovery. A legacy fix-owned mismatch can receive
independent verification pinned to its original alignment. Existing verification
and rejection-publication requirements still govern incoming review.

In assessment-only history, original legacy applicability (including a completed
skip) stays authoritative until a dedicated request starts nonassessment work.
Filter by the original request binding before choosing its retained stages, so
another request's later stages cannot hide its unfinished applicability task.
Read renamed alignment inputs through their existing compatibility adapter.

Canonical admission runs again under the existing batch-claim lock, using the
task layer's deterministic callback over captured inputs and retained entries.
The shared rubric does not reserve a comparison owner; the first admitted chain
does. Earlier siblings in one batch are considered before admitting later ones.
A competing route rejects the batch before claims or budgets change. Captures
remain outside the lock, and finish retains ordinary live-input validation.
No canonical marker, queue or readiness cache is persisted. The existing claimed
definition makes the winner discoverable in another session. This avoids duplicate
paid comparisons during concurrent preparations; native API dollar savings are
unmeasured and end when a distinct sourced request authorizes another comparison.
New dependent assignments bind the original request reference inherited from
their accepted prerequisites. Historical readers derive omitted older references
only when the original consumed result matches that prerequisite, rather than
borrowing a newer replacement's request. Replay leaves original captured input
maps unchanged. This uses existing input references and immutable results, with
no copied request contents or new durable request registry.

## Incoming alternatives gate

The following contract is retained for historical managed-stage recovery. Current
initial inspection uses the shared assessment protocol above.

The alternatives review names replace architecture review across skills, options,
handoffs and helpers. Read old saved configuration and managed descriptors through
bounded name aliases without rewriting their files or original evidence. Derive
contract-v2 task keys using their historical identities, preserving attempts,
fingerprints, claims, sibling results, budgets and publication recovery records.
No durable state or migration store is added. This avoids reconstructing settings
and recovery context in a fresh session. Existing freshness checks still recapture
current resources: renamed or changed governing guidance refreshes only affected
work; aliases never grant stale coverage. Historical Decisions headings remain
readable for exact publication reconciliation. Native API dollar savings remain
unmeasured.

`dk-pr-review-alternatives` is a manual alternatives-only entrypoint sharing these
managed stages, reviewer policy and restricted publisher. A new explicit request
requires fresh assessment/comparison even for unchanged code; resuming that
request reuses its completed stages. The assessment references the original
request's stable message identity, source and exact instruction in the existing
input map. Helpers propagate the reference into comparison and require comparison
instead of a simple-change skip. Dependencies refresh selection and alignment
through immutable result references. Existing automatic task definitions remain
unchanged; task keys, original attempts, completed siblings and budgets survive.
Manual invocation enables only this invocation's alternatives policy and stops
before ordinary tasks and finalizers. No model launcher or separate runner exists.

This adds no store, manifest field, completion marker or rendered report. The
minimal request excerpt, when no original input artifact exists, avoids confusing
a fresh review with an interrupted one and repeating paid analysis after a new
session. Its expected avoided reconstruction/review cost exceeds capture and
targeted loading of that source reference. Freshness requires the same request
and all ordinary governing inputs; a different actual request deliberately
refreshes comparison. Replays never mint another request identity or reset limits.
Offline tests establish rerun, recovery and invalidation behavior; native API
dollar savings have not been measured.

Incoming review completes initial alternatives analysis once per PR before its
configured task list. Its replaceable prompt supplies examination guidance only. The
framework supplies stage assignments, mandatory checklist, weighted arithmetic,
selection gates and publication/recovery rules. Standalone reviewer execution and
the independent dk-architecture catalog remain separate.

Pin automatic incoming assessment, comparison, selection, initial alignment and
any independent mismatch verification to the first assessment's Git revisions.
Keep their existing contract-v2 keys, fingerprints, original results, native
observations, completed siblings, claims and budgets. Reuse accepted stages across
later commits, review passes, invocations and fresh sessions. No automatic impact
or alignment pass follows author changes. Missing original evidence and changed
governing requirements, resources or scoring/selection policy block for explicit
reassessment; incomplete claims, checks, pending choice and uncertain alignment
resume as unfinished work. A sourced user choice settles pending selection,
preserving unchanged comparison. New explicit manual requests still
refresh analysis using their original request reference. Retained impact chains
keep their existing replay and dependency-validation contract.
Manual completion uses the transient `gate --manual` selector to require that
request and current alignment. Automatic incoming gate checks can reuse the latest
accepted explicit analysis as retained design context after later author commits;
they do not turn a completed manual request into another automatic native pass.

Ordinary incoming assignments consume immutable initial evidence as design
context, with a mandatory checklist to assess current implementation and scope
against it. They retain current Git pins and independent review, verification,
CI, authorization and publication checks. A simple-change skip is likewise an
initial assessment, never current-head coverage. Material scope changes require
explicit reassessment. Keep original source artifacts stable; later current PR
evidence belongs to ordinary review rather than overwriting the initial inputs.

An initial confirmed mismatch still requires its independently verified
REQUEST_CHANGES receipt and stops ordinary review at that head. After a later
author head, that reconciled receipt and original required changes release
ordinary review, which must verify the fixes. This release does not assert current
alignment or clean review. Missing/uncertain receipts block, and no stale
rejection may be published against later inputs. Repeated same-head entry keeps
the completed rejection outcome; current publication permissions govern any new
action. Disabled alternatives retains all evidence.

This extends reuse of the existing stage records rather than adding a completion
flag, copied decision, snapshot, store or budget. Fresh-session resume reads
compact managed status and targeted original references. Expected avoided cost
comes from eliminating repeated impact/alignment analysis and its coordinator
reconstruction in the current and next session; validating the existing references
costs less than repeating that native work. Freshness ends at changed governing
requirements, guidance, policy or missing/damaged originals, while ordinary
current-head coverage remains live. Offline tests cover unchanged fresh-process
reuse, later heads, partial recovery, explicit manual reruns, rejection recovery
and relevant-input invalidation. Native API dollar savings remain unmeasured.

Use existing managed tasks for assessment, comparison, selection, impact,
alignment and independent mismatch verification. Retain only their definitions,
original immutable results, native observations and dependency references in the
existing PR manifest. There is no alternatives database, completion flag or saved
rendered comparison. Completion is derived from accepted task evidence and the
existing review publication journal when rejecting a mismatch. Native tools execute the prompts; helpers never
launch model sessions. Missing execution/verification evidence blocks dependents.
An alternatives-only rejection is a completed, separately named outcome, with
ordinary review and approval/merge finalizers unperformed.

For explicit manual reviews and retained impact-chain recovery, the following
freshness protocol remains supported. Comparison binds the requirements, discussion, constraints, preferences, prompt
and original diff base/head. Selection separately binds scoring/auto-pick policy
and the comparison reference; changing auto-pick policy repeats selection only.
After changed live inputs, a native impact task compares the original analysis
with current evidence. Reuse requires a supported nonmaterial finding and a fresh
implementation alignment check. Retained applicability and selection are checked
against current guidance without resolving their old live dependencies; nonmaterial
impact explicitly covers applicability, feasibility, confidence and the sourced
choice. It references the reused selection (or assessment for a skip). This avoids
rerunning those agents solely to rebind current pins. Material or uncertain impact reopens affected
analysis, retaining original results, completed siblings, claims and budgets.
Selection, alignment and impact reference exactly the analysis they assess.
Replaying impact resolves its original selection input hash through existing
immutable results and attempt references, rather than depending on the current
selection claim. That source is historical context, never current coverage. This
lets selection refresh after policy or user-choice changes without creating a
claim cycle, copying a contract or repeating impact analysis. The new selection
remains independently subject to native/checklist acceptance. Missing or damaged
original sources block recovery. Selection/impact linkage compares exact captured
result bindings, so another attempt under the same task key cannot upgrade old
coverage. Every normal review handoff includes the settled
decision as governing context.

Alternatives analysis and selection remain in original private managed results.
Neither the alternatives workflow nor Decisions/description skills add a generated
alternatives section to the PR description. Aligned review requires no description
edit or publication receipt, including for analysis-only requests. The legacy
`edit_alternatives_decisions` setting remains readable but cannot enable these
edits. Verified mismatch rejection still requires its independently checked
`REQUEST_CHANGES` review and reconciled receipt under current permissions.

On those manual and retained-impact paths, alignment references the existing current PR metadata artifact through
`alternatives_current_pr`, already used by impact. The gate checks its consumed
body and head against the live PR. This adds no durable field, copied metadata,
rendered summary or new store. Changed description, head or other governing inputs
still require affected analysis and current alignment. Original comparison,
selection, completed siblings, fingerprints, budgets and recovery evidence retain
their existing identities. Historical publication journals and bounded-body
normalization remain available for exact read-only recovery; do not resend an old
description edit or require its section to remain published. External edits end
historical normalization chains and require assessment; ambiguous or circular
chains fail privately. Disabled alternatives retains existing evidence and
bypasses its stages. An unavailable ordinary reviewer cannot block the separately
preflighted alternatives stage or its verified rejection.

The expected saving is avoiding repeated alternatives analysis, user selection,
public-summary generation and description-publication recovery in later sessions. Consumers read compact
status then only referenced evidence. Small definitions/references reuse the
existing accepted-result and publication machinery, so their expected avoided
native analysis and recovery cost exceeds their capture/validation/loading cost.
Freshness ends at changed governing inputs, material/uncertain impact, changed
selection policy or damaged/missing evidence; current authorization, publication
and CI checks are never cached. Offline reuse/recovery tests establish behavior,
not API dollar savings. Native API cost telemetry is unavailable for this change.

Alternatives dependency validation memoizes accepted tasks and shares Git capture
only within one read-only validation boundary, across sibling incoming occurrences.
Managed prepare/resume use separate contexts for assignment construction and live
capture; each reads the live PR once, rather than once per sibling. Contexts end
before native execution, publication or the next helper command. Preparation and
completion keep separate live checks. This replaces recursive repeated reads of the same immutable reports
and diff without adding durable state. Historical reuse separately recaptures
review resources, instructions and native contracts: changed guidance refreshes
comparison even when impact reports that implementation changes are nonmaterial.
Offline capture counts are process/I/O proxies, not native API dollar evidence.

Markdown boundary scanning advances through nested list prefixes by offset and
locates the homogeneous thematic-marker suffix once. It avoids quadratic suffix
rescanning and copying on long descriptions. Offline scan/copy budgets measure
deterministic work rather than elapsed time or native API dollar savings. This
adds no persisted parser output, readiness cache or telemetry.

## Once-per-PR fix alternatives review

This section describes historical fix-stage recovery. Current initial work uses
shared assessment and the issue implementation alternatives component above.

`dk-pr-fix` owns a default-enabled alternatives gate with the same replaceable
reviewer, scoring and selection controls as incoming review. Before its repeated
task list, assess the initial PR, compare reasonable approaches, settle the choice
and assess initial implementation alignment. A mismatch becomes required fix
guidance, never a request-changes review of the author's own PR. Uncertain analysis
or a pending choice blocks progress. A supported simple-change skip completes
the initial assessment without comparison.

Keep these original stages in the existing PR task manifest, with fixed PR-wide
keys independent of run, cycle, head and configured reviewer. The existing `skill`
selector identifies the fix owner; the prepared options bind that owner. Preserve
incoming stage identities and fingerprints. The first assessment's exact Git pins
govern the other fix stages; retain the existing original input artifacts rather
than overwriting them with later PR snapshots. Repeated preparations retain those
pins. Helpers discover the four known task keys and return compact next-stage or
accepted-result references. No completion flag, decision copy, snapshot store or
new budget is added: completion follows from accepted native/checklist evidence.
Completed-stage guards share immutable Git calculations with accepted dependencies
inside the existing transient validation context. Each new boundary captures anew;
resource and instruction freshness checks still run. This avoids duplicate full
diff generation during reuse without adding durable state. Offline capture counts
are work proxies, not measured API dollar savings.

Completed initial analysis runs once for the PR, across new fix invocations,
sessions, commits, cleanup, CI repairs and iterations. Resume unfinished claims
and checks and preserve completed siblings; incomplete or pending work is not a
completed review. A sourced pending selection can be settled without repeating
comparison. The initial decision supplies design context to current fixes and
ordinary reviewers; it never supplies current-head review, CI or publication
coverage. Current work must remain within its sourced requirements and constraints.
Material scope changes require explicit reassessment, rather than automatic
repetition or stale acceptance. Changed governing resources, instruction sources,
original evidence or scoring/selection policy block reuse and automatic reruns of
completed initial stages. Missing or damaged evidence blocks recovery. Disabling
the gate preserves its original records. Incoming review uses its own once-per-PR
evidence; manual alternatives review retains explicit-rerun behavior.
Every result consumed by the incoming gate must belong to the incoming workflow;
an initial fix-plan skip or alignment cannot release incoming review.

Historical fix-stage recovery has an explicit `dk.py reassess` entry point. It
requires one rebuilt stage descriptor and the original request artifact's stable
message identity, exact instruction and source. Bind that artifact through the
existing input map, and retain only its reference/identity, affected task key,
original immutable result hash and authorized replacement fingerprint with a
content binding in the existing managed definition. No copied instruction,
conversation, result, settings snapshot or separate authorization store is added.
Ordinary preparation cannot turn an arbitrary authorization input into permission.
The native coordinator verifies that the cited instruction authorizes the selected
stage; helpers validate the exact artifact and binding, never semantic consent.

Reassessment replaces only that stage's active coverage through the normal
claim/native-evidence/checklist transaction. Original attempts, results and sourced
choices remain historical evidence. Unchanged prerequisites and siblings reuse
their accepted results. A changed dependency result blocks completed downstream
coverage until each affected stage is explicitly bound and freshly completed;
the same original instruction may cover those stages only when its scope says so.
Missing old installation resources produce a compact `reassessment_required`
fix-gate handoff; rebuilt definitions resolve current resources without reading
the removed installation. Missing or damaged original results still require
restoration. Original Git pins and initial PR sources remain pinned.

Repeated identical authorization reuses its original binding and completed
analysis, including from a fresh session. Interrupted claims require explicit
recovery; preparing another authorization cannot overwrite them. Source bytes,
governing inputs or a binding change revoke that authorization, requiring a new
explicit request identity/binding. A superseded authorization cannot restore old
coverage. Retain the original run when one exists, so exhausted review allowances,
same-cycle restrictions, retries, CI counts and publication recovery remain in
force. Reassessment grants no publication/merge permission or budget renewal.
Only validated descriptor bindings can populate the reserved reassessment option.
New execution checks the retained run and review-cycle allowance under the task
store lock, including saved-key and low-level retries after incomplete results;
unchanged accepted reuse does not reserve another run. Missing bindings cannot
turn an incomplete authorized replacement into ordinary unbudgeted execution.
The same locked boundary checks the captured unsigned inputs and consumed request
identity before recovering a claim or reserving allowance. A newer claimed request
supersedes its predecessor even before either replacement records a result.

Each new reassessment attempt retains its request identity alongside the existing
claim fields, so a later request cannot make an older consumed request usable
again. The active binding remains in the managed definition; no second
authorization history is stored.
After an incomplete replacement, validate the latest immutable result and the
most recent complete original through existing attempt/result references before
binding a new request to the latest result. Changed inputs require renewed
authorization even in that state; missing or damaged original evidence blocks.

This small binding avoids repeated native reconstruction of which original result
the user authorized replacing, and avoids repeating that replacement on the next
session. Its expected avoided API dollar cost exceeds capture, validation and
targeted loading of the reference and hashes; freshness ends at changes to the
source, authorized inputs or replacement chain. Ordinary authorization, CI and
publication checks remain live. Offline reuse, interruption and invalidation
regressions establish behavior; native API dollar savings are unmeasured.
The attempt's request identity avoids reconstructing superseded permission or
repeating unauthorized native work in a fresh session. Its expected avoided
recovery cost exceeds storing and checking one identity alongside the existing
attempt; its validity follows that recorded attempt and never grants new scope.

The reused original results and small owner selector avoid repeating paid
alternatives analysis and reconstructing its recovery on subsequent cycles and
fresh-session entry. Their expected avoided API dollar cost exceeds capturing,
validating and loading these existing references; no additional durable store is
admitted. Freshness ends at changed governing inputs or damaged sources, while
ordinary current-head checks remain live. Offline tests cover fresh-process reuse,
partial recovery, new heads, scoped invalidation and separate incoming evidence;
native API dollar savings are unmeasured.

## Workflow simplification reviewer

The incoming and fix workflows independently own an enabled gate, priority, scope
and replaceable reviewer invocation. Compose the additional leaf inside existing
selected reviewer runners, after ordinary groups. Custom task ordering stays
intact; omitting a runner omits simplification. Standalone runner plans do not
inject it. Standalone simplification has its own priority/scope defaults. Managed
workflow instructions govern replacement providers, with explicit option overrides
for the bundled reviewer. Disabled providers are never resolved or preflighted;
malformed configuration still fails validation.

Use the existing skill occurrence selector plus a small workflow selector (owner,
flattened runner index and original invocation-local options). This reference is
stored only in the existing managed definition and spec binding, not a settings
snapshot. A fresh session uses it to derive the live runner options and gate at
prepare, finish and resume. Disabled/removed leaves cannot satisfy stale required
coverage. Re-select the live required plan before completion; old results remain
historical evidence. Policy/provider changes refresh simplification work while
ordinary leaf identities, fingerprints, completed siblings, claims, results and
budgets retain their existing lifecycle. No new store or budget is needed.

The reference avoids reconstructing the caller's governing assignment and stale
coverage recovery on fresh-session resume. Its expected avoided native analysis
and reconstruction cost exceeds serializing and validating that small selector.
Freshness ends when the owning selection, provider, instructions, effective
options or consumed PR context change. Existing resource capture and native
observation contracts remain mandatory; helpers never execute models. Offline
reuse, partial recovery and isolated invalidation checks establish behavior, not
measured native API dollar savings.

Simplification returns read-only recommendations with concrete complexity,
scope, priority tradeoff, behavior preservation and verification evidence. Review
independently verifies maintainability benefit without a functional-bug condition;
fix assesses and implements justified recommendations through existing stages.
Reject or defer weak or out-of-scope suggestions. Changed hunks bound default
edits; optional surrounding edits must be directly necessary for this PR. Every
cycle stays anchored to supplied PR changes, preventing cumulative broad cleanup.
Simplification cannot replace ordinary coverage, publication permission or live
readiness checks.

The workflow selector also resolves the owning runner prompt and native controls.
Append that prompt directly to each prepared leaf assignment and bind a digest of
those governing fields in existing task options. Keep no duplicate prompt or
settings snapshot in the retained definition. Fresh-session recapture rejects
changed parent instructions/controls at reuse and completion, avoiding stale
coverage and repeated reconstruction. Sibling-list and scheduling changes are
excluded from that digest so unaffected analysis remains reusable. Workflow-bound
leaf claims inherit the owning workflow limits and permissions.

Fork-CI approval uses those same retained workflow selectors to resolve every
live incoming reviewer-run occurrence and its composed leaves. Match each leaf's
owner selector, local index and invocation, including repeated reviewers and the
required simplifier. Missing or mixed workflow/standalone coverage fails closed.
Omitted or null selector options mean the same empty override as live selection.
Multiple runner stages require independently retained leaf identities. Existing
content-review identities overlap for identical invocations across stages,
including their default simplifiers; that unsupported combination fails closed.
Historical skill-owned evidence containing only standalone reviewer selections
keeps its raw runner contract; legacy process evidence remains unchanged. No
new mode flag, settings snapshot or durable field is added. Existing task and
snapshot checks preserve source, configuration, native-evidence and finding
freshness. This avoids repeating completed review or reconstructing unsupported
configuration workarounds during finalization and fresh-session recovery, while
retaining original results, siblings, claims, budgets and publication receipts.
Native API dollar savings have not been measured.

## Local commit preparation

Direct commit/message preparation and PR fix/finalization share one semantic
scope assessment before commit cleanup. A read-only helper captures HEAD, staged
and unstaged patches and untracked paths without interpreting relevance, writing
Git state or retaining an inventory. Capture disables filesystem-monitor hooks
and configured clean/process filters, including initialized submodules, so Git
inspection cannot populate filter-managed caches. Content patches disable external
diff/textconv; submodules always use short gitlink summaries to avoid child diffs
that re-enable those drivers. Submodule paths, revisions and dirty markers stay
visible; initialized submodule content can be captured separately with the same
helper. It reports bypassed driver names alongside the evidence: staged content
stays index-normalized while the worktree comparison bypasses external
normalization. Such differences still need native assessment; capture neither hides candidate edits nor runs filters to
resolve them. Native assessment includes suitable existing edits under current
commit authorization and preserves unrelated/excluded hunks
and their staging state. Commit, rewrite and push permissions stay separate;
cleanup retains its clean-worktree and reviewed-tree guards.

No durable state, cache keys or result schemas change. Managed callers use their
existing result for scope evidence, avoiding a second selection protocol and
reconstruction of omitted relevant edits on a later run. Reuse remains bounded by
current scope, exclusions, HEAD, index and worktree content; live changes require
fresh assessment and affected review coverage. Offline Git tests establish capture
and preservation behavior, not native API dollar savings; cost evidence is
unavailable.

## Historical finding follow-ups

Complete discussion and retained original findings feed discovery and independent
current-head verification even when a report already exists. Native agents group
equivalent requests per finding, choose the latest equivalent request and preserve
fixed/rejected/uncertain and explicit accepted/deferred dispositions. At least one new PR commit after the latest
substantive request makes a still-confirmed finding eligible for a reminder; zero
new commits suppress
unchanged reruns. Verified follow-up receipts reset that boundary even before
the reply is visible in discussion. Age governs reminders only; unresolved findings
retain their ordinary approval/CI/merge gates and posting permissions remain live.

The read-only follow-up helper verifies existing complete evidence, captures a
stable paginated PR commit list with its total and constructs source routes and
reply payloads. A deterministic source resolver owns finding identities and
anchor provenance; native SHA selections and prose never establish age. Formal
reviews and original inline comments use their associated review commit only
when complete edit history proves the finding was already present at creation.
Supported Qodo details blocks use stable `/findings/<uuid>` identities. Complete,
undeleted original-to-current GitHub edit snapshots locate each finding's
introduction independently; supported numbering, whitespace and presentation
changes preserve that revision even when the report footer changes. Whitespace
normalization applies only to prose; recognized code and preformatted fragments,
leftover code delimiters and indented quote/list content remain verbatim,
including indentation and whitespace in string literals. Changed or
disappearing/reintroduced finding content is conservatively unknown. The
structural parser reads HTML with recognized code masked at its original offsets;
details tokens in code, comments or attribute values do not delimit findings.
Unsupported code markup makes structured parsing unknown. Numbering and trailing
recognized status badges normalize only in the outer finding summary. If prose
has quotes, backslashes, tabs or uncertain code markers, the finding otherwise compares
verbatim without guessing string boundaries. Qodo introductions require an
unambiguous same-repository report footer in an actual HTML comment on a
standalone line outside code and quotation. Only whitespace or the recognized
Qodo attribution may follow it. A verified PR head force-push event must prove
that SHA arrived before the revision.
Ordinary push arrival without equivalent evidence, unsupported formats, missing
or deleted revisions and ambiguous identities remain unknown. Source/edit and
push timestamps order authenticated events only; commit dates and whole-comment
creation/update times never stand in for per-finding arrival evidence. Unsupported
or rewritten latest anchors stay uncertain. Complete API
parent graphs count PR members reachable from the current head but absent from
the anchor ancestry. Chronological list position is never arrival history:
merging an older-dated side branch can add commits after the request. Complete
short histories need no checkout when API parents prove every member reachable.
Beyond the REST 250-commit cap or when API parent evidence is insufficient, full
local objects supplement the list: pin the captured
head and live target tip, require count/list agreement, prove anchor ancestry and
exclude target-only commits. Changed pins, shallow/missing objects or incomplete
GitHub evidence require restoration and recapture. Timestamps never substitute
for PR membership. Formatting-only bot edits require semantic reassessment of the
changed discussion, while retaining each unchanged finding's original anchor.
Edit/push reads are paginated and recaptured for stability; their compact
finding-specific provenance joins the existing transient inspection fingerprint.
There is no new registry or cache: original histories remain on GitHub and
existing native results and publication journals retain their identities. This
removes repeated model inference over full edited reports, including in fresh
sessions; native API dollar savings have not been measured. Live source changes
invalidate only affected discussion assessments under existing freshness rules.

Inspection returns compact source and publication references; native decisions
stay in existing original task results. Default inspection exposes counts, pins
and candidate ages, with complete commit membership available through an explicit
full-evidence option. Both presentations share the complete internal fingerprint;
planning still recaptures and validates the whole proof. This avoids repeated
model consumption of large histories in this and fresh sessions without storing
derived summaries; changed source pins still invalidate affected work.
Planning recaptures discussion, commits
and original intents and checks its transient input fingerprint. Existing
verified equivalent replies suppress recent reminders even during discussion
visibility lag; pending or undispatched equivalent intents require their existing
recovery rather than regenerated wording. The publisher retains original source,
root parent, payload, head, receipts and uncertain-write reconciliation.
Journal discovery matches the publisher's canonical PR scope, preserving exact
original source URLs and journals. A verified superseded unavailable anchor no
longer blocks age when the native verifier establishes a later substantive
request; unknown latest anchors leave reminder age uncertain, while uncertain equivalent writes still block.
Pending publication barriers share the publisher's independent-reply exception
for unrelated review requests; other pending kinds retain their barriers.
Review-route planning also revalidates an existing `no_resend` disposition through
the publisher's read-only recovery handler. Changed head, requested reviewers or
matching review/timeline evidence requires reassessment; the original pending
intent and disposition remain intact without another durable record.

Unknown or rewritten report anchors cannot permanently suppress a newly confirmed
finding that has no equivalent open thread or current substantive request. Native
verification records finding-specific coverage evidence in its existing original
result. The transient planner checks referenced thread states and current
receipts and hands a changed-line candidate to ordinary review publication under
explicit effective route grants.
Age remains unknown; no threshold proof is manufactured. A general bot summary is
not an unresolved thread. Coverage/location or permission gaps remain incomplete,
and fixed/rejected/accepted/deferred/uncertain dispositions remain excluded.

Destination selection is independent of the original report location. For an
age-eligible finding, prefer its original open inline thread or an equivalent
open inline thread from the verified substantive requests. The inline publisher
binds the selected comment and root while the body preserves the original finding
link. Otherwise use ordinary changed-line review publication with its coverage,
location and permission checks; never generate a top-level reminder from a review
body or summary. Missing permissions/location leave publication incomplete.
Existing top-level follow-up operations retain their original recovery identity.
This changes only transient routing, adds no state and avoids unnecessary summary
reply generation/recovery across sessions. Existing analysis and receipts remain
reusable within their governing freshness boundaries; changed routing guidance
refreshes affected work without resetting siblings or caches. API dollar savings
have not been measured.

Equivalent prepared and pending intents recover their original operation;
verified current-head receipts suppress reposts before discussion visibility,
including findings nested in aggregated review payloads. The ordinary publisher
retains its live authorization, changed-line and publication checks.

No durable follow-up registry, commit-list cache, copied result, rendered plan or
new counter is admitted. Existing fingerprints, budgets, original results and
completed siblings retain their lifecycle. Reusing valid original analysis and
publication evidence avoids repeated semantic discovery and duplicate uncertain
writes in a fresh session without a new state's creation, validation, loading or
explanation cost. Freshness ends at changed governing instructions, relevant code,
discussion, request meaning, source pins or publication evidence; only affected
work refreshes. Offline call counts and reuse tests are proxies; native API dollar
savings across current and subsequent sessions have not been measured.

## Prose finalization

The fix workflow selects one prose editing finalizer after review converges and
before commit cleanup and final CI. It is outside the repeating reviewer list.
Plain English is the default; ASD-STE100 and installed providers use the existing
invocation/list replacement contract. Incoming review and custom empty lists do
not acquire an implicit pass. Native skills identify prose and preserve meaning
and protected syntax; deterministic helpers select candidate Git diffs only.

The shared prose helper captures original PR pins, guidance and consumed sources
through existing managed definitions. Accepted native results retain only input
baseline/head and output commit references. Subsequent handoffs compare the
current tree to that accepted output and select a delta, or reuse an unchanged
pass. A previous immutable result reference in existing managed inputs preserves
the dependency without copying output or adding a registry/completion flag.
Original claims, results, completed siblings, review budgets and publication
recovery remain intact. Interrupted work reconciles the original claim; pending
checklists finish without repeating prose analysis. Edits are committed through
existing authorized stages before accepting the output reference, and affected
review coverage refreshes before final CI.

These small references avoid repeating full prose analysis and reconstructing
its accepted boundary in a new session. Their expected avoided API cost exceeds
capture, validation, targeted loading and explanation costs. Freshness requires
unchanged instructions, provider/options, consumed resources/references and
original base/target content; changes require full affected analysis. Later text
changes require delta analysis. Missing evidence or Git objects blocks reuse.
No current authorization, publication or CI check is cached. Offline fixtures
establish reuse, recovery, delta selection and isolated invalidation; native API
dollar savings have not been measured. No token/process proxy is a dollar claim.

## Configurable bundled role behavior

Shared invocations accept a role-keyed `behavior` map. Each `override` and `append`
field resolves independently through user, upstream, repository and invocation
precedence. Omission inherits, explicit null clears, and text replaces at each
scope. Compose replacement or shipped default, then addition; original assignment
instructions remain separate. Every bundled skill has a maintained default prompt
under `codex/skills/SKILL/prompts/`; generated alternatives, verification,
configuration and supporting roles have named resources. Synchronize these into
Claude's distribution. External plugins retain ownership of their prompts.

Entry and execution contracts stay mandatory across replacements: host/repository
rules, scope, output/evidence/checklists, permissions, budgets, publication, CI and
historical alternatives/reassessment boundaries. Specialized prompt options still
construct default behavior. Full replacements suppress that behavioral text and
helper-injected methodology without deleting settings. Deterministic helpers
resolve prompts before native assignment; they never launch model sessions.
Configuration-agent self-edits apply on its next assignment. CI diagnosis uses
its existing capture digest to bind consumed role behavior; optional upstream and
invocation edits remain small selectors in the existing diagnosis evidence,
avoiding repeated diagnosis while unsent retries still revalidate. Uncertain
publication retains its original operation and reconciliation boundary.

Only sparse scoped edits use existing configuration records and request-bound
operations. Existing intents, original before/after hashes and receipts reconcile
uncertain writes without another action store. Managed definitions retain role
selectors and original invocation edits; consumed prompt digests and role resources
use existing freshness. Rendered prompts are constructed transiently in the existing
assignment, not saved as another recovery artifact or settings snapshot. Task keys,
original results, completed siblings, claims, retries and publication recovery
remain addressable. Unrelated role edits are excluded from occurrence identities
and consumed-role fingerprints. Owning runner behavior governs its leaves, while
unrelated internal roles do not. Historical alternatives retain their explicit
reassessment boundary and original evidence.

Targeted default/effective/source inspection and deterministic handoffs avoid
reconstructing prompts in this run and the next fresh session. This adds no state
store or rendered-prompt cache to create, validate, load or explain; sparse settings
are the requested durable configuration itself. Freshness ends at changed consumed
behavior, governing contracts or relevant inputs. Offline reuse, partial recovery,
role invalidation and uncertain-write tests establish behavior; native API dollar
savings remain unmeasured.

## Configuration layout upgrades from saved intent

The declared configuration layout revision is independent of package releases.
Revision 1 freezes the existing layout as baseline, including historical records
without a revision. Installation and unchanged reads create no upgrade history.
Future deliberate revision changes gate configuration consumption before ordinary
workflow execution. The existing session reserves one upgrade, delegates its native
configuration-only assignment, applies admitted typed operations, reloads settings
and resumes the original workflow with its original permissions. Helpers never
launch model sessions. Unsupported newer layouts, corrupt evidence, invalid
bootstrap behavior or unavailable native delegation leave affected work incomplete.

The same private configuration history owns upgrade requests. A target revision,
minimal scope selectors, a checkout-identity fingerprint, a claim token and
native-agent binding prevent competing
paid interpretations and uncertain duplicate delegation. There is one active
upgrade per history because user settings span repositories. Short history locks
serialize reservations and writes; configuration consumers hold shared read locks
through their complete read and check the upgrade barrier. Existing request writers
check the same barrier, including previously prepared requests. Ordinary baseline
reads without history have no filesystem side effects. Unaffected installations
continue reading. Within an affected history, v1 conservatively gates all scoped
reads, since every effective workflow can inherit user configuration.

Ownership has no timeout. Only reconciled native terminal evidence (or proof that
an unbound reservation never started) permits token rotation. Retain that original
evidence reference with the binding; stale owners cannot assess, clarify, cancel
or write. Fresh sessions inspect compact request status and targeted ownership,
sources, original asks or receipts. Uncertain delegation remains incomplete until
reconciled. The existing host owns evidence truth; helpers enforce its required
shape and binding without pretending to prove native execution.

Active setting decisions are computed from original applied records, successful
setting replacements/narrowings and prior translation lineage. Superseded masks
prevent partial asks from restoring obsolete portions. Removals remain active
decisions; scoped exceptions retain broader asks. Native assessment covers all
active asks in user/current-repository/current-PR scopes and inherited upstream
context. Unexplained recorded/current-value conflicts require clarification.
Prior applied clarification references remain discoverable without copying answers
or repeating questions; native admission still checks their subject and conditions.
Preserve unrecorded settings with clear meaning; ambiguous intent or unsupported
settings require a sourced decision. Incompatible upstream stores require their
own checkout. Bootstrap resolves just the configuration role and controls, rather
than requiring the complete obsolete settings to pass the new validator. Known
revision-1 instruction selections retain their baseline discovery semantics during
bootstrap even when the target validator rejects their mode. This preserves
exclusions without resetting the policy; unknown or invalid bootstrap selection
remains blocked. Inferred parent-domain stores join explicit upstream inputs.

Validated proposals retain sparse changes in existing request operations.
Lineage links each active source decision to translated leaves, a preserved
source or a sourced retirement. Existing readback receipt namespaces identify
original domain store owners, including baseline records without owner metadata.
Match their stable namespace and filename against current scoped stores, without
resolving a removed checkout or reading arbitrary historical paths. No duplicate
owner field is saved.

Revision receipts distinguish workflow, domain-store and CI owners when scope
identities differ, avoiding false coverage of an untouched store. Pure workflow
and instruction-policy reads need only their workflow receipt; the global active
upgrade barrier still protects every consumer.

Skill and behavior reads also require the repository domain receipt for the
legacy stores they consume. A different checkout namespace cannot supply that
receipt; use the owning checkout when it is missing.

CI identity and explicit-chain policy readers check the stores they select,
including nested calls. Ordinary readers retain their shared lock while nested
readers check admission; only request-evidence reads bypass admission. Readiness
reuse lasts one outer read and is invalidated on exit, including copied execution
contexts. It creates no durable state and never carries into another command.
The reservation fingerprint blocks paid reassessment under a changed
checkout namespace; restore that identity for recovery or cancel before any
write. Its small load/validation cost is expected to be lower than another
native assessment and uncertain-store reconciliation. It stays valid only for
that original identity. New migration leaves retain original ask references;
migration is never a new user preference. Historical requests remain immutable
and never replay completed writes. Existing intent-before-write, original
before/after hashes, locks and readback receipts preserve completed siblings and
reconcile partial application. Recovery never overwrites unexpected values.
Completed upgrade requests supply per-scope revision receipts without a separate
index.

Expected avoided API cost is one semantic interpretation per valid upgrade,
no competing analyses and targeted recovery in the next fresh session. The small
revision/claim/binding, lineage, existing sparse operation and receipt fields avoid
reconstructing original choices and uncertain writes; their expected avoided paid
interpretation exceeds their creation, validation, loading and explanation cost.
No copied asks, conversations, configuration snapshots, derived status registry,
telemetry or migration database is added. Freshness binds relevant current store
values, active source decisions and clarifications, schemas/defaults, consumed
behavior and applicable governing instructions. Changed inputs refresh affected
admission; partial recovery preserves original operations, lineage and receipts.
Existing review keys/results, siblings, budgets and publication recovery stay
intact. Current authorization, publication and CI checks remain live. Offline
reuse/concurrency/recovery tests are proxies; measured native API dollar savings
are unavailable and are not inferred from process counts.
