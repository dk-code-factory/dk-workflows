# Status output details

Use this reference for compact result and progress references or `status --explain`. [Task state](task-state.md) defines routine commands and claim handling.

## Compact status

Compact status displays saved state; it does not assess readiness. Before assessing dispositions, exact user acceptances or recovery, read `status --run RUN_ID` and original results. Omit `--run` when evidence spans runs.

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
