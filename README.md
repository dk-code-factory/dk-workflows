# dk-workflows

Version 3.0.4. Agent-led PR workflows for Codex and Claude Code, with bundled drafting, feedback, publication, cleanup and CI skills. Requires Python 3.11+, Git and authenticated gh for remote PR actions.

Use `dk-pr-iter` as the universal entry point: it recognizes your accounts and routes your PRs to `dk-pr-fix` and other authors' PRs to `dk-pr-review`. Invoke either workflow directly when the intended responsibility is explicit. Fixing preserves the review/fix, commit cleanup and final CI stages. Incoming review verifies candidate findings independently before posting; automatic approval and merging each require separate authorization or enabled policy.

Configure reviewers, skills, personal accounts and PR policies through `dk-config`. There is no default reviewer. Automatic posting of verified findings is enabled for incoming review; approval and merging are disabled by default. Narrower instructions, including analysis-only review, override saved policies.

Supporting skills use a subject name for broad ownership and a final action when multiple operations share a subject:

| Skill | Responsibility |
| --- | --- |
| `dk-pr-reviewers-run` | Run configured reviewers and retain their results. |
| `dk-pr-feedback-read` | Collect complete PR discussion and feedback. |
| `dk-pr-feedback-address` | Verify feedback and implement justified fixes. |
| `dk-pr-feedback-reply` | Reply to feedback and resolve answered threads. |
| `dk-pr-review-post` | Publish a prepared, verified review. |
| `dk-pr-description` | Draft/update the title and description. |
| `dk-pr-decisions` | Collect, add and reconcile settled decisions. |
| `dk-pr-commits` | Assess and organize the full commit series. |
| `dk-pr-split` | Split oversized or unrelated scope into separate PRs. |
| `dk-pr-publish` | Publish prepared pushes, edits, replies, reviews and authorized merges with live checks and recovery. |
| `dk-ci` | Observe exact-head CI and handle authorized reruns. |
| `dk-ci-flakes` | Discover and verify known-flake evidence. |
| `dk-commit-message` | Draft and refine commit messages. |
| `dk-issue` | Draft, create and update issues. |

The old names are replaced in both host distributions: `dk-pr` → `dk-pr-iter`; the former maintenance-only `dk-pr-iter` → `dk-pr-fix`; the old review coordinator `dk-pr-review` → `dk-pr-reviewers-run`; `dk-pr-address-reviews` → `dk-pr-feedback-address`; `dk-pr-read-comments` → `dk-pr-feedback-read`; `dk-pr-reply-review` → `dk-pr-feedback-reply`; `dk-pr-post-review` → `dk-pr-review-post`; `dk-flaky-tests` → `dk-ci-flakes`; and `dk-issue-description` → `dk-issue`. `dk-pr-config` is merged into `dk-config`; `dk-pr-split-commits` is split into `dk-pr-split` and `dk-pr-commits`. `dk-pr-review` now denotes the full incoming-review workflow. Update explicitly saved skill selections through `dk-config`; there are no old-name aliases. Internal helper locations and private state paths stay stable, including completion prompts, flake history, task manifests and publication journals.

Install through this plugin’s marketplace or the organization catalog. External reviewers are installed and selected separately; there is no default reviewer. Native agents resolve and invoke skills, which own their procedures and completion reports. [Native reviewer handoffs](docs/native-review.md) retain host handles and require successful execution plus a complete report. [Architecture](https://github.com/dk-code-factory/dk-architecture) is independent and is never invoked automatically.

Standalone review and iteration share task identities and cached results. Fingerprints cover canonical PR identity, complete consumed Git content/context, installed resources, assigned instructions, options and the contract version. Generate specs with `spec --definition` and claim reviewer passes with `start-batch`. Each task retains its own result, claim and recovery path. Completed findings remain reusable analysis; failed, incomplete or damaged artifacts do not satisfy coverage. Squash-only cleanup can retain content reviews while refreshing head-specific checks. Use `status --explain` for reuse diagnostics.

Iteration runs review/fix cycles and commit cleanup before blocking on final-head CI. CI repairs refresh scope, affected reviews and cleanup before observation of the resulting final head. Observation deadlines, retry counters and the five-cycle review budget survive interruption.

Private workflow state lives outside repositories under XDG_STATE_HOME in `dk-workflows/v2`. Atomic claims prevent duplicate launches. Publication journals retain exact intent and require fresh checks; uncertain writes reconcile before another attempt. Direct skill calls use caching only when their caller explicitly manages task state.

Read [task state](docs/task-state.md), [workflow configuration](docs/configuration.md), [PR contracts](docs/pr/contracts.md), [publication recovery](docs/pr/publication.md) and [acceptance](docs/acceptance.md). Git capture implementation and measurements are in the [developer reference](docs/capture-performance.md).

Edit skill prompts under `codex/skills/`, then run `python3 scripts/sync_skills.py` to update the Claude distribution. `python3 scripts/check.py` validates both copies, links, Python syntax and versions. `python3 scripts/test.py` runs all bundled suites offline using real Git and fixture GitHub/native-skill transports. Run controlled native-agent exercises for material recipe changes; offline fixtures do not establish native adherence. `python3 scripts/version.py X.Y.Z` synchronizes release metadata.
