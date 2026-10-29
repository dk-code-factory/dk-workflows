# Preparation data contract

Use with [the preparation procedure](preparation.md). Return JSON only. Evidence
references name indexed original sources, optionally with locators (`production/0#L42`).
All strings below are nonempty unless stated otherwise; evidence arrays are nonempty.

Fresh artifacts also retain architecture (catalog fingerprint, coordinator packet
reference/fingerprint and explicit exception references). The automatic
architecture/0 source and architecture-exceptions/* sources participate in the
conclusion graph and engineering identity. Every worker/nested handoff exports its
own recipient/path packet. Full-catalog architecture review is an independent
mandatory gate beside configured reviewers; complete applicable-rule coverage and
valid approvals are required for completion. A scope/Decisions change triggers
fresh architecture judgment, including during semantic cleanup reconciliation.

## Initial response

Return `{"analysis": ANALYSIS, "graph": NODES}`. Analysis contains:

| Field | Required content |
| --- | --- |
| `boundaries` | Nonempty conclusion arrays for `correctness`, `remediation_depth`, `performance`, `compatibility`, `complexity`. Cover supported cases/exclusions, allowed depth, relevant workloads/limits, supported interfaces/environments and justified design costs. |
| `placement` | Conclusions for `production`, `tests`, `reuse`, `entrypoints`, `dependencies`, each with a nonempty `locations` string array. |
| `test_strategy` | Nonempty array: string fields `behavior`, `existing`, `missing`, `level`, `location`, `command`, `rationale`, `limitation`, plus `evidence`. Use `none` when applicable. Level: `inspection`, `unit`, `integration`, `system`, `performance`. |
| `questions` | Open prior questions and resolved contradictions, using the shape below. Empty is valid. |
| `reviewer_addenda` | Each configured reviewer name mapped to tailored instructions. |
| `worker_instructions` | Boundaries, selected remedy, principal code/test locations, helpers, scenarios, commands and stopping criteria. |
| `completion_criteria` | Required behavior, placement, sufficient validation, compatibility and cumulative complexity. |
| `sync_required`, `sync_reason` | Boolean and explanation; empty reason allowed only when false. |

A conclusion has `text`, `agreement`, boolean `material`, `rationale`, `evidence`.
Agreement: `established` (explicitly agreed), `interpretation` (grounded routine choice),
or `proposal` (unsettled). Material proposals and open material questions produce
`needs_decision`; required unpublished synchronization produces `sync_pending`.

Questions require `id`, `status` (`open` or `resolved`), boolean `material`,
`statements`, `affected_work`, `recommendation`, `proposed_changes`, `evidence`,
and at least two `options`, each with `choice`, `benefits`, `costs`. A resolved
question also needs `resolution` citing the exact answer or applicable precedence/
supersession and reasoning. Preserve open IDs; graph updates cannot settle choices.

`NODES` has exactly one node per analysis conclusion:

```json
{"location":"/boundaries/correctness/0","depends_on":[],
 "paths":[{"path":"src","kind":"subtree"}],"sources":["production/0"],"decisions":[]}
```

Locations are JSON pointers: each boundary, placement, test, question, reviewer
addendum, worker/completion string, and combined `/sync_required` value
`{"sync_required":false,"sync_reason":""}`. Escape reviewer names as JSON pointers.
Initial edges use locations; runtime assigns stable `c-…` IDs and stores nodes by ID.
Each node needs supporting paths, sources or dependencies. Sources are input keys;
Decisions are existing authoritative IDs. Paths are normalized repository-relative
whole `file` or `subtree` watches; `.` permits a root subtree. Cover absent-file
assumptions; no globs, symbol/line watches, call graphs or similarity matching.
Runtime adds governing source watches and reviewer/worker/completion dependencies
on governing conclusions; AI supplies other relevant edges.

## Reassessment response

Return exactly `dispositions`, `additions`, `impacts` arrays:

```json
{"dispositions":[{"id":"c-ID","action":"retain","reason":"Inspected evidence"}],
 "additions":[],"impacts":[{"change":"path:new.py","affected":[],"rationale":"Inspected impact"}]}
```

- Each affected/additionally affected existing node and every dependent needs one
  disposition: `id`, `action`, `reason`. `retain` preserves its value exactly;
  `replace` requires `value`; `remove` retires the ID. Only replace supplies a value.
- Optional `watches` replaces all fields: `depends_on`, `paths`, `sources`, `decisions`.
  Existing edges use stable IDs. Dependents must explicitly rewire removed targets.
- Additions require `location`, `value`, `watches`, `reason`. List locations append
  with `/boundaries/DIMENSION/-`, `/test_strategy/-`, `/questions/-`; singleton slots
  must be absent. Added governing conclusions require guidance dispositions.
- New or updated nodes may depend on `addition:N`, the response's zero-based addition
  index in canonical decimal without leading zeros. Forward edges and cycles work;
  runtime preallocates stable IDs and resolves references before validation. Stored
  IDs survive replacement; retired IDs cannot return. Runtime rebuilds list indexes.
- Each unmapped change requires `change`, `affected` existing IDs (possibly empty),
  `rationale`. Repeated `affected` IDs are coalesced. Missing mappings never prove
  irrelevance. Invalid or dangling references, repeated `impacts[].change` or
  `dispositions[].id` entries, ID collisions, missing dispositions or lost open
  questions block.

If impact cannot be bounded, return only
`{"full_reassessment":true,"reason":"Why impact cannot be bounded"}`.
The one full fallback uses the same disposition protocol for all existing nodes;
a full phase cannot escalate again. Explanations belong in the refresh report.

## Evidence normalization

Repository sources match relative paths; known GitHub records match stable IDs.
Opaque inputs keep exact byte hashes and path identities. A `complete` field in
application data declares no fetch completeness. Supported collectors enforce theirs.
Unknown fields, source additions/removals, authorship, text, nested order and precedence
remain significant. Exact supported exceptions:

- Context transport paths, head and local-checkout status are operational; metadata
  ignores `headRefOid`, Decisions ignore only record `head_sha` (`data.head_sha` in
  an envelope), patches derive from Git. All other Decisions envelope fields govern.
  Manifest locations are transport, with each source compared separately. Reviewer
  descriptors compare structurally; verified prior preparation supplies lineage.
- Complete inline feedback uses integer `schemaVersion:1`, `threads`/`items` and
  validated `threadsSha256`/`itemsSha256`. Conversation has no version marker,
  `reviews`/`comments`/`items` and validated `sha256`. Only known collection order,
  JSON formatting and supported thread `isResolved` are operational. Unknown fields
  retain content/order, including other schemas' collection/hash names. All original
  text records remain required; new, edited, removed, bot or self-authored text governs.
- Integer `preparation_history_version:1` requires `governing` and `operational`
  objects. Integer `evidence_version:1` requires `governing`, permits `operational`
  and supports stable `source_id` across relocation. Other marker types stay opaque.
  Keep choices, questions, constraints, unanswered requests and supersession under
  `governing`; unknown fields govern. Excluded operational fields are nonnegative
  integer `cycles`/`cycle_retries`/`repairs`/`reruns`, string `head_sha`/`run_id`/
  `checked_at`, known CI `status`, and `publication_receipts` arrays with string
  `url`/`head_sha`/`sha256`/`outcome`/`timestamp`/`artifact`/`kind` fields only.
  Unknown receipt fields keep the collection governing. Known statuses: `pending`,
  `queued`, `in_progress`, `success`, `failure`, `neutral`, `cancelled`, `skipped`,
  `timed_out`. Operational state never changes Decisions or review-session identity.

## Stored artifact and verification

Immutable `preparation_version:3` artifacts retain `analysis`, graph/retired IDs,
input fingerprints, reviewer descriptors, readiness, PR/base/head/body/Decisions
identity, parent path/ID/hash and migration provenance. Snapshots are fingerprinted
and read-only outside the repo. Historical validation checks retained integrity and
graph structure; current gates also check live inputs and current mandatory rules.
Migration preserves original v1 storage and grants no coverage or cycle budget.

Artifact and API `refresh` report mode, changed paths/sources, unmapped changes,
binding/identity changes, affected/reused conclusions, explanations and actual
`model_call_count`, including corrections. Dependency metadata and refresh explanations
remain outside binding analysis. Tree-equivalent review reuse preserves original
provenance only when existing scope/rules/Decisions/instruction/base checks pass.

Completion verification has `verification_version:2`, originating `preparation_id`,
runtime-computed `engineering_id`,
`pr_url`, `head_sha`, and `checks` for `behavior`, `placement`, `test_sufficiency`,
`compatibility`, `complexity`. Each check contains boolean `satisfied`, `rationale`,
and nonempty `evidence`: `{"path":"ABSOLUTE_PATH","sha256":"FILE_HASH"}`.
Inspection reports, code and test results qualify; missing, false or stale evidence
blocks completion. Both stages use the same current preparation, verification and
mutable run record; an immutable history snapshot cannot replace live choices.


The complete artifact fingerprint is `preparation_id`; its runtime-computed
`engineering_id` is the compatibility key. Store both. The latter excludes supported
operational metadata, storage paths, reports and verified lineage, retaining unknown
fields as governing. Reviewer order is explicit in `reviewer_order`. Sources retain
`path`, `sha256`, immutable `snapshot`, and `mode` (`capture` or `live`). See the
preparation procedure for capture defaults and manifest `source_modes` overrides.
Never accept a supplied engineering ID without recomputation and current evidence
and binding validation. Completion verification v2 remains reusable across an
operational refresh with identical engineering identity and head; its preparation
reference remains unchanged. Use workflow-owned evidence captures for verification
reports intended to survive original export removal. Legacy verification v1 belongs
to the retained legacy runtime. Fresh artifacts never rewrite historical checkpoints.

`recovery_manifest` and `recovery_context` retain verified path/hash references to runnable private recovery inputs. Captured sources record `origin_identity`; recovery manifests carry `source_origins` to preserve identity while relocating captures. These declared storage fields do not govern engineering compatibility.
