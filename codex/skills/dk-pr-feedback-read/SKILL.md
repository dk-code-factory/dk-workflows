---
name: dk-pr-feedback-read
description: Read complete inline PR feedback, review bodies and general comments without changing GitHub.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Use [shared PR evidence](../../../docs/pr/supplied-evidence.md) for analysis and live verification.

Use the supplied complete evidence handoff. Without one, use `fetch_unresolved_review_threads.py` and `fetch_pr_conversation.py` under `runtime/dk-pr-feedback-address/scripts/`. Capture complete, stable snapshots across all pages, with source references.

Include settled decisions and deferred work. Distinguish resolvable inline threads from top-level comments and review bodies. Recheck the head and snapshot stability. A truncated page is incomplete.
