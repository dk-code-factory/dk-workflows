# dk-workflows

Version 2.2.6. Agent-led PR workflows and bundled PR skills for Codex and Claude Code. Python 3.11+ and Git support deterministic bookkeeping; authenticated gh is required for remote PR actions.

The coordinating skills are `dk-pr-review`, `dk-pr-iter` and `dk-config`. This same plugin also includes PR drafting, Decisions, feedback reads/fixes, replies, review publication, partitioning/commit cleanup, CI/flaky-test handling, durable publication and PR settings:

- `dk-commit-message`, `dk-issue-description`, `dk-pr-description`, `dk-pr-decisions`
- `dk-pr-read-comments`, `dk-pr-address-reviews`, `dk-pr-reply-review`, `dk-pr-post-review`
- `dk-pr-split-commits`, `dk-ci`, `dk-flaky-tests`, `dk-pr-publish`, `dk-pr-config`

Install this plugin through its own marketplace or the organization catalog. The bundled PR skills need no separate plugin or repository. Selected external reviewers are installed separately; there is no default reviewer. Native agents resolve and invoke skills, which own their work and completion reports. The coordination helper owns fingerprints, claims, retained results and budgets. There are no host launchers, reviewer-specific execution proofs, model/profile discovery or custom updater lifecycle.

[Architecture](https://github.com/dk-code-factory/dk-architecture) remains independently usable. Workflows never invoke it automatically or require architecture packets or completion gates.

One cache entry per managed skill task is shared between standalone review and iteration. Fingerprints cover canonical PR identity, exact installed resources, assigned task/instructions, consumed snapshots, execution options and the contract version. Content reviews compare complete target/base/head trees, diff and governing context; commit SHAs remain provenance. Squash-only cleanup retains content reviews while refreshing head-specific checks. Reusable results may contain findings; readiness is assessed separately. Failed, incomplete or damaged artifacts never satisfy coverage.

Iteration waits for CI after review/fix cycles, commit cleanup and final scope synchronization. Intermediate CI does not block review or cleanup. Final failures remain blockers; code repairs refresh affected reviews and cleanup before CI is observed on the new final head. Existing observation deadlines and retry budgets are retained.

Private workflow manifests and immutable outputs live outside repositories under XDG_STATE_HOME in `dk-workflows/v2`. Atomic claims prevent duplicate launches; interruptions require explicit recovery and retain the five-cycle review budget and retry counters. PR publication retains its existing private journal paths for compatibility and always performs fresh checks. Uncertain writes reconcile before any replacement attempt. Direct skill calls bypass workflow caching unless their caller explicitly uses it.

Read [task state](docs/task-state.md), [workflow configuration/migration](docs/configuration.md), [PR task contracts](docs/pr/contracts.md), [publication recovery](docs/pr/publication.md) and the [acceptance contract](docs/acceptance.md). Saved bindings using the temporary `dk-pr-skills:` prefix resolve to the bundled `dk-workflows:` identities. Existing private settings, publication intents, CI windows and budgets remain available. Active v1 runs still finish through their retained v1 runtime; v2 imports no v1 review caches.

Run `python3 scripts/check.py` and `python3 scripts/test.py`. All suites and fixtures are bundled and run offline without another checkout. The full recipe uses separate processes, real Git, a local remote and fixture GitHub/native-skill transports. Both host manifests use 2.2.6; `scripts/version.py X.Y.Z` updates the synchronized plugin versions.
