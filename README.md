# dk-workflows

Version 3.5.1. Agent-led PR workflows for Codex and Claude Code, with bundled drafting, feedback, publication, cleanup and CI skills. Requires Python 3.11+, Git and authenticated gh for remote PR actions.

Use `dk-pr-iter` as the universal entry point: it recognizes your accounts and routes your PRs to `dk-pr-fix` and other authors' PRs to `dk-pr-review`. Invoke either workflow directly when the intended responsibility is explicit. Fixing runs review/fix cycles followed by [configured finalizers](docs/finalizers.md); the default ending preserves commit cleanup and final CI. Incoming review verifies candidate findings independently before posting; automatic approval and merging each require separate authorization or enabled policy.

Configure reviewers, skills, personal accounts and PR policies through `dk-config`. Ordered [cycle hooks](docs/cycle-hooks.md) run agent instructions or selected skills before and after each new review cycle, with retained completion on resume. There is no default reviewer. Automatic posting of verified findings is enabled for incoming review; approval and merging are disabled by default. Narrower instructions, including analysis-only review, override saved policies.

Configure `finalizers` as an ordered list of `/skill/PLUGIN:SKILL` strings. Nested lists run independent skills in parallel and wait for every sibling before proceeding. External installed skills are supported. Each scoped list replaces inherited finalizers; `[]` disables them. Final live checks report selected-plan completion separately from skipped cleanup, CI or other readiness evidence.

Configure [commit grouping and message policies](docs/commit-policy.md) through `dk-config`: save a prompt or select a native policy skill globally or for a repository. For example, “Use dk-config to set my global commit policy to this prompt: squash each PR into one commit and use a short subject-only message.” Policies apply during selected final cleanup and direct `dk-commit-split` calls, within existing rewrite and publication permissions. The default keeps current cleanup behavior.

Use `dk-preferences` for concrete personal or repository instructions scoped to implementation and review. [Preferences](docs/preferences.md) live in a separate private XDG_CONFIG_HOME catalog. Admission settles contradictions before saving; runtime retrieval matches paths and coordinators evaluate conditions. Workflows pass complete scoped records downstream and refresh affected task inputs when applicable preferences change. Existing saved rules stay separate.

Supporting skills use a subject name for broad ownership and a final action when multiple operations share a subject:

| Skill | Responsibility |
| --- | --- |
| `dk-preferences` | Admit, store, inspect and remove scoped implementation and review preferences. |
| `dk-pr-reviewers-run` | Run configured reviewers and retain their results. |
| `dk-pr-feedback-read` | Collect complete PR discussion and feedback. |
| `dk-pr-feedback-address` | Verify feedback and implement justified fixes. |
| `dk-pr-feedback-reply` | Reply to feedback and resolve answered threads. |
| `dk-pr-review-post` | Publish a prepared, verified review. |
| `dk-pr-description` | Draft/update the title and description. |
| `dk-pr-decisions` | Collect, add and reconcile settled decisions. |
| `dk-commit-split` | Assess and organize the full commit series. |
| `dk-pr-split` | Split oversized or unrelated scope into separate PRs. |
| `dk-pr-publish` | Publish prepared pushes, edits, replies, reviews and authorized merges with live checks and recovery. |
| `dk-ci` | Observe exact-head CI and handle authorized reruns. |
| `dk-ci-flakes` | Discover and verify known-flake evidence. |
| `dk-commit-message` | Draft and refine commit messages. |
| `dk-issue` | Draft, create and update issues. |

The old names are replaced in both host distributions: `dk-pr` → `dk-pr-iter`; the former maintenance-only `dk-pr-iter` → `dk-pr-fix`; the old review coordinator `dk-pr-review` → `dk-pr-reviewers-run`; `dk-pr-address-reviews` → `dk-pr-feedback-address`; `dk-pr-read-comments` → `dk-pr-feedback-read`; `dk-pr-reply-review` → `dk-pr-feedback-reply`; `dk-pr-post-review` → `dk-pr-review-post`; `dk-flaky-tests` → `dk-ci-flakes`; and `dk-issue-description` → `dk-issue`. `dk-pr-config` is merged into `dk-config`; `dk-pr-split-commits` is split into `dk-pr-split` and `dk-commit-split`. `dk-pr-commits` is renamed to `dk-commit-split`. `dk-pr-review` now denotes the full incoming-review workflow. Update explicitly saved skill selections through `dk-config`; there are no old-name aliases. Runtime helper folders now match the public skill names: `runtime/dk-pr-feedback-address/` and `runtime/dk-issue/`. Private state paths stay stable, including completion prompts, flake history, task manifests and publication journals.

Install through this plugin’s marketplace or the organization catalog. External reviewers are installed and selected separately; there is no default reviewer. Native agents resolve and invoke skills, which own their procedures and completion reports. [Native reviewer handoffs](docs/native-review.md) retain host handles and require successful execution plus a complete report. [Architecture](https://github.com/dk-code-factory/dk-architecture) is independent and is never invoked automatically.

Standalone review and iteration share task identities and cached results. Fingerprints cover canonical PR identity, complete consumed Git content/context, installed resources, assigned instructions, options and the contract version. Generate specs with `spec --definition` and claim reviewer passes with `start-batch`. Each task retains its own result, claim and recovery path. Completed findings remain reusable analysis; failed, incomplete or damaged artifacts do not satisfy coverage. Squash-only cleanup can retain content reviews while refreshing head-specific checks. Use `status --explain` for reuse diagnostics.

The default fix plan runs commit cleanup and governing-content synchronization before blocking on final-head CI. CI repairs refresh scope, affected reviews and selected cleanup before observation of the resulting final head. Custom finalizer plans control which ending actions run. Observation deadlines, retry counters and the five-cycle review budget survive interruption.

Private workflow state lives outside repositories under XDG_STATE_HOME in `dk-workflows/v2`. Atomic claims prevent duplicate launches. Publication journals retain exact intent and require fresh checks; uncertain writes reconcile before another attempt. Direct skill calls use caching only when their caller explicitly manages task state.

Read [commit cleanup](docs/pr/commit-cleanup.md) for the read-only commit-series inspection command and cleanup safeguards. That helper requires Git 2.45 or newer.

Read [task state](docs/task-state.md), [workflow configuration](docs/configuration.md), [PR contracts](docs/pr/contracts.md), [publication recovery](docs/pr/publication.md) and [acceptance](docs/acceptance.md). Git capture implementation and measurements are in the [developer reference](docs/capture-performance.md).

Edit skill prompts under `codex/skills/`, then run `python3 scripts/sync_skills.py` to update the Claude distribution. `python3 scripts/check.py` validates both copies, links, Python syntax and versions. `python3 scripts/test.py` runs all bundled suites offline using real Git and fixture GitHub/native-skill transports. Run controlled native-agent exercises for material recipe changes; offline fixtures do not establish native adherence. `python3 scripts/version.py X.Y.Z` synchronizes release metadata.

Install `pytest`, `pytest-subtests` and `pytest-xdist` to run `python3 scripts/test.py --workers 2`, as CI does. Recipe scenarios run in parallel after the other workflow tests, keeping Git capture process-count probes separate from concurrent test activity. The other suites retain separate serial processes. Every suite reports its 20 slowest test phases. The default test command remains serial and does not require `pytest-xdist`.

CI checks package validity and PR release versions before installing test dependencies. New PR commits cancel superseded validation runs; main validations remain independent. Releases verify tag identity and main ancestry first, then reuse successful main validation only for the exact tagged commit. Missing, malformed or inaccessible evidence falls back to the complete test suite before publication.
