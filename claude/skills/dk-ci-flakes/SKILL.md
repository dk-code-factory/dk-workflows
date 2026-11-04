---
name: dk-ci-flakes
description: Find flaky tests linked to open issues and verify CI exceptions for specific GitHub Actions attempts.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Use `runtime/dk-ci/scripts/flaky_tests.py` to search within its limits, list records and synchronize them. Save the repository, head, job, attempt, test and proof that the linked issue is open. Missing logs, inaccessible or closed issues, unsupported reports and unknown failures block acceptance. A known flaky test cannot exempt an entire job.

Report configured CI exceptions separately, with each id, reason, scope and source. Create issues or rerun jobs only when authorized.
