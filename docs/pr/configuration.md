# PR configuration

Use `dk-config` for these policies and [workflow settings](../configuration.md). Keep existing helpers and private storage paths so saved choices remain effective.

Run `python3 PACKAGE_ROOT/scripts/pr_config.py --cwd REPO [--pr PR_URL] COMMAND`:

- `pr-size list|set|reset`: thresholds and separate modes for moving unrelated work and splitting related work.
- `pr-partition show|enable|disable|instructions|clear-instructions|reset`: exact per-PR instructions, opt-outs and history.
- `ci-exceptions list|add|remove|reset`: explicit test or pipeline matchers and reasons at repository or user scope. Matchers belong to the PR base repository. Other repository guidance uses fork, upstream, then user precedence.
- `rules show|set|reset` and `drafting show|set|reset`: PR-owned JSON settings.
- `completion show|set|reset`: original plain-text completion prompts.

For `rules`, `drafting` and `completion`, use `set --scope repo|user --file FILE`. Their `show` commands use effective precedence and return source paths. Preserve conflicting rules and exact original instructions. A generated summary must not replace them. Drafting values identify native skills. Completion instructions cannot expand publication permission.

Private rules, drafting, size, per-PR partition, CI exceptions and flaky-test records live under XDG_DATA_HOME. Bundled PR skills own these settings and completion prompts. Drafting roles inherit separately. Private rules use the first nonempty repository, upstream or user record, in that order. Preserve user acceptances, conditions and opt-outs. `dk-architecture` owns architecture catalogs and settings. Use `dk-config` to select reviewers.

Without custom completion instructions, verify required local work and current CI. Update the PR description and Decisions, finish verified feedback replies, and confirm the exact current head, target and cleanup evidence. Request re-review from appropriate prior reviewers when authorized. Return current remote receipts and blockers. Default completion never includes merging.
