# Private workflow usage attribution

Workflow usage is separate from the reviewer quality ledger. It observes lifecycle
invocations, stable CLI helper dispatches, provider attempts, review coverage and
checkpoint reuse. Both standalone review and iteration workflows are included.
Collection is passive: it starts no model calls, changes no review contracts or
budgets, and supplies no evidence to a workflow gate. Failed telemetry storage
leaves a warning and does not replace the helper or provider result.

The lifecycle receipt identifies a private versioned workflow record. A nested
invocation with a known parent joins that workflow; a fresh root has its own
identity. Resuming a retained invocation keeps its identity. Each actual helper
dispatch and provider attempt receives a separate identity. Records retain parent
links, role, stage, invocation, runtime/plugin identity, repository and available
pinned evidence. They also retain observed provider, model and effort. Unknown
profile settings remain unknown; this feature does not inspect host credentials
or invent effective settings from defaults.

An explicitly selected retained invocation takes precedence over a different
inherited invocation context. Same-invocation transport ancestry is preserved.
Artifact fingerprints are best-effort observations of regular files up to 16 MiB;
nonregular, oversized or changing files are skipped with a warning. Native helper
parsing and evidence validation remain authoritative.

Helper stages cover preparation, coordination, review, fixing, replies, CI,
cleanup and completion. They measure the work dispatched through the bundled
CLI. Native editing and reasoning outside these processes have no automatic
measurement. In particular, an observed fixing gate is not a measurement of the
host agent's edits, and a CI wait is not a measurement of hosted CI resources.
Helper dispatch counts describe local CLI calls, not all agent coordination calls.
Unobserved host and external agent activity remains explicitly unknown.

## Outcomes and coverage

An entered invocation or in-flight provider is unfinished until a terminal
observation is retained. Interruption and failure remain distinct from normal
return. A helper's zero exit code records a return, not workflow completion.
Typed helper receipts can report incomplete or blocked work even after a zero
exit. Lifecycle completion only closes that invocation; it does not establish a
completed engineering workflow or a merge.

The review runner records actual complete/incomplete coverage and its configured,
executed and reused reviewer identities with the pinned head, base, preparation
and session. A reused checkpoint is a reuse observation with no new execution or
usage. It cannot establish that another provider ran. Existing review session and
architecture evidence remain the authority for acceptance.

Iteration completion comes from an actual terminal `finish-run` observation.
Completed and limited outcomes remain separate. Neither a clean review nor green
CI establishes workflow completion. A workflow can be completed while its usage
coverage is partial; reports keep these separate so missing measurements cannot
make a completed workflow appear fully measured.

## Measurements and privacy

Provider observations retain available raw usage and its source, elapsed duration,
prompt size and captured transport output size. Inclusive provider totals and
descendant usage use the existing evidence-based usage accounting: a measured
inclusive ancestor covers matching child components, and an unmeasured component
remains partial. Parent elapsed time and child durations are not summed into a
whole-workflow duration. Equal quantities without matching identities are not
deduplicated. Missing tokens, compactions, tool output sizes, model-request counts
and host coordination observations remain unknown rather than zero.
Managed lifecycle registration can precede its transport identity; without an
observed ancestry link, its host component conservatively remains unknown.

Records are private under the user's state directory, outside the source checkout
and plugin. They are not distributed or automatically deleted. The existing
reviewer quality ledger and its API remain unchanged. These observations support
local attribution; they do not establish billed cost, performance improvements or
review recall.

## Read a retained workflow

Lifecycle `begin` receipts include `workflow_usage.path` when collection starts.
Use the retained invocation with the normal CLI:

```text
python3 PLUGIN_ROOT/scripts/dk.py --invocation INVOCATION_FILE workflow-usage
python3 PLUGIN_ROOT/scripts/dk.py --host codex workflow-usage --workflow PRIVATE_WORKFLOW_FILE --group-by stage --group-by role
```

The local query returns version 1 data with workflow outcome, latest review
coverage, lifecycle closure, execution statuses, checkpoint reuse count,
unobserved stages, unknown host invocations, component usage coverage and retained
events. `--group-by` uses the existing usage dimensions: repository, stage,
provider, model, effort and role. Querying does not run a provider or create a
measurement.

Python integrations can use `shared.workflow_usage.context()` and
`observe(kind, status=..., evidence=...)` for evidence available to that process.
The supplied evidence remains an observation; it grants no publication or review
authority. Only actual `run_state` terminal observations establish the iteration
outcome. Standalone workflows retain their coverage and lifecycle status without
inventing an iteration completion record.
