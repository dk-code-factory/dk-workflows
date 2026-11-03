# dk-pr-skills

Independent MIT package for Codex and Claude Code, version 2.0.0. Repository: https://github.com/dk-code-factory/dk-pr-skills. It requires Python 3.11+, Git and authenticated gh for remote actions. No workflow installation, host executor, lifecycle token or preparation id is needed.

Skills cover commit/issue/PR drafting, Decisions, complete feedback reads and fixes, replies, review publication, partition/commit cleanup, CI/flaky tests, publishing and domain settings. Native agents do the work; deterministic helpers preserve GitHub evidence, settings, CI deadlines and publication recovery. Read [task contracts](docs/contracts.md), [publication](docs/publication.md), [partition/cleanup](docs/partition-cleanup.md) and [configuration](docs/configuration.md).

Run `python3 scripts/test.py` for offline tests. Install with this directory's own host manifests/catalogs. It imports only its own runtime and can be copied outside the parent checkout. Install from this repository's own marketplace and release tags.
