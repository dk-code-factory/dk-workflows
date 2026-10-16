# API contract

All commands return one JSON object:

```json
{
  "schema_version": 2,
  "command": "scan",
  "status": "ok",
  "data": {},
  "publication": {"updated": false, "pending": [], "reason": null},
  "error": null
}
```

`status` is `ok`, `pending`, or `error`. Pending publication is a completed command with exit code 0, so callers must inspect `status`. Errors have exit code 1, `data: null`, and `error: {"code": "...", "message": "..."}`; `command` is null when handling an error. Errors are JSON on stdout. CLI help remains plain text.

`publication` always has the same shape. `pending` contains unpublished decision objects with `text` and `references`; `reason` explains a conflict or failure. Nonpublishing commands return an empty publication result. Inspect both pending items and the reason, including when a replacement has no pending additions.

## Decision records

`scan`, `list`, `context`, `publish`, and `suggest` return `data.decisions`. `add` and `replace` return the selected record as `data.decision`. Every record has:

- `id`: stable hash of the exact normalized choice text, independent of source links.
- `text`: choice text, without the generated `**Choice:**` prefix.
- `source_type`: `description`, `discussion`, `code`, `user`, or `local`.
- `references`: exact source URLs; empty for a directly authorized user choice or local handoff.
- `source`: user instruction, local evidence, description snapshot hash, or null when references supply the source.
- `status`: `active`, `candidate`, or `superseded`.
- `publication_status`: `published` or `unpublished`, based on the fetched/cached description.
- `superseded_by`: replacement ID for historical entries, otherwise null.

Existing Decisions entries are read as Markdown blocks, including multiline bullets and standalone paragraphs. Their text is preserved rather than rewritten by analysis. Entries from discussions that have identical normalized text share an ID with the corresponding description entry. Different wording remains distinct; reconcile a changed agreement explicitly with `replace`.

`context` includes `pr_url`, `head_sha`, `body_hash`, and `verified` in `data`, plus the unified records. It retains `live_decisions`, `discussion_decisions`, and `local_decisions` as compatibility evidence. Use active unified records for current constraints; the raw live section can contain superseded history. `list` describes a cached snapshot and makes no freshness claim. `suggest` returns candidates only; use `add` or `replace` to select one before publication.

## Refresh and publication

`scan`, `context`, `suggest`, `add`, and `replace` refresh the PR and discussions and may run model analysis. They may write the private cache but do not edit GitHub. `list` is entirely offline. `publish` loads the cache and checks live GitHub state without rerunning analysis. `scan --publish` combines refresh and publication when both are requested.

Code suggestions are bound to the head SHA. Description suggestions are bound to the complete PR body hash. Selecting either requires the exact cached `{text, references}` object and a matching current snapshot. Discussion choices require exact current comment or review URLs. A PR URL alone is accepted only for an exact current description suggestion.

`replace` requires an active ID from `list` or `context`. It stages the new choice and marks the old record superseded locally. Publication verifies that the exact original Markdown entry remains, then moves it into a collapsed history block within Decisions and adds the new choice. History includes the original record and replacement ID; unrelated body text is preserved. Repeated publication is idempotent. Replaced choices that were never published are also included in history, so replacement chains survive cache loss. If the original entry changes, reconcile the replacement before publishing; do not overwrite the external edit.

The live body/head comparison is best effort, not an atomic GitHub compare-and-swap. A change between the final read and PATCH cannot be excluded by this API.

## Migration

Previously `scan` and `add` published automatically. Now use `scan --publish`, or run `publish` after `scan`, `add`, or `replace`. Previously `list` refreshed; now run `scan` when fresh data is needed. Existing cache files remain readable.

All former top-level command results now live under `data`; publication results always live under `publication`. Consumers should inspect `schema_version` and `status` before reading `data`. The PR review runner accepts the new context envelope and legacy context files.


## Preparation and combined publication

`prepare --input-manifest FILE --output-dir PRIVATE_DIR` delegates to
`scripts/preparation.py` without a discussion scan or any publication. It preserves
this API v2 envelope; `data` contains `artifact`, `preparation_id` and `readiness`.
The artifact has its own `preparation_version: 1`; read
[the preparation contract](preparation.md) for complete evidence, semantic analysis,
question retention, placement, proportionate tests, and downstream gates.
`verified: true` retains its original provenance/freshness meaning.

`publish --description-file FILE --description-review-state TOKEN` composes reviewed
scope prose with active Decisions and replacement history into one body edit. The
completed token must be for `dk-pr-description`. Live title/body/head must match the
cached source snapshot. The publisher records intent before writing and verifies
live state afterward; unrelated title changes are never overwritten.

Publication history and pending intent survive scan/resume. `publish --reconcile`
only observes: it confirms an already-applied body without writing again, keeping any newer requested Decisions or reviewed description pending until an explicit publication, or records
verified non-application before an explicit refresh/retry. Conflicts and uncertain
results retain `status: pending` with `publication.reason`, even if the pending choice
array is empty. Dependent iteration stays `sync_pending` until live publication,
context and preparation agree. Existing scan/list/context/add/replace/suggest
commands and unified decision records retain their contracts.
