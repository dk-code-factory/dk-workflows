# Workflow design

## Objective and state admission

The agent decides and invokes native skills. Python handles deterministic reads,
input capture and recovery. Optimize total model tokens across the current task
and subsequent runs, including a new session with no inherited conversation.

Durable state is justified only when its expected avoided model work exceeds
the tokens needed to prepare, maintain, validate and consume it. A proposed state
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

## Managed task interface

`scripts/dk.py prepare --file REQUEST.json` accepts explicit task definitions.
One helper call builds specs, captures current inputs, claims eligible work and
returns `invoke`, `reuse` or `recover_required`. It includes expected native
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

The low-level spec/start/record API remains compatible. Preparing a task through
the managed interface can attach a definition to an existing reusable entry
without invoking its reviewer, resetting budgets or changing its fingerprint.

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
