# Workflow configuration

Run `python3 PLUGIN_ROOT/scripts/config.py --repository HOST/OWNER/REPO show|set|reset|preflight`. `set --file SETTINGS.json --scope repo|user` replaces that scope. `show` merges user settings, optional `--upstream HOST/OWNER/REPO` settings, then repository settings. Skill roles inherit separately. Each ordered reviewer list replaces the lower-scope list. Private files are stored under `${XDG_CONFIG_HOME:-~/.config}/dk-workflows/v2/`.

```json
{
  "reviewers": [{"skill":"code-review-ad:code-review-ad", "required":true, "options":{}}],
  "skills": {"feedback":"dk-workflows:dk-pr-feedback-address"},
  "domain_settings": {"dk-workflows":"private domain settings"}
}
```

There is no default reviewer. PR roles default to bundled skills; users may select other installed skills. `run_reviewers`, `read_feedback` and `post_review` select the coordinator, feedback collector and review publisher. Partition and cleanup use separate `dk-pr-split` and `dk-commit-split` skills.

`cycle_hooks` defines ordered `before_cycle` and `after_cycle` lists for `dk-pr-fix` and `dk-pr-review`. Entries require a stable `id` and original `instructions`, with optional `skill` and object `options`. Without a skill, the workflow agent carries out the instructions. Each phase inherits separately across user, upstream and repository scopes; a supplied list replaces that phase, and `[]` disables it. Defaults are empty. Selected hook skills also participate in availability preflight. See [cycle hooks](cycle-hooks.md) for examples, exact boundaries and retained task receipts. Hooks remain within the invoking workflow's permissions.

`finalizers` configures the ending of `dk-pr-fix`: qualified `/skill/PLUGIN:SKILL` strings run in order, and nested nonempty lists run concurrently with a barrier before the next group. Each skill appears once. External installed skills are supported. An explicit scoped list replaces inherited finalizers; `[]` disables them. Without an explicit list, effective cleanup, Decisions, description, reply, CI and publisher roles select the default sequential plan. See [PR fix finalizers](finalizers.md) for task receipts, dependencies, resume and reporting skipped readiness checks. Completion prompts cannot silently restore removed actions.

Before work, resolve every selected installation through native tools, including optional reviewers. Pass a JSON array of resolved skill identity strings to `preflight --available FILE.json`. Require `ready:true` in the returned JSON. Missing selections return `ready:false` and `missing_selected_skills` with exit 0; command success alone does not establish availability. Resolve ambiguous installations before supplying the list. Missing or ambiguous installations block work until installed or explicitly deselected. Save resolved resource paths and options. Read each skill's guidance when invoking it. Preflight proves availability only. Each skill owns its options, internal passes and completion report.

Preflight defaults to `--workflow fix` and includes selected finalizer identities after removing the `/skill/` prefix. Use `--workflow review` for incoming or standalone review; it does not require fix-only finalizers. Both modes still validate the other configured skill selections.

Place the global `--upstream HOST/OWNER/REPO` option before `show` or `preflight` when inheriting those settings. Pass the same upstream identity to `scripts/pr_route.py --pr PR_URL --upstream HOST/OWNER/REPO` so routing uses the same personal accounts.

Use `dk-config` for workflow settings and bundled [PR policies](pr/configuration.md). Scoped implementation and review preference requests route to `dk-preferences` under [the preference contract](preferences.md). Preferences have a separate shared personal/repository catalog; saved rules remain separate and are not converted. The settings helpers keep their existing private stores, schemas and precedence. Selected external skills own their settings. Renaming a skill does not update saved selections, clear work or move state. Update explicitly selected old identities through `dk-config`.

Use `scripts/pr_config.py commit-policy show|set|reset` through `dk-config` to select a private global or repository [commit policy](commit-policy.md). A policy is a saved prompt or a native skill that returns instructions for commit grouping and messages. Repository and upstream settings replace the entire inherited policy. The default retains existing cleanup and message behavior.

`identities` lists additional personal accounts as `{"host":"github.com","id":123}`. Verify each account on its host before saving its immutable numeric id. Each scope replaces the list from lower scopes. The authenticated account needs no saved entry. Never compare numeric account ids across hosts.

`incoming_review` merges each setting across user, upstream and repository scopes. Defaults are `{"post":true,"approve":false,"merge":false,"merge_method":"squash"}`. Invoking `dk-pr-review` permits posting verified findings under effective policy. Approval and merging each require their own enabled setting or exact user instruction. Narrower instructions, including analysis-only review, override saved permission.

Supported merge methods are `squash` and `merge`. Automatic merges require current successful checks and satisfied repository review and merge requirements. Configure only requested actions. Enabling merges does not enable posting an approval. The fixed iteration limits also apply to incoming review refreshes and retries.

The separate `dk-architecture` package owns architecture settings. Workflows do not invoke it automatically or require unrelated documentation conflicts to be resolved. Applicable repository guidance and explicit architecture rules still govern tasks and reuse. Record dispositions for relevant findings. Incomplete selected-skill results remain incomplete.
