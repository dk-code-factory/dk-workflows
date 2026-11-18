# Status output details

For bounded current selected obligations and original recovery references, use
the read-only [next-action interface](next-actions.md). Unlike retained-entry
discovery, it derives the current task/finalizer selection from live configuration.
It never establishes CI, authorization or publication readiness.

Routine coordination uses `resume`, `result` and `progress` under [task state](task-state.md). Resume recaptures retained definitions, replaces finding bodies with `finding_count` and includes saved Git pins for comparison with the current PR. It is read-only and does not establish completeness of the current selected task set. Missing-source/removed-occurrence tasks report `refresh_required`; legacy entries report `definition_required` under `unmanaged`. Supply those current definitions once through prepare to attach replay information without resetting results or budgets. `--key` restricts saved tasks, while unfinished claims remain PR-wide.

`result --key KEY --section findings|normalized|original` validates the saved envelope and reads the requested section. It exposes historical evidence, not a current-coverage verdict. `progress --run RUN_ID --after OFFSET --limit N` returns original progress records, a total and the next offset. Read every page relevant to dispositions, acceptances and recovery. None of these views persists a rendered summary or a second index.

The remaining sections describe compatible low-level status output and `status --explain`.

## Compact status

Compact status displays saved state; it does not assess readiness. Before assessing dispositions, acceptances or recovery, read necessary original evidence and relevant progress pages. Low-level integrations can still use full `status --run RUN_ID`. Include other runs when evidence spans runs.

`progress_ref` is a JSON Pointer to the same progress array in the manifest. In run names, decode `~1` as `/` and `~0` as `~`. Each validated `result` path points to an immutable JSON envelope:

- `normalized` contains findings and skill-owned fields.
- `original` contains the exact original output in base64. Decode with `base64.b64decode(envelope['original'], validate=True)`.

Older envelopes remain in the manifest's sibling `results/` directory. Attempt history in `entries` references their filenames. A missing or damaged current envelope produces a null result path and cannot be reused in either view.

Both status views use the same lock and integrity checks. Neither changes manifests, claims or counters. Compact size does not grow with progress bodies, but still includes requested tasks, budget maps, runs and all unfinished claims. It does not reduce manifest I/O or replace current input, CI or publication checks.

## Explained status

Explained and ordinary status use the same capture and reuse decision. `blockers` reports changed inputs, explicit invalidation, noncacheable tasks or results, unfinished claims, absent results, missing artifacts, damaged results, and failed or incomplete results. Several blockers may apply.

`previous_capture` identifies the last captured components. These may belong to an unfinished attempt rather than the saved result. Compare `previous_capture_fingerprint`, `current_fingerprint` and `result_fingerprint` to distinguish them. `result_fingerprint_mismatch` also explains a current capture with an older result. The ordinary `result` field still points to validated original evidence.

A task without prior capture reports `no_previous_capture` and additions; it does not imply a change from an earlier review. Missing governing resources still block capture before claim or budget changes.

Diagnostics do not change fingerprints or coverage, allocate cycles, recover claims, or authorize reuse, publication or completion. Without `--explain`, detailed comparisons are omitted and compact output keeps its existing size limit.

## Structured recovery fields

Task and configuration helpers add `diagnostic` without replacing existing
`status`, `action`, `reason`, `detail` or exit behavior. Each diagnostic contains
`code`, a useful `message`, a typed `action`, `dispatched` and `outcome`.
Configuration preflight adds `diagnostics` for independently unsupported calls.
The legacy `pr_config.py` error text remains on stderr and its additive JSON
response is on stdout; failures still exit with status 2.

| Code | Supported recovery |
| --- | --- |
| `stale_inputs` | Rebuild affected assignments under current inputs, retaining original evidence and allowances. |
| `evidence_missing`, `evidence_damaged`, `evidence_unavailable` | Restore the referenced original artifact or access before reuse. |
| `definition_required` | Supply the original task's current explicit definition. |
| `execution_contract_missing` | Resolve the installed contract before native execution. |
| `execution_capability_missing` | Use a supported host and native tools; leave required work incomplete until available. |
| `execution_evidence_missing`, `execution_evidence_invalid` | Establish required original native observations; a complete report alone is insufficient. |
| `active_claim` | Reconcile the referenced original claim and domain effects before explicit recovery. |
| `allowance_exhausted` | Obtain an applicable explicit human decision; never reset counters or infer permission from a resume. |
| `configuration_invalid`, `admission_required` | Correct configuration or finish the original configuration admission. |
| `publication_uncertain` | Run the supplied read-only reconciliation for the original operation. |
| `configuration_write_uncertain` | Inspect the original configuration request through its read-only resume command. |
| `unclassified_failure` | Inspect original evidence; provenance is unknown and no specific cause is inferred. |

Action `kind` is `command`, `agent_task` or `human_input`, retaining the shared
Diagnostic vocabulary. Commands supply an argument array. `agent_task` means work
in the already-running native session. Artifacts are targeted original paths,
manifest paths with JSON Pointer fragments, or labeled identities such as a run or
request ID. Recovery never starts another model session.

`dispatched` describes the affected operation, not every earlier external action.
It is true for a retained pending publication, false for a proven local condition,
and null when dispatch cannot be established. `outcome:unknown` preserves an
unresolved outcome; the legacy outer status may still be `blocked` or `applying`.
A local preparation failure cannot establish non-delivery of an earlier write.
For an interrupted configuration request, `admission_diagnostic` can explain
stale inputs while the primary diagnostic retains original write uncertainty.
Neither field authorizes applying the request or discarding its intents/receipts.

Classification uses exception types marked at authoritative validation sites or
existing structured state, never explanation wording. Untyped legacy errors keep
unknown provenance. Inspection derives these fields in memory and leaves original
manifests, results, claims, counters and journals unchanged. Inspect again after
relevant input or evidence changes; diagnostics never establish current review,
CI, permission or publication readiness.
