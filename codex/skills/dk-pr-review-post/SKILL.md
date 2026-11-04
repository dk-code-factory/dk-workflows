---
name: dk-pr-review-post
description: Publish verified inline PR findings when authorized, with any remaining coverage blockers.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read original review results, verification dispositions, and the current head and diff. Publish only completed, verified findings. Each comment must map to a changed line and preserve its evidence. An incomplete review may post confirmed findings as `COMMENT` if it states the remaining coverage blockers.

Post only when explicitly requested or authorized by the invoked incoming review workflow. Local analysis alone does not authorize publication. Use `scripts/publish.py` with kind `post-review` and the exact reviewed payload. Approval requires separate authorization and completed clean coverage. Never approve the authenticated account's own PR.

Save the operation path. After uncertainty, reconcile actor, head, body and comment identities. Add a general summary only when requested; always disclose coverage blockers for a partial review.
