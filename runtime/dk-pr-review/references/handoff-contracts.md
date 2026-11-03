# Reviewer handoff contracts

Review bindings default to `git-diff-v1`. It supplies the complete preparation,
complete verified Decisions envelope and selected reviewer addendum. Existing
providers keep this format, including mixed lists with compact providers.

To select compact delivery for a provider that supports it, set an explicit binding
through `dk-config reviewers set --bindings-file FILE`:

```json
[
  {"skill":"example:review","host":"codex","handoff_contract":"git-diff-v2"},
  {"command":["review-tool","--handoff","{handoff}"],"handoff_contract":"git-diff-v2"}
]
```

Selecting v2 declares that the provider supports the compact input. Never infer
support from an existing binding. Unknown versions fail rather than silently
falling back. Drafting uses its own contract and rejects this reviewer-only field.
Use the [offline compatibility diagnostic](../../../docs/compact-review-compatibility.md)
to compare privately retained full/compact captures. Synthetic checks and diagnostic
reports do not authorize adoption; native compatibility trials remain pending.
The provider identity stays stable. The selected contract participates in its
descriptor, preparation identity and resume checks. Changing it requires refreshed
preparation; pinned execution cannot hide a changed selection.

## Compact input: git-diff-v2

Fresh preparation v4 and unified Decisions are required. Retain the existing
`checkout`, `base_sha`, `head_sha`, `patch`, `scope`, `rules`, `pr_url`,
`decision_instructions`, `reviewer_addendum`, and role-specific `architecture_packet`
when supplied. Input changes are:

- `contract` is `git-diff-v2`; `preparation_view_version` is `1`.
- `preparation` is a guidance view. Preserve complete shared analysis: boundaries,
  placement, tests, questions, worker instructions, completion criteria and
  synchronization requirements. Its `analysis.reviewer_addenda` contains only this
  reviewer's entry. All unfamiliar preparation/analysis fields remain visible.
- Known storage/recovery fields, runtime conclusion graphs and retired IDs are
  omitted. Known provider descriptors are available through the original and its
  indexed evidence rather than repeated inline. Descriptors with unfamiliar fields
  or source/execution extensions remain inline. Governing input fingerprints and
  source evidence remain available. Source `path` selects its retained snapshot
  when present; extended source records preserve their original shape.
- `decisions` contains all active unified records, preserving exact text, rationale,
  scope, conditions, sources and publication state. Duplicated legacy arrays and
  superseded records are available in the complete original. History never becomes
  a current constraint.
- `full_preparation` and `full_decisions` each contain an absolute `path` and
  SHA-256 `sha256` for the complete original record in private workflow storage.
  Read originals when history or omitted metadata is needed; verify the hash first.
  Keep references with shared guidance in nested handoffs and correction retries.
  Do not read all bookkeeping merely because its reference is present.

This view is not a preparation artifact and cannot pass a work or completion gate.
The runtime validates the full original and current source evidence independently,
including contract selection before reusable checkpoints. It verifies full-record
references before launch and after execution. Missing or changed referenced records
block success. The same handoff and original references survive the bounded output
correction. A provider must report incomplete when required evidence is unavailable.

## Results

Both input versions retain the existing findings JSON, location validator, one
correction attempt, finding thresholds, role restrictions, architecture delivery
and interruption behavior. Compact input grants no extra publication authority.

Reviewer bindings optionally declare `nested_review_roles` as unique recipient
identities. Bundled defaults cover AD Toolkit/Standards/Spec/Performance and
Matpocock Standards/Spec. Preparation fingerprints the declarations. Before any
dispatch the coordinator materializes recipient-specific packets and places them
in `nested_architecture_packets` in each handoff. Nested roles read their own
packet; undeclared recipients or expanded paths require fresh coordinator delivery.
Never substitute the parent packet or create private state inside a sandbox.
Architecture executes concurrently and checkpoints independently; configured result
fields and output order remain unchanged.

Known methods and explicit nested-role obligations require retained native launch
and completion evidence before successful coverage or checkpoint reuse. See
[execution evidence](execution-evidence.md) for supported host streams, independent
conditional applicability assessments, private provenance and failure states.
