---
name: dk-pr-split
description: Move unrelated work or split large changes into separate PRs under saved size, scope and publication policies.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Follow partition guidance in [partition and cleanup](../../../docs/pr/partition-cleanup.md). Read current policies through `dk-config`. Save ownership evidence, recovery refs, exact remote leases, test results and reasons for each split boundary. Explain why work should stay in this PR, or return verified publication receipts for the split. Use `dk-commit-split` to assess or reorganize commits while preserving the final tree.
