# Automatic configuration layout upgrades

The integer in [the layout declaration](../runtime/config_layout.json) is
independent of the package version. Revision 1 is today's baseline: installing
this release, reading unchanged settings and running `upgrade-check` create no
request history. Raise the declaration only for an intentional future layout
change. Existing requests without a revision belong to revision 1. New requests
and successful upgrade receipts record their revision; completed historical
requests stay immutable and never replay writes. Receipts distinguish repository
and CI owners when a fork PR selects base-repository CI exceptions. An upgrade
uses the head repository checkout for its domain settings; upstream domain
stores need their own checkout when incompatible.

Before consuming configuration for a workflow, call `python3
PLUGIN_ROOT/scripts/config_requests.py upgrade-check --checkout REPO` with the
same `--repository HOST/OWNER/REPO`, optional `--upstream` and canonical `--pr`
as the workflow. This is read-only. `ready` permits normal reads.
`upgrade_required` requires `upgrade-start` with that context. Repository
ownership must match the checkout and PR head identity for domain settings; a PR
URL alone cannot authorize unrelated checkout guidance. `waiting` gives the
original request reference: wait or reconcile its native agent, never delegate a
second agent. `blocked` or an error identifies evidence or ownership to restore.
Do not consume affected settings while upgrading. Read helpers enforce the
barrier and return a recovery command. Keep the original workflow assignment,
invocation options and all permissions, including explicit merge authorization,
in its existing handoff. An upgrade neither revokes nor grants workflow
permissions.

## Native assignment and ownership

Only `upgrade-start` can atomically reserve a new upgrade. The sole winner gets
`delegation:reserved`, `claim_token` and a configuration-only handoff; other callers
receive the same request ID and waiting status, without a delegation grant. A compact checkout-identity fingerprint prevents
reinterpreting a reservation under a changed Git origin. Restore its original
identity for recovery, or cancel an unwritten request and start with current
context. V1
serializes upgrades across the entire private history because user settings are
shared between repositories. No elapsed-time lease exists.

The running session uses supported native delegation, preserving the returned
behavior, additional instructions and requested model/effort controls. If these
controls or native delegation are unavailable, report incomplete with the original
reservation. Never launch Codex or Claude Code, or substitute another model runner.
Bind the returned native identity immediately using `upgrade-bind --request-id ID
--claim-token TOKEN --binding HOST_AGENT_ID`; bind before the agent assesses or
writes. A configuration agent may inspect references before binding but must wait
for that binding before admission. After interruption reconcile the original host
identity before continuing; an unbound reservation requires host evidence that
native work never started. An uncertain delegation is incomplete, not permission
to create another agent.

`upgrade-recover --request-id ID --claim-token TOKEN --file EVIDENCE.json` rotates
the claim only after native reconciliation establishes the old agent cannot act.
Evidence is `{binding: HOST_AGENT_ID, state: terminated|completed, reference: HOST_EVIDENCE}`;
for an unbound reservation use null binding and `state:not_started`. These fields
reference actual host observations; a helper cannot verify native execution or
manufacture them. Never infer quiescence from time, a missing response, an API
error or a caller assertion without native evidence. Recovery returns a new
reservation; every stale token fails. The claim and binding are available through
the private targeted `show --section ownership` read for fresh-session recovery.

Pass the current token to `prepare`, `clarify`, `apply` and `cancel`. The same
request commands, store locks, admission checks, intent-before-write commits,
readbacks and receipts own application. No separate migration writer exists.
A completed apply returns the original receipt without replaying changed settings.
Uncertain writes and completed siblings retain their original operations and
receipts. Reprepare with `operations:retained` after changed governing inputs;
partial recovery must retain the translation lineage too. Unexpected store edits
block recovery. Cancelling cannot discard an uncertain or applied write. If the
layout declaration changes during application, restore a compatible installation
declaring the original layout, reconcile those writes and receipts, then upgrade
to the newer layout. Elapsed time cannot make those writes safe to discard.

## Reinterpret all active intent

