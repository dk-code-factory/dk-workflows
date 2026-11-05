---
name: dk-config
description: Manage reviewers, cycle hooks, finalizers, accounts, PR rules, scoped preferences, drafting, splitting, CI exceptions and completion settings.
---

Set PLUGIN_ROOT to this plugin's root directory. Read [workflow configuration](../../../docs/configuration.md). Use `scripts/config.py` for reviewers, skill roles, before/after cycle hooks, personal accounts and incoming review policies. Hooks are ordered agent instructions with an optional selected skill. Preserve their exact instructions and scope; an empty phase list disables inherited hooks for that phase. Follow [cycle hooks](../../../docs/cycle-hooks.md) for boundaries, task definitions and recovery.

Use the same helper for [PR fix finalizers](../../../docs/finalizers.md): an ordered list of qualified `/skill/PLUGIN:SKILL` strings, with nested lists for parallel groups. Each skill appears once. Preserve the whole list and grouping; a scoped list replaces inherited finalizers and `[]` disables them. Resolve external selections through native tools. Explain skipped default checks and preserve existing role selections when no explicit finalizer list is requested.

Route requests to add, update, list, inspect or remove scoped implementation and review preferences to `dk-preferences` under [the preference contract](../../../docs/preferences.md). Keep them separate from existing saved rules and settings; do not convert rules automatically.

Read [PR configuration](../../../docs/pr/configuration.md). Use `scripts/pr_config.py` for rules, drafting, completion prompts, size and split policies, and CI exceptions. Use `dk-ci-flakes` for saved flaky-test evidence.

Preserve private stores, scope precedence, original instructions and per-PR choices. Renaming a skill does not permit resetting settings or moving saved state. The selected external skill owns its settings. The separate `dk-architecture` package owns architecture settings.

Verify additional personal accounts on their GitHub host and save their immutable numeric ids. Explain the repository or user scope before changing automatic posting, approval or merge settings. Enable only requested actions. Permission to post findings does not permit approval or merging. Preserve unrelated settings.
