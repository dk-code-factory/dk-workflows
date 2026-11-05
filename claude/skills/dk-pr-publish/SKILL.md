---
name: dk-pr-publish
description: Publish prepared PR changes, replies, reviews and authorized merges with current checks and recovery records.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Follow [publication and recovery](../../../docs/pr/publication.md). Use `scripts/publish.py` for description edits, replies and pushes with exact leases. Use its `resolve-thread`, `request-review`, `create-draft-pr`, `ci-rerun`, `ci-approve`, `post-review` and `merge-pr` handlers for other actions. This skill checks GitHub state and handles recovery independently of the workflow cache.

Merging requires separate explicit or saved policy authorization, completed clean review and verification, and current CI. Iteration alone does not authorize merging. Save the exact target, payload and returned operation path. After an uncertain outcome, use that operation for read-only reconciliation. Cached analysis never authorizes an action.

Fork CI approval uses [current incoming-review evidence](../../../docs/pr/ci-approval.md), independently of PR approval or merging. Use `dk-ci-approve` to discover eligible runs and retain one operation per run and attempt.
