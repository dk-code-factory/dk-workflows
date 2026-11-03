---
name: dk-pr-publish
description: Publish authorized PR edits, pushes, replies and completion actions with live reconciliation.
---

Resolve PACKAGE_ROOT from this installed skill location. This bundled PR skill can be invoked directly and needs no workflow lifecycle, preparation or invocation tokens. Read [shared inputs, results and publication](../../../docs/pr/contracts.md). Use scripts/publish.py for description edits, replies and exact-lease pushes. Read [publication](../../../docs/pr/publication.md). This skill owns GitHub validation and recovery, independently of workflow caching. Use the publisher's resolve-thread, request-review, create-draft-pr, ci-rerun and post-review handlers for the remaining actions. Their prepared requests capture exact targets and payloads; preserve the returned operation for read-only reconciliation after uncertainty. A cached analysis result cannot authorize an action.
