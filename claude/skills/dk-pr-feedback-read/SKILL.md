---
name: dk-pr-feedback-read
description: Read complete inline PR feedback, review bodies and general comments without changing GitHub.
---

Resolve PACKAGE_ROOT from this skill’s installation and follow [the shared PR task contract](../../../docs/pr/contracts.md).

For analysis inputs and live verification, follow [shared PR evidence](../../../docs/pr/supplied-evidence.md).

Use a supplied complete evidence handoff when provided; otherwise use runtime/dk-pr-address-reviews/scripts/fetch_unresolved_review_threads.py and fetch_pr_conversation.py to capture complete stable snapshots with pagination and source references. Include settled/deferred decisions and distinguish inline resolvable threads from top-level comments/review bodies. Recheck head and snapshot stability. Do not report a truncated page as complete.
