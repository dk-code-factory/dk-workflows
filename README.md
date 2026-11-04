# dk-workflows

Version 2.3.1. Agent-led PR workflows for Codex and Claude Code, with bundled drafting, feedback, publication, cleanup and CI skills. Requires Python 3.11+, Git and authenticated gh for remote PR actions.

The coordinators are `dk-pr-review`, `dk-pr-iter` and `dk-config`. Bundled domain skills cover:

- Drafting and Decisions: `dk-commit-message`, `dk-issue-description`, `dk-pr-description`, `dk-pr-decisions`.
- Feedback and publication: `dk-pr-read-comments`, `dk-pr-address-reviews`, `dk-pr-reply-review`, `dk-pr-post-review`, `dk-pr-publish`.
- Scope, CI and settings: `dk-pr-split-commits`, `dk-ci`, `dk-flaky-tests`, `dk-pr-config`.

Install through this plugin’s marketplace or the organization catalog. External reviewers are installed and selected separately; there is no default reviewer. Native agents resolve and invoke skills, which own their procedures and completion reports. [Native reviewer handoffs](docs/native-review.md) retain host handles and require successful execution plus a complete report. [Architecture](https://github.com/dk-code-factory/dk-architecture) is independent and is never invoked automatically.

Standalone review and iteration share task identities and cached results. Fingerprints cover canonical PR identity, complete consumed Git content/context, installed resources, assigned instructions, options and the contract version. Generate specs with `spec --definition` and claim reviewer passes with `start-batch`. Each task retains its own result, claim and recovery path. Completed findings remain reusable analysis; failed, incomplete or damaged artifacts do not satisfy coverage. Squash-only cleanup can retain content reviews while refreshing head-specific checks. Use `status --explain` for reuse diagnostics.

Iteration runs review/fix cycles and commit cleanup before blocking on final-head CI. CI repairs refresh scope, affected reviews and cleanup before observation of the resulting final head. Observation deadlines, retry counters and the five-cycle review budget survive interruption.

Private workflow state lives outside repositories under XDG_STATE_HOME in `dk-workflows/v2`. Atomic claims prevent duplicate launches. Publication journals retain exact intent and require fresh checks; uncertain writes reconcile before another attempt. Direct skill calls use caching only when their caller explicitly manages task state.

Read [task state](docs/task-state.md), [workflow configuration](docs/configuration.md), [PR contracts](docs/pr/contracts.md), [publication recovery](docs/pr/publication.md) and [acceptance](docs/acceptance.md). Git capture implementation and measurements are in the [developer reference](docs/capture-performance.md).

Edit skill prompts under `codex/skills/`, then run `python3 scripts/sync_skills.py` to update the Claude distribution. `python3 scripts/check.py` validates both copies, links, Python syntax and versions. `python3 scripts/test.py` runs all bundled suites offline using real Git and fixture GitHub/native-skill transports. Run controlled native-agent exercises for material recipe changes; offline fixtures do not establish native adherence. `python3 scripts/version.py X.Y.Z` synchronizes release metadata.
