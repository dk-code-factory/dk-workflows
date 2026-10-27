# Review publication diagnostics

Use the exact read-only reconciliation command returned by the helper, or
`dk.py --invocation INVOCATION_FILE post-review reconcile --operation FILE`.
Keep the original operation, pinned head, complete pre-submit review snapshot,
captured diff, approved payload hash, server response and verified receipt.

The helper first uses a retained review ID when available. Otherwise it compares
new reviews against the complete snapshot and requires one exact submitted match
for the authenticated author, reviewed head, event/state, review body and every
inline body and location, including ranges. Missing, extra, partial, pending or
ambiguous content blocks verification. A GraphQL errors array or missing response
fields is uncertainty even when the HTTP request succeeded.

Do not submit another review to resolve uncertainty, and do not submit, modify or
delete an unrelated pending draft. Inspect the retained IDs and mismatches; report
a partial or ambiguous result for explicit resolution. If the earlier request
created nothing, account for that attempt before preparing a new publication.

A changed head or base requires fresh review of the current diff. A historical
receipt cannot supply current readiness. Reconciliation preserves the receipt
separately from a subsequent readiness failure; follow the reported gate action.

Normal payload construction is described in [the payload reference](payload.md).
