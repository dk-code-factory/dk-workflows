# v2 behavior contract

Preserve: unchanged reruns invoke zero reviewers; failed or interrupted tasks retain completed siblings; five review/fix cycles with retained same-cycle retries; tree-preserving commit cleanup; fresh publication reconciliation after interruption; exact-head CI; existing partition settings and explicit human acceptances.

Deliberate changes: agents resolve and invoke skills; no host adapters or reviewer-internal proof gates; no engineering preparation graph or semantic reconciliation; no automatic architecture work or architecture completion gate. Domain skills own GitHub and publication. The cache applies only to invocations explicitly managed by workflows.

Regression scenarios in tests exercise actual Git trees across squash, independent reviewer resource edits, scope/instruction changes, damaged and failed outputs, concurrent claims, interruption, explicit invalidation, retained cycle/retry limits and readiness separate from completed findings. Package tests exercise publication interruption and standalone domain helpers. No authenticated model or GitHub calls are required.

PR skills, helpers, tests and fixtures are bundled in dk-workflows. Its workflow, PR and integration suites run without another repository or external test checkout. Architecture remains in its independent repository. Production coordination invokes selected native skills and keeps domain behavior inside its owning skills/modules.

The full recipe test in tests/test_recipe_integration.py drives separate helper/skill processes and a real local Git remote. It covers feedback, Decisions/description synchronization, partition assessment, CI/rerun receipts, interrupted and partial reviews, retained retry/cycle budgets, findings fixes, tree-preserving squash cleanup, final coverage, replies/resolution, re-review and completion. It then repeats standalone review and iteration, asserting zero reviewer launches and no duplicate publications. This is deterministic integration coverage; native-agent adherence also needs a controlled skill exercise when recipes change materially.
