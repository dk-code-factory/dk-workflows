---
name: dk-config
description: Change, inspect or explain saved dk-workflows settings when configuration is the user's task. Routine PR routing, review, fixing, splitting and finalization read settings directly without invoking this skill.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Use this skill for configuration requests, including questions about available settings and their effective values or sources. During ordinary workflow execution, use the read-only helpers in [workflow configuration](../../../docs/configuration.md) and [PR configuration](../../../docs/pr/configuration.md) directly. Consuming an existing policy, checking selected-skill availability or merging a PR under current permissions does not require invoking this skill or changing saved settings. Source inspection while developing or troubleshooting this package is ordinary repository work.

Persist workflow state only when it reduces total token consumption, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to this plugin's root directory. Read [workflow configuration](../../../docs/configuration.md). Use `scripts/config.py skill-show|skill-set|skill-reset|skill-plan|skill-preflight` for skill-owned settings. Discover each bundled skill's `config.schema.json`; validate only options it declares and preserve unrelated settings. Use fully qualified identities for every selection. Process commands are compatibility readers for older settings; new task and finalizer selections are invocation lists owned by the relevant skill. Do not introduce a coordinator, responsibility map or declarative condition language. Place custom work before or after review directly in the task list. Existing process hook records remain supported by the [legacy cycle-hook API](../../../docs/cycle-hooks.md); do not add them to new skill-owned options or silently append them to a new task list.

Resolve configuration for `dk-workflows:dk-config` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Use the same helper for [PR fix finalizers](../../../docs/finalizers.md): an ordered list of shared invocation strings or objects, with explicit `{"parallel":[...]}` groups; legacy nested arrays remain readable. A skill may appear in distinct occurrences; use list occurrence identity when generating new managed specs. Preserve the whole list and grouping; a scoped list replaces inherited finalizers and `[]` disables them. Resolve external selections through native tools. Explain skipped default checks and preserve existing process and skill selections when no explicit finalizer list is requested.

Route requests to add, update, list, inspect or remove scoped implementation and review preferences to `dk-workflows:dk-preferences` under [the preference contract](../../../docs/preferences.md). Keep them separate from existing saved rules and settings; do not convert rules automatically.

Use `scripts/pr_config.py commit-policy show|set|reset` for [commit grouping and message policies](../../../docs/commit-policy.md). `set --scope user|repo --file FILE` accepts a literal JSON `prompt` policy or a shared invocation string or object, including custom instructions, options and agent model/effort settings. Preserve the exact prompt, resolve selected skills through native tools and preserve unrelated settings. Scope reset restores inheritance. Report the effective policy and source after changes. Policy providers return instructions only; selecting a policy does not grant rewrite or publication permission or add cleanup to custom finalizers.

Read [PR configuration](../../../docs/pr/configuration.md). Use `scripts/pr_config.py` for rules, drafting, completion prompts, size and split policies, and CI exceptions. Use `dk-workflows:dk-ci-flakes` for saved flaky-test evidence.

Preserve private stores, scope precedence, original instructions and per-PR choices. Renaming a skill does not permit resetting settings or moving saved state. The selected external skill owns its settings. The separate `dk-architecture` package owns architecture settings.

Verify additional personal accounts on their GitHub host and save their immutable numeric ids. Explain the repository or user scope before changing automatic posting, fork CI approval, PR approval or merge settings. Enable only requested actions. Permission to post findings does not permit approval or merging. Preserve unrelated settings.

`dk-workflows:dk-pr-review` option `permissions.approve_fork_ci` defaults to true, independently of `approve_pr` and `merge_pr`; the skill-owned finalizer list selects `dk-workflows:dk-ci-approve` by default. Narrower instructions, including analysis-only requests, disable affected mutations.
