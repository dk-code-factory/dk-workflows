---
name: dk-config
description: Change, inspect or explain saved dk-workflows settings when configuration is the user's task. Routine PR routing, review, fixing, splitting and finalization read settings directly without invoking this skill.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Use this skill for configuration requests, including questions about available settings and their effective values or sources. During ordinary workflow execution, use the read-only helpers in [workflow configuration](../../../docs/configuration.md) and [PR configuration](../../../docs/pr/configuration.md) directly. Consuming an existing policy, checking selected-skill availability or merging a PR under current permissions does not require invoking this skill or changing saved settings. Source inspection while developing or troubleshooting this package is ordinary repository work.

Set PLUGIN_ROOT to this plugin's root directory. Read [workflow configuration](../../../docs/configuration.md). Use `scripts/config.py` for process entrypoints, process operations, reviewers, finalizers, before/after cycle hooks, personal accounts and action policies. Hooks are ordered agent instructions with an optional selected skill. Preserve their exact instructions and scope; an empty phase list disables inherited hooks for that phase. Follow [cycle hooks](../../../docs/cycle-hooks.md) for boundaries, task definitions and recovery.

Use the same helper for [PR fix finalizers](../../../docs/finalizers.md): an ordered list of shared invocation strings or objects, with nested lists for parallel groups. Each skill appears once. Preserve the whole list and grouping; a scoped list replaces inherited finalizers and `[]` disables them. Resolve external selections through native tools. Explain skipped default checks and preserve existing process and skill selections when no explicit finalizer list is requested.

Route requests to add, update, list, inspect or remove scoped implementation and review preferences to `dk-preferences` under [the preference contract](../../../docs/preferences.md). Keep them separate from existing saved rules and settings; do not convert rules automatically.

Use `scripts/pr_config.py commit-policy show|set|reset` for [commit grouping and message policies](../../../docs/commit-policy.md). `set --scope user|repo --file FILE` accepts a literal JSON `prompt` policy or a shared invocation string or object, including custom instructions, options and agent model/effort settings. Preserve the exact prompt, resolve selected skills through native tools and preserve unrelated settings. Scope reset restores inheritance. Report the effective policy and source after changes. Policy providers return instructions only; selecting a policy does not grant rewrite or publication permission or add cleanup to custom finalizers.

Read [PR configuration](../../../docs/pr/configuration.md). Use `scripts/pr_config.py` for rules, drafting, completion prompts, size and split policies, and CI exceptions. Use `dk-ci-flakes` for saved flaky-test evidence.

Preserve private stores, scope precedence, original instructions and per-PR choices. Renaming a skill does not permit resetting settings or moving saved state. The selected external skill owns its settings. The separate `dk-architecture` package owns architecture settings.

Verify additional personal accounts on their GitHub host and save their immutable numeric ids. Explain the repository or user scope before changing automatic posting, fork CI approval, PR approval or merge settings. Enable only requested actions. Permission to post findings does not permit approval or merging. Preserve unrelated settings.

`processes.pr-review.actions.approve_ci` defaults to true, independently of `approve` and `merge`; `processes.pr-review.operations` includes the bundled `dk-ci-approve` invocation by default. Narrower instructions, including analysis-only requests, disable affected mutations.
