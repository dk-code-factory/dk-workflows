---
name: dk-commit-split
description: Review and organize all PR commits while preserving the reviewed final tree, authorship and logical commit boundaries.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Follow commit cleanup in [partition and cleanup](../../../docs/pr/partition-cleanup.md). Inspect every commit. Explain why no change is needed, or reorganize commits when authorized and safe. Save ownership evidence, recovery refs, exact remote leases, test results, boundary reasons, and the reviewed and final heads and trees.

Use `dk-commit-message` for messages and `dk-pr-publish` for authorized pushes with exact leases. An unchanged head does not prove that every commit was assessed. Use `dk-pr-split` to move work into separate PRs.
