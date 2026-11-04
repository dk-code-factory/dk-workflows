---
name: dk-pr-post-review
description: Publish a completed authorized GitHub PR review as verified inline comments.
---

Resolve PACKAGE_ROOT from this skill’s installation and follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read the completed review result and the current PR head/diff. Verify each comment maps to a changed line and retains its source evidence. Post only when explicitly requested; a request to review locally does not imply publication. Use scripts/publish.py with kind post-review for the exact reviewed payload. Preserve its operation and reconcile by actor/head/body/comment identities after uncertainty. Do not add a general summary unless requested.
