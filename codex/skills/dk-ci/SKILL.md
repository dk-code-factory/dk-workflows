---
name: dk-ci
description: Check CI for the current PR commit, preserve wait deadlines and classify failures using verified flaky tests or exceptions.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Run `python3 PACKAGE_ROOT/runtime/dk-ci/scripts/ci_wait.py --pr PR_URL --head SHA`, using the full lowercase head SHA. Keep one observer per PR and head across interruptions. The default wait is 30 minutes, with one 30-minute extension while checks are pending. Resume the same process when a tool yields. Read the current head and check attempts from GitHub.

The helper returns `success`, `known_flakes`, `accepted_exceptions`, `failure`, `timeout`, `no_checks` or `head_changed`. Only the first three satisfy CI, with current evidence. Restore access before using `--retry-denied`. Diagnose failures with `dk-ci-flakes` and code inspection. Preserve the caller's repair and rerun budgets.

Read the final JSON object's `result` on stdout: accepted outcomes exit 0; other terminal outcomes exit 1. Progress JSON goes to stderr. Helper exceptions exit 2 with `{"result":"error","message":"..."}` on stderr and no final stdout result. Preserve the error and treat CI as blocked.

Before rerunning a transient failure, reserve the caller's budget for that head. Use `scripts/publish.py` to prepare and publish a `ci-rerun` request with current failed-attempt evidence. Pass the verified operation to `ci_wait.py --rerun-operation FILE` to preserve the next attempt's wait window on resume. Do not rerun whole workflows as a fallback.
