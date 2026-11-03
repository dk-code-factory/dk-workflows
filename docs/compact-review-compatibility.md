# Compact reviewer compatibility evidence

Issue [#82](https://github.com/dk-code-factory/dk-workflows/issues/82) retains
**native compatibility trials as pending**. This change supplies an offline
diagnostic for privately retained paired captures. Synthetic tests exercise the
analyzer and transport contracts; they do not establish provider compatibility,
guidance comprehension, general finding recall, or live acceptance.

The diagnostic never invokes a provider, changes reviewer settings, publishes
findings, or approves adoption. Every report keeps `authority: diagnostic-only`,
`adoption_authorized: false`, `publication_authorized: false`, and
`live_acceptance: pending`, including a report with `status: observed` and native
observation provenance. Native provenance is the capture author's declaration,
not cryptographic attestation of a provider service.

## Reproduce offline

Run from the repository root, with Python 3 and pytest installed for tests:

```sh
PYTHONPATH=runtime DK_HOST=codex python3 -m pytest -q tests/test_compact_compatibility.py tests/test_review_execution.py
PYTHONPATH=runtime DK_HOST=codex python3 -m pytest -q runtime/dk-pr-review/tests/test_compact_delivery.py
python3 scripts/compare_review_handoffs.py "$PRIVATE_CAPTURE_DIR/manifest.json" --report "$PRIVATE_CAPTURE_DIR/comparison-001.json"
```

Set `PRIVATE_CAPTURE_DIR` to an absolute private directory outside the checkout.
The analyzer reads existing captures only. Its new report is created with mode
0600 and never overwrites a prior report. Exit codes are 0 for observed, 1 for
incomplete, and 2 for an invalid manifest or report destination. Pair failures
retain their reasons and any matrix entries already inspected. Missing, changed,
or inaccessible required originals produce incomplete evidence, never a clean
compatibility conclusion. Unknown delivery remains explicitly incomplete even
when all independent passes completed.

## Private manifest version 1

Keep manifests, handoffs, descriptors, original records, prompt captures, raw
native attempts, results and reports outside Git and the plugin distribution.
References below use `{ "path": "ABSOLUTE_PRIVATE_PATH", "sha256": "HEX_SHA256" }`
and bind the **exact file bytes**. Architecture references use the existing
`{ "path": "ABSOLUTE_PRIVATE_PATH", "fingerprint": "PACKET_FINGERPRINT" }` contract.
The original records and installed procedure sources must remain readable.
Installed procedure texts may reside in a local plugin checkout; generated
captures and reports must remain outside Git checkouts, including separate
reviewed repositories.
Preparation sources select `snapshot` when present, otherwise `path`.
The retained source index must include the standard preparation records and
every manifest evidence entry, including explicit absence records. Category
indexes and original paths must match the retained manifest. Installed reviewer
procedure sources remain checked per pair rather than compared across providers.
Delivered Decisions must match the original preparation Decisions source and
its identity hash. Architecture-bearing captures also require the pinned Git
checkout: coordinator paths must match both revisions' changed paths before
recipient packet coverage can be considered complete.

The manifest object has these fields:

| Field | Value |
| --- | --- |
| `version` | Integer `1` |
| `provenance` | `synthetic` or `native-observation`; never relabel generated events as native |
| `pairs` | Exactly three ordered pair objects: Codex AD, Claude Toolkit, Claude AD |

Each pair has `id`, `route`, `full`, and `compact`. IDs in order are
`codex-ad`, `claude-toolkit`, `claude-ad`. Route is `native` or
`run-claude-skill`; Codex AD uses `native`, and both Claude routes must match.
The route records the capture plan; retain the actual invocation command in the
private capture materials. Route labels alone do not prove a native invocation.
Each `full` or `compact` run object has exactly the following required fields,
plus the optional `deliveries` list:

| Field | Value |
| --- | --- |
| `binding` | Actual reviewer binding, including `skill`, explicit `host`, selected `handoff_contract`, and any profile or nested-role declarations |
| `handoff` | Hashed reference to the actual delivered JSON handoff |
| `descriptor` | Hashed reference to the prepared provider descriptor with exact installed execution identity and source hashes |
| `result` | Hashed reference to the original retained findings JSON, before any filtering |
| `execution` | Existing mandatory specialist execution evidence reference, including its raw attempt references and assessment |
| `deliveries` | Recipient-specific delivery records described below; omission means unknown delivery |

For the three pairs, bindings must respectively select
`code-review-ad:code-review-ad` on `codex`, `pr-review-toolkit:review-pr` on
`claude`, and `code-review-ad:code-review-ad` on `claude`. The full run selects
`git-diff-v1`; the compact run selects `git-diff-v2`. Do not reuse one invocation
as proof for multiple runs. A minimal skeleton is:

```json
{
  "version": 1,
  "provenance": "native-observation",
  "pairs": [
    {"id": "codex-ad", "route": "native", "full": {}, "compact": {}},
    {"id": "claude-toolkit", "route": "run-claude-skill", "full": {}, "compact": {}},
    {"id": "claude-ad", "route": "run-claude-skill", "full": {}, "compact": {}}
  ]
}
```

Replace each empty run object with the fields above. This skeleton deliberately
contains no trial results. The matrix retains provider execution version, root,
procedure, bundle identity, configured argument vector where supplied, source
hashes, contract, preparation identity, revisions, requested scope, invocation,
response slots, required/completed roles and delivery coverage. Unknown installed
versions or procedure texts fail through the existing execution verifier.

## Delivery observations

Role completion and receipt of guidance are separate questions. Execution is
verified by `shared.review_execution.verify`, including native host streams,
required independent passes, conditional applicability and original result
binding. No replacement completion mechanism is introduced here.

Every observed response slot, including bounded output correction slot 1, needs
delivery records for `reviewer` and every required/completed nested role. A record
has integer `attempt` (0 or 1), exact `recipient`, `kind`, a hashed `source`, and a
JSON `pointer` into that source. Pointer uses JSON Pointer escaping (`~0`, `~1`).
When `event` is supplied, it is a one-based line in the source attempt's JSONL
`stdout`; its source must be that run's retained raw execution attempt for the
same response slot. The pointer then selects a value in that event.

Two kinds are supported:

- `native-prompt` cites the exact direct child launch prompt in a raw native
  attempt event. Codex pointers select `/item/prompt`; Claude pointers select
  `/message/content/N/input/prompt` for the relevant Task/Agent block. The prompt
  must contain the recipient's own `DK_REVIEW_PASS=ROLE` line and exactly one
  `DK_REVIEW_DELIVERY_JSON=JSON_OBJECT` line. This diagnostic envelope is optional
  trial instrumentation, not a change to either handoff version or live gates.
  Codex launches require a completed spawn and a distinct child identity. Claude
  launches require a matching successful tool response. A failed launch retained
  before transport repair cannot establish delivery. Native event observations
  must cite a successful transport attempt; a successful launch in a discarded
  failed attempt cannot establish delivery to reviewers in the retry.
- `source-bound-observation` also supplies nonempty `excerpt` and `received`.
  The excerpt must occur in the string selected by the source pointer. `received`
  is the observer's explicit normalized delivery observation. Retain the native
  launch/read records supporting it; a generic completion assertion is inadequate
  evidence for the human interpreting the report. These observations remain
  labeled as observations, separately from parsed native prompt envelopes. The
  analyzer checks their source availability and content binding, not whether an
  observer's interpretation of prose is true.

`received` (or the native envelope's JSON object) must equal the exact object
returned by `shared.compact_compatibility.delivery_payload(handoff, recipient)`:
JSON scalar types remain distinct, including booleans and numbers.

```python
{
    "scope": handoff["scope"],
    "base_sha": handoff["base_sha"],
    "head_sha": handoff["head_sha"],
    "rules": handoff["rules"],
    "pr_url": handoff["pr_url"],
    "reviewer_addendum": handoff["reviewer_addendum"],
    "decision_instructions": handoff["decision_instructions"],
    "analysis": handoff["preparation"]["analysis"],
    "decisions": active_decisions(handoff["decisions"]),
    # When present: full_preparation, full_decisions, and this recipient's packet.
}
```

The full analysis preserves all shared guidance and the handoff's addenda; the
compact analysis preserves all shared guidance and only its assigned addendum.
`active_decisions` preserves the entire envelope except the three duplicated
legacy arrays and superseded unified records. Active text, conditions, rationale,
scope, sources and publication state remain exact. Nested recipients receive
their own architecture packet. Original full-record paths and hashes must survive
compact delivery and correction unchanged. Completion records alone never fill
in missing delivery observations.

## Paired comparison and pending native capture plan

For each pair, retain freshly prepared full and compact runs against the same
pinned PR URL, base/head, patch bytes, requested scope, rules, Decisions, shared
analysis and governing source hashes. Contract changes require fresh preparation;
never edit preparation descriptors to reuse an incompatible checkpoint. Manifest,
context and reviewer transport captures may change during this refresh. Installed
method execution and source hashes are compared independently. Unknown preparation
fields remain subject to the existing compact projection; originals stay hashed.
Architecture packets are checked against their retained catalog and preparation
for recipient, intended paths, rules, revisions and governing scope. Packet
fingerprints and governing-source capture locations can differ across freshly
prepared runs; the comparison retains their governing content and source
fingerprints. Retain the original catalog in private architecture storage.
Retained governing originals must remain readable and match their semantic
fingerprints. The coordinator scope must match the captured PR, metadata and
Decisions, even when all recipient packets agree with one another.

When separately authorized and bounded, capture Codex AD while keeping it in the
cohort, then invoke Claude Toolkit and Claude AD **separately**, using
`run-claude-skill` for both when that route is selected. Pass the identical pinned
requested scope to both Claude invocations; do not let a Toolkit summary narrow
the AD scope. Retain the actual command arguments, installed procedure/version,
all prompts, native stream events, corrections and results privately. The capture
plan must name its paid-call and output budgets before any live invocation.

Exercise initial delivery and correction, an explicit request for omitted
metadata/history, active Decisions with exact conditions, recipient-specific
architecture packets, and negative controls for unavailable originals and changed
contracts/methods. The offline analyzer reads and hashes originals and reports
their availability. Native proof that a provider fetched and understood omitted
metadata/history still requires the retained trial observations; analyzer success
does not supply that behavioral evidence. Synthetic fixtures cover missing,
changed and inaccessible originals, correction delivery gaps, scope drift,
conditional roles, architecture recipient/revision errors and finding differences.

Findings are compared by exact retained JSON content, producing `shared`,
`full_only`, and `compact_only` sets. Different wording remains different output.
Counts from a small sample do not establish general recall or equal review quality.
Report differences for investigation; do not automatically discard either result.

Only an explicitly selected binding may opt into v2 after a separate assessment
of the relevant native evidence. Unsupported or unverified providers retain v1.
No diagnostic changes independent passes, fresh cycle agents, five-cycle limits,
correction budgets, architecture checks, checkpoint rules or publication guards.
The live acceptance portion of #82 stays open until separately authorized native
trials and their evidence have been assessed.
