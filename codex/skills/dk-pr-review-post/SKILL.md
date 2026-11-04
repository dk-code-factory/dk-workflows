---
name: dk-pr-review-post
description: Publish a completed authorized GitHub PR review as verified inline comments.
---

Resolve PACKAGE_ROOT from this skill’s installation and follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read the original review results, verification dispositions and the current PR head/diff. Publish only completed, verified findings; an incomplete overall review may post these as COMMENT while disclosing remaining coverage blockers. Verify each comment maps to a changed line and retains its source evidence. Post only when explicitly requested or authorized by the invoked incoming-review workflow; a local analysis request does not imply publication. Use scripts/publish.py with kind post-review for the exact reviewed payload. Approval requires separate authorization and completed clean coverage; never approve the authenticated account's own PR. Preserve its operation and reconcile by actor/head/body/comment identities after uncertainty. Do not add a general summary unless requested, except the required coverage disclosure on a partial review.
