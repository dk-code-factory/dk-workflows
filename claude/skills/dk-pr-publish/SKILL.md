---
name: dk-pr-publish
description: Publish prepared PR changes, including pushes, edits, replies, reviews and authorized merges, with fresh validation and recovery.
---

Resolve PACKAGE_ROOT from this skill’s installation and follow [the shared PR task contract](../../../docs/pr/contracts.md).

Use scripts/publish.py for description edits, replies and exact-lease pushes. Read [publication](../../../docs/pr/publication.md). This skill owns GitHub validation and recovery, independently of workflow caching. Use the publisher's resolve-thread, request-review, create-draft-pr, ci-rerun, post-review and merge-pr handlers for the remaining actions. Merging requires separate explicit or policy authorization, completed clean review/verification and current CI; iteration alone never authorizes it. Prepared requests capture exact targets and payloads; preserve the returned operation for read-only reconciliation after uncertainty. A cached analysis result cannot authorize an action.
