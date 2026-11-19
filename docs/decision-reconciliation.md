# Reconciling decisions and alternatives

`dk-pr-decisions` owns `reconciliation_prompt`. Resolve its effective value
through [skill-owned configuration](skill-configuration.md), including selected
invocation-local options, and apply that prompt in the already-running native
session. The default asks the user about unresolved contradictions between an
advised or selected alternative and a settled user or repository maintainer/admin
decision. Read original sources to establish the agreement, author and scope;
suggestions and a role label alone do not establish a settled decision.

Interpret arbitrary direct user instructions by meaning and host instruction
priority. They can revise, narrow, condition, suspend or exclude earlier choices,
or explicitly request reconsideration. No keyword, command grammar or separate
configuration change is required. For example, all of these are meaningful:

- "Ignore every decision made in this PR."
- "Keep the compatibility agreement, but reconsider the storage choice."
- "Use the smaller approach even though we previously agreed to add a service."
- "Follow the maintainer's approach only if it preserves offline recovery."

Apply a clear instruction without asking for the same authorization again.
An exclusion removes the covered PR decisions from governing this work; it does
not delete their history, select an alternative by itself, waive validation or
exclude unrelated repository guidance. Preserve all unaffected commitments.
Ask only when a material conflict or scope ambiguity remains unresolved by the
actual instructions. A configurable prompt cannot override direct user direction
or host instruction priority.

## Native execution and handoffs

During initial requirements collection, before comparing alternatives, identify
effective agreements and user-directed exceptions from complete PR evidence and
direct instructions. Supply them as scoped constraints. An explicitly requested
reconsideration permits evaluation of conflicting approaches; label the conflict
rather than silently treating the earlier agreement as absent or the candidate
as authorized. After advice or selection, reconcile concrete conflicts before
dependent edits. Reconcile again when relevant discussion or instructions change.

Use the configured Decisions task when selected. The fix coordinator owns the
same reconciliation duty at entry and before implementation when its selected
task list has not yet reached Decisions or omits it. Reading its policy does not
invoke an omitted skill, restore a removed finalizer or change task ordering.
Automatic alternative selection still returns unresolved ranking choices without
prompting. A separate reconciliation question about an existing agreement is
governed by resolved Decisions role behavior, with `reconciliation_prompt` only in default construction, independently of ranking mode.

Before asking, use compact task resume and targeted result reads to find a
previous answer. A current direct instruction or an applicable retained answer
can already settle the conflict. Otherwise explain the conflicting behavior and
sources, ask for the user's direction, and leave dependent implementation
incomplete. Continue independent authorized work. Missing native execution or
required evidence leaves the affected work incomplete; helpers never execute
another model session or decide natural-language precedence.

Keep only necessary conflict resolutions in the existing private Decisions or
owning managed task result. Reference the original decision and advised/selected
alternative, with the exact user answer/instruction source, its scope and any
conditions. If no stable message reference exists, retain the exact instruction
in the existing private instruction artifact; never invent a URL. Pending
conflicts stay explicitly unfinished. Do not create another decisions database,
copy comparisons or persist a rendered list of effective commitments.

Helpers construct and fingerprint ordinary managed assignments from the supplied
original sources. The native coordinator passes the fixing agent the effective
commitments, scoped revisions/exceptions, resolution references, unresolved
conflicts and required implementation changes. Bind consumed original evidence
and user-resolution artifacts in `definition.inputs` or `definition.instructions`;
an unsourced prose summary cannot replace them. Carry these inputs into later
review, Decisions and finalization handoffs so the PR body or historical reports
cannot silently restore a superseded or excluded choice. Publishing or rewriting
existing Decisions still uses its separate authorization and live checks.

The fixing agent implements the reconciled approach within permission and returns
code and relevant validation evidence, an already-satisfied explanation, or a
blocker for each required change. A selection, resolution or completed initial
alignment is not evidence that the current code implements it. Do not mark
dependent implementation complete while a governing conflict remains pending.

## Reuse, freshness and cost

Reuse a sourced resolution across ordinary commits, cycles and fresh sessions
while its subject, scope, conditions and governing sources still apply. Reuse
existing user-resolution evidence even if surrounding discussion requires a new
collection; do not ask again merely to rebind an unchanged answer. A new explicit
instruction can revise or revoke that answer. A materially changed alternative,
decision, requirement or expired condition requires affected reconciliation;
ask only if existing instructions no longer settle it. Missing or damaged
sources block applicable reuse. Preserve original results, unaffected siblings,
cache identities, claims, budgets and publication recovery.

Bind changed governing directions before accepting affected downstream work.
When they change a completed alternatives stage's governing inputs, use the
existing sourced explicit-reassessment path. The user's actual revision or
reconsideration request can supply that authority without a second permission
question; an ordinary resume cannot. Do not clear the initial analysis or accept
stale coverage merely to get past its freshness guard.

The retained source references and actual user resolution avoid repeating paid
conflict analysis and user questioning in the next session. They live in original
managed results and existing instruction artifacts, with no extra state store or
settings snapshot. Compact discovery and targeted evidence reads bound resume
cost. Savings end at relevant source/condition changes or missing evidence;
current authorization, code review, CI and publication remain live. Offline tests
verify policy transport, evidence reuse, recovery and invalidation, not native
judgment or measured API dollar savings.

The effective Decisions role behavior controls methodology. A complete `default` behavior replacement suppresses `reconciliation_prompt`; sourced commitments, direct user instructions, admission and dependent-work guards remain governing contracts. Resolve [bundled behavior](behavior.md) before each supporting assignment.
