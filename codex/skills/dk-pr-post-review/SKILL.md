---
name: dk-pr-post-review
description: Publish a completed authorized GitHub PR review as verified inline comments.
---

Resolve PACKAGE_ROOT from this installed skill location. This bundled PR skill can be invoked directly and needs no workflow lifecycle, preparation or invocation tokens. Read [shared inputs, results and publication](../../../docs/pr/contracts.md). Read the completed review result and the current PR head/diff. Verify each comment maps to a changed line and retains its source evidence. Post only when explicitly requested; a request to review locally does not imply publication. Use scripts/publish.py with kind post-review for the exact reviewed payload. Preserve its operation and reconcile by actor/head/body/comment identities after uncertainty. Do not add a general summary unless requested.
