---
name: dk-config
description: Manage reviewers, cycle hooks, accounts, PR rules, drafting, splitting, CI exceptions and completion settings.
---

Set PLUGIN_ROOT to this plugin's root directory. Read [workflow configuration](../../../docs/configuration.md). Use `scripts/config.py` for reviewers, skill roles, before/after cycle hooks, personal accounts and incoming review policies. Hooks are ordered agent instructions with an optional selected skill. Preserve their exact instructions and scope; an empty phase list disables inherited hooks for that phase. Follow [cycle hooks](../../../docs/cycle-hooks.md) for boundaries, task definitions and recovery.

Read [PR configuration](../../../docs/pr/configuration.md). Use `scripts/pr_config.py` for rules, drafting, completion prompts, size and split policies, and CI exceptions. Use `dk-ci-flakes` for saved flaky-test evidence.

Preserve private stores, scope precedence, original instructions and per-PR choices. Renaming a skill does not permit resetting settings or moving saved state. The selected external skill owns its settings. The separate `dk-architecture` package owns architecture settings.

Verify additional personal accounts on their GitHub host and save their immutable numeric ids. Explain the repository or user scope before changing automatic posting, fork CI approval, PR approval or merge settings. Enable only requested actions. Permission to post findings does not permit approval or merging. Preserve unrelated settings.

`incoming_review.approve_ci` defaults to true, independently of `approve` and `merge`; `skills.ci_approve` selects the bundled `dk-ci-approve` by default. Narrower instructions, including analysis-only requests, disable affected mutations.
