# dk-workflows

Version 2.0.0. Agent-led PR review and iteration for Codex and Claude Code. Python 3.11+ and Git are required for bookkeeping; selected domain skills supply their own tools.

Three public skills:

- `dk-pr-review`: invoke configured reviewer skills and reuse unchanged completed results.
- `dk-pr-iter`: coordinate feedback, scope, CI, bounded review/fix cycles, commit cleanup and completion.
- `dk-config`: select skills and required reviewers; delegate domain settings to their owner.

Install this plugin through its Codex or Claude marketplace manifest. There is no default reviewer. Install selected reviewer and domain skills separately; missing selected installations are reported before work begins. Native agents resolve and invoke skills. The core has no host launcher, reviewer-specific execution checks, model/profile discovery or custom updater lifecycle.

The independently installable [dk-pr-skills](https://github.com/dk-code-factory/dk-pr-skills) owns PR drafting, Decisions, feedback, publication, splitting/cleanup, CI, flaky tests and domain configuration. The [dk-architecture](https://github.com/dk-code-factory/dk-architecture) owns architecture catalogs, rules, review and exceptions. Each owns its source, manifests, tests, CI and releases in its own repository. They run without importing workflow code. Architecture has no automatic invocation, packets or completion gates in workflows.

Each managed skill task has one reusable cache entry shared between standalone review and iteration. Fingerprints cover canonical PR identity, exact installed resources, assigned task/instructions, consumed input snapshots, execution options and contract version. Ordinary content review compares complete target/base/head trees, diff and context; commit SHAs are provenance. Squash-only changes retain content review and refresh head-specific checks. Failed/incomplete/damaged results never count as coverage. Findings can remain in a reusable completed result.

State and immutable outputs live outside repositories in `dk-workflows/v2` under XDG_STATE_HOME. Atomic claims prevent duplicate launches; interrupted claims require explicit recovery. Five review-and-fix cycles and retry counters survive resume. Domain skills own fresh publication checks and reconcile uncertain writes before another attempt. Direct skill calls bypass the cache unless their caller uses the helper explicitly.

Read [task state](docs/task-state.md), [configuration/migration](docs/configuration.md) and the [acceptance contract](docs/acceptance.md). Active v1 runs finish through their retained v1 runtime; v2 does not migrate caches or convert active runs.

Run `python3 scripts/check.py` and `python3 scripts/test.py --pr-skills-root PATH`. Use a checkout of the dk-pr-skills revision pinned in test-dependencies.json, or set DK_PR_SKILLS_ROOT. CI checks out that exact dependency. The tests themselves are offline: the full recipe uses real Git, a local remote and fixture GitHub/native skill transports. Each domain repository runs its own independent suites. Both host manifests use 2.0.0. `scripts/version.py X.Y.Z` updates core versions; each extracted package owns its own release thereafter. The organization catalog lists all three independent plugins; the workflow installation exposes only its three coordinating skills.
