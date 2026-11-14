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

## Managed task interface

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

Repository guidance is discovered before adoption. At first native workflow
initialization, `dk-config` presents relevant rules and provenance and obtains a
repository-specific human, all-source or custom choice, with exact includes and
exclusions. Discovery reads are evidence-only; ordinary source reads enforce the
saved selection. The current inventory is reconstructed deterministically, not
stored. All skills and referenced handoff/admission guidance use this boundary.
Hosts may already have loaded repository or ancestor rules; this policy controls
optional dk-workflows adoption and cannot unload context or override host priority.
A conflicting host rule is disclosed and affected behavior remains incomplete
when the requested exclusion cannot be honored.

Only the mode and exact source references, or a minimal reset-pending marker,
are saved in private repository configuration. The pending marker prevents a
reset from falling back to pre-feature cached guidance. The choice avoids repeated
source discovery interpretation and asking the same question in subsequent skills and fresh sessions. The existing
configuration-request history retains the user's choice and write recovery;
there is no new source-content snapshot, rendered rules summary, effective
settings cache or transcript. The expected avoided native cost of repeated
source-choice explanation and clarification exceeds reading and validating this
small record for repeated repository use; actual API dollar savings are unmeasured.
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

The freshness boundary is the current repository identity, saved choice and
selected source contents. New sources follow the explicit saved mode and
exclusions. Changed/reset choices or selected content require affected governing
inputs and analysis to be refreshed. Task definitions retain only the checkout
and selection/content binding digest; existing capture validates that binding and requires rebuilding stale assignments
and handoffs. This preserves task keys without deleting original outputs,
resetting budgets or discarding unaffected siblings. Old summaries and copied
handoffs cannot promote excluded sources back into rules; retain them only as
historical evidence and rebuild affected governing handoffs from selected original
references. Missing or malformed evidence blocks applicable reuse. Low-level
uninitialized captures retain compatibility with existing fingerprints; native
workflow initialization still requires the source question before source loading.
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
Offline acceptance covers initialization, reuse, change/reset, excluded cached
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

## Incoming architecture gate

`dk-pr-review-architect` is a manual architecture-only entrypoint sharing these
managed stages, reviewer policy and restricted publisher. A new explicit request
requires fresh assessment/comparison even for unchanged code; resuming that
request reuses its completed stages. The assessment references the original
request's stable message identity, source and exact instruction in the existing
input map. Helpers propagate the reference into comparison and require comparison
instead of a simple-change skip. Dependencies refresh selection and alignment
through immutable result references. Existing automatic task definitions remain
unchanged; task keys, original attempts, completed siblings and budgets survive.
Manual invocation enables only this invocation's architecture policy and stops
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

Incoming review assesses architecture before every configured task or parallel
review group. Its replaceable prompt supplies examination guidance only. The
framework supplies stage assignments, mandatory checklist, weighted arithmetic,
selection gates and publication/recovery rules. Standalone reviewer execution and
the independent dk-architecture catalog remain separate.

Use existing managed tasks for assessment, comparison, selection, impact,
alignment and independent mismatch verification. Retain only their definitions,
original immutable results, native observations and dependency references in the
existing PR manifest. There is no architecture database, completion flag or saved
rendered comparison. Completion is derived from accepted task evidence and the
existing publication journal. Native tools execute the prompts; helpers never
launch model sessions. Missing execution/verification evidence blocks dependents.
An architecture-only rejection is a completed, separately named outcome, with
ordinary review and approval/merge finalizers unperformed.

Comparison binds the requirements, discussion, constraints, preferences, prompt
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

Decision edits use the existing exact-body publication journal, constrained to
Architecture decisions under the configured Decisions heading. Recognize the
workflow's own edits only by exact reconciled before/after body chains in existing
PR publication journals. Consecutive own edits normalize to the original governing
body, avoiding self-invalidating analysis across policy or rationale updates. No
lineage database is saved. External edits end the chain and still require impact;
ambiguous or circular histories fail privately rather than guessing. The gate reads the live
PR head and selected decision subsection before releasing reviewers; a historical
receipt alone cannot establish that the decision remains published. Unrelated
body edits require impact bound to the existing current PR metadata source.
Code-only head updates preserve the receipt when the selected
subsection remains exact and current analysis/alignment are accepted. Public text contains design
reasoning and rejected alternatives, never private comparisons, handles or task
state. Analysis-only and denied decision permission leave publication incomplete
and private; disabled architecture bypasses all stages and new writes without
removing retained evidence. An unavailable ordinary reviewer cannot block the
separately preflighted architecture stage or its verified rejection.

The expected saving is avoiding repeated alternatives analysis, user selection
and uncertain-publication reconstruction in later sessions. Consumers read compact
status then only referenced evidence. Small definitions/references reuse the
existing accepted-result and publication machinery, so their expected avoided
native analysis and recovery cost exceeds their capture/validation/loading cost.
Freshness ends at changed governing inputs, material/uncertain impact, changed
selection policy or damaged/missing evidence; current authorization, publication
and CI checks are never cached. Offline reuse/recovery tests establish behavior,
not API dollar savings. Native API cost telemetry is unavailable for this change.

Architecture dependency validation memoizes accepted tasks and shares Git capture
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
