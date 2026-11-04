---
name: dk-ci
description: Observe exact-head PR CI with retained deadlines and classify failures using verified flakes or exceptions.
---

Resolve PACKAGE_ROOT from this skill’s installation and follow [the shared PR task contract](../../../docs/pr/contracts.md).

Use runtime/dk-ci/scripts/ci_wait.py --pr PR_URL --head SHA. One observer per PR/head, retained across interruption. Default wait is 30 minutes with one 30-minute extension while pending. Reuse the same process when tool calls yield; do not start a second observer. Current head and attempts are live inputs. The helper returns success, known_flakes, accepted_exceptions, failure, timeout, no_checks or head_changed. Only the first three can satisfy CI with current supporting evidence. Repair access before --retry-denied. Diagnose failures through dk-ci-flakes and code inspection; callers retain repair/rerun budgets. For a transient rerun, reserve the caller's per-head budget, prepare/publish a ci-rerun request with fresh failed-attempt evidence through scripts/publish.py, and pass its verified operation to ci_wait.py --rerun-operation FILE. This preserves the next attempt window on resume. Never rerun broad workflows as a fallback.
