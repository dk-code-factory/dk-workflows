---
name: dk-pr-commits
description: Assess and organize a PR's complete commit series, preserving the reviewed final tree, authorship and coherent commit boundaries.
---

Resolve PACKAGE_ROOT from this installation and follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read [partition and cleanup](../../../docs/pr/partition-cleanup.md) and apply its commit-cleanup guidance. Inspect the whole series; return a justified no-op or reorganize commits only when authorized and safe. Retain ownership, recovery refs, exact leases, test evidence, boundary reasons and reviewed/final heads and trees. Use `dk-commit-message` for messages and `dk-pr-publish` for authorized exact-lease pushes. An unchanged head alone does not establish a whole-series assessment. Splitting scope into separate PRs belongs to `dk-pr-split`.
