# PR domain configuration

These policies are managed by `dk-config` together with [workflow configuration](../configuration.md). There is no separate PR configuration skill. Keep the existing helper and private storage layout so saved choices remain effective.

Use `python3 PACKAGE_ROOT/scripts/pr_config.py --cwd REPO [--pr PR_URL] COMMAND`. `pr-size list|set|reset` manages thresholds and independent evacuation/split modes. `pr-partition show|enable|disable|instructions|clear-instructions|reset` manages per-PR exact instructions, opt-outs and history. `ci-exceptions list|add|remove|reset` owns explicit test or pipeline matcher settings and reasons at repository/user scope. Exception matchers belong to the PR base repository; other repository guidance follows fork/upstream/user precedence.

`rules show|set|reset` and `drafting show|set|reset` manage domain-owned JSON documents. `completion show|set|reset` manages the original plain-text prompt store. Use --scope repo|user and --file FILE for set. Show uses effective precedence by default and returns the original source path. Preserve rule conflicts and exact original instructions rather than storing a generated summary as authoritative. Drafting values select native skill identities. Completion instructions cannot broaden publication permission.

Private rules, drafting, size, per-PR partition, CI-exception and flaky-test records live under XDG_DATA_HOME. Bundled PR skills own these settings and completion prompts. Drafting roles inherit individually; private rules use the first nonempty repository/upstream/user record. Do not reset user acceptances, conditions or opt-outs. Architecture catalogs/settings belong to dk-architecture. Coordinate reviewer selection through dk-config.

With no custom completion instructions, verify required local work and current CI, synchronize the PR description and Decisions, finish verified feedback replies, confirm the exact current head/target and cleanup evidence, and request re-review from appropriate prior reviewers when authorized. Return current remote receipts and blockers. Never merge as part of default completion.
