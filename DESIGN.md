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
Automatic CI discovery reads the flake skill's live options at the scan boundary.
Default-limit scans keep their existing fingerprint; nondefault limits join the
failure fingerprint, so changed limits refresh only affected scans while old
results and incomplete claims remain retained. This reuses the existing scan
registry and evidence rather than saving another settings snapshot.

Retry, CI repair and rerun limits accept zero. Existing run counters and minimum
retained limits still govern recovery; changing settings cannot replenish them.
Review-cycle allowances remain positive and keep their explicit continuation
contract. These changes add no durable store or claim of measured token savings.

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
