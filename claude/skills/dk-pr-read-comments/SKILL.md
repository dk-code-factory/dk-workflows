---
name: dk-pr-read-comments
description: Read complete inline PR feedback, review bodies and general comments without changing GitHub.
---

Resolve PACKAGE_ROOT from this installed skill location. This skill works independently of dk-workflows and needs no lifecycle, preparation or invocation tokens. Read [shared inputs, results and publication](../../../docs/contracts.md). Use runtime/dk-pr-address-reviews/scripts/fetch_unresolved_review_threads.py and fetch_pr_conversation.py to capture complete stable snapshots with pagination and source references. Include settled/deferred decisions and distinguish inline resolvable threads from top-level comments/review bodies. Recheck head and snapshot stability. Do not report a truncated page as complete.
