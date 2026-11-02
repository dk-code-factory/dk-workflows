# Operational refresh and cleanup evidence

Fresh runs use preparation v4, run contract v3, session v3, review-result v4,
role-checkpoint v2 and reconciliation v3. Completion verification remains v2.
Historical artifacts remain readable for reporting; active older runs use their
retained runtime. Never migrate an active run or reset its counters.

Use `run-state refresh-preparation --previous-preparation PREPARATION.json
--context CURRENT_CONTEXT.json --decisions-file VERIFIED_DECISIONS.json
--inline-file COMPLETE_THREADS.json --conversation-file COMPLETE_CONVERSATION.json`.
The helper captures typed run history, builds a clean isolated checkout at the
current head, reconstructs the retained manifest, invokes incremental preparation
once, and attaches with the selected expected parent. It preserves categories,
source IDs, capture origins, relative repository paths and reviewer order. Only
verified mirrored Decisions copies are refreshed automatically. Independent choices
remain independent. Optional `--replacements-file FILE` maps existing source IDs
to replacement paths. Additions, removals and role changes require the ordinary
explicit manifest workflow. Read readiness, both IDs, changed governing inputs,
model_call_count and coverage_requirements from the receipt.

Record cleanup handoff, prepared guard/recovery state and completed assessment
with `run-state operational --field cleanup --file FILE`. The exact input shape is:

```json
{
  "cleanup_version": 1,
  "kind": "handoff",
  "run_id": "ACTIVE_RUN_ID",
  "head_sha": "FULL_HEAD_SHA",
  "worker": "CANONICAL_CLEANUP_WORKER",
  "preparation_id": "PREPARATION_SHA256",
  "engineering_id": "ENGINEERING_SHA256",
  "artifacts": {
    "preparation": {"path": "/ABSOLUTE/PRIVATE/preparation.json", "sha256": "FILE_SHA256"}
  }
}
```

`kind` is handoff, prepared or assessment. Prepared additionally requires guard;
assessment requires guard and assessment. Optional recovery uses the same absolute
path/file-sha256 reference shape. No other fields are accepted. Use the same worker
through cleanup. The helper verifies run/head/worker/preparation bindings and
artifact hashes, validates guard/assessment identities independently, captures
mutable references and retains immutable machine evidence. Returned artifact and
sha256 fields identify the normalized retained record. Raw supplied bytes are also
retained privately. Never put engineering decisions or arbitrary prose in these
records. Generic events, user decisions and unknown evidence remain governing;
historical events are never reclassified or rewritten. Operational classification
does not establish cleanup success: publication and completion revalidate the
actual guard, lease, target tip, candidate, assessment and live evidence.

Before candidate publication, refresh complete feedback, verified Decisions and
preparation with the helper, then run precheck-cleanup and the review gate. For
unchanged computed engineering identity, reconciliation v3 emits a fingerprinted
`proof_kind: exact_identity` receipt after deterministic review, disposition,
architecture and guard validation. Capture/provenance relocation alone does not
require semantic comparison. Real differences use semantic_judgment directly
against the original reviewed preparation, preserving disagreement/correction
history. Never substitute a precheck for final coverage.

After a verified no-op assessment, refresh operational evidence once. When the
current coverage gate passes, retain the existing review and architecture coverage
with zero subsequent preparation/comparison/architecture model calls. A rewritten
head still needs guarded publication, new-head CI and final-head reconciliation.
Architecture reuse requires validated tree, baseline, scope, catalog rules,
approvals and governing inputs; the receipt retains the coverage attestation.
The separate completion agent consumes the existing assessment and verification;
it runs fresh live gates without repeating preparation or series assessment.

`run-state timings` reports private diagnostic spans for preparation, independent
reviewer roles, architecture, comparisons, CI and completion. Elapsed/active time
uses the union of intervals; overlapping activity, model dispatches, retries,
reuse, unfinished spans and unmeasured gaps are reported separately. Inclusive
provider token usage remains unknown when unavailable. Timing never changes a gate
or establishes real wall-time improvement without comparable actual runs.