Inside a reserved upgrade assignment, use targeted source/store references as
evidence rather than recursively starting an upgrade or consuming workflow settings.
The native configuration agent reads `show --section sources`, then targeted
original `ask`, `decisions`, `assessment` and clarification sections as needed.
Historical domain ownership comes from existing readback receipt namespaces,
including records without owner metadata. Match the namespace and filename to
current scoped stores; never resolve a removed checkout or copy another owner
field into history. A changed domain-store namespace needs its own revision
receipt. Pure workflow and instruction-policy reads can reuse their workflow
receipt while the active upgrade barrier still protects all consumers.

Skill and behavior reads also require the repository domain receipt for the
legacy stores they consume. A different checkout namespace cannot supply that
receipt; use the owning checkout when it is missing.

CI identity and explicit-chain policy readers check the stores they select,
including nested calls. Ordinary readers retain their shared lock while nested
readers check admission; only request-evidence reads bypass admission. Readiness
reuse lasts one outer read and is invalidated on exit, including copied execution
contexts. It creates no durable state and never carries into another command.

Source entries include targeted `clarification_references` to applicable earlier
answers; reuse an answer only while its subject, scope and conditions still apply,
without copying it or asking again. Source entries identify active setting decisions,
explicit `masks` for superseded parts and original ask references. Never restore a masked part of a multi-setting
ask. Removals, narrowed decisions and scoped exceptions remain meaningful intent;
a repository exception does not retire a broader user decision.
Source entries distinguish historical origin decisions from active leaves and mask
unrelated or superseded portions at every referenced generation. Freshness follows
these original decision and clarification references transitively. Translated
sources retain `origins` and translation lineage pointing back to the original
asks, rather than becoming new user preferences.

Read current store values from returned store references as evidence, including
settings without request history. Reinterpret every active ask across user,
current-repository and current-PR scopes, using later decisions, necessary
clarifications, applicable instructions, current values and the target schema.
Inherited upstream settings are context. Never edit their stores from this
checkout; incompatible upstream settings require their own checkout and upgrade.
The bootstrap resolves only valid configuration-agent behavior, instructions and
native controls from the old records, without first validating unrelated obsolete
options. Known revision-1 instruction selections keep their original discovery
semantics during bootstrap even when their mode is obsolete in the target schema.
Their include/exclude masks remain active; this is not a reset to all sources.
Ambiguous or invalid bootstrap fields and corrupt files block admission.
Unsupported newer revisions require a compatible installation, never a downgrade.

Preserve unrecorded settings when their meaning is clear. Ask for clarification
when it is ambiguous, when recorded asks conflict with current values without an
explained later choice, or when the target cannot represent a setting. Record the
necessary answer with `clarify`. Never invent missing intent or silently discard
an unsupported setting. A direct, already recorded user choice needs no repeat
approval. Helpers check structural coverage; the native agent owns semantic truth.

Prepare the smallest typed operations through [request admission](configuration-requests.md).
An empty operation list is allowed for an upgrade that preserves all settings.
The preview supplies the admission fingerprint, active candidates and changed
decision IDs. Alongside the usual assessment, submit `lineage` with exactly one
entry for every candidate:

```json
{"request":"cfg-SOURCE","decision":"d-SOURCE","targets":["d-TARGET"],
 "disposition":"translated","evidence":"Source ask and settled interpretation reference."}
```

`translated` requires target decision IDs (one source may split into several).
`preserved` and `retired` use empty targets. Retirement needs sourced evidence
explaining how the ask remains satisfied or the user's removal decision. Preserve
broader scoped decisions independently. The assessment must review every candidate
and retain unresolved questions. A recorded/current-value mismatch requires a
clarification excerpt before completed admission. Renamed or removed obsolete keys
can be repaired with a validated whole-workflow proposal; history retains only its
sparse changes. The obsolete input need not pass the new validator, but the
resulting configuration must. Relevance fingerprints bind current values, source
decisions, clarification, schemas, behavior and governing instructions.

After successful application, reload configuration and continue the original
workflow. Resume valid existing analysis, claims, budgets and publication receipts;
changed relevant inputs refresh affected work under its existing freshness rules.
No upgrade clears review caches or authorizes external actions. Native API costs
are unmeasured; offline process counts establish no dollar savings.
