---
name: dk-flaky-tests
description: Discover and verify issue-backed flaky tests and explicit CI exceptions from exact Actions attempts.
---

Resolve PACKAGE_ROOT from this installed skill location. This skill works independently of dk-workflows and needs no lifecycle, preparation or invocation tokens. Read [shared inputs, results and publication](../../../docs/contracts.md). Use runtime/dk-ci/scripts/flaky_tests.py for bounded discovery, list and synchronization. Retain exact repository/head/job/attempt/test evidence and currently open issue proof. Missing logs, inaccessible/closed issues, unsupported reports and mixed unknown failures remain blocking. A known test flake cannot exempt an entire job. Report configured explicit exceptions separately with ids, reason, scope and source. Never create an issue or rerun jobs unless authorized.
