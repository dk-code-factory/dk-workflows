# Workflow configuration

Use `python3 PLUGIN_ROOT/scripts/config.py --repository HOST/OWNER/REPO show|set|reset|preflight`. `set --file SETTINGS.json --scope repo|user` replaces one scope. `show` merges user, optional `--upstream HOST/OWNER/REPO`, then repository settings. Skill roles inherit individually; ordered reviewer lists replace. Files are private under `${XDG_CONFIG_HOME:-~/.config}/dk-workflows/v2/`.

```json
{
  "reviewers": [{"skill":"code-review-ad:code-review-ad", "required":true, "options":{}}],
  "skills": {"feedback":"dk-workflows:dk-pr-feedback-address"},
  "domain_settings": {"dk-workflows":"private domain settings"}
}
```

There is no default reviewer. Default PR roles select bundled skills; users may select other installed skills. The `run_reviewers`, `read_feedback` and `post_review` roles select the coordinator, feedback collector and review publisher. Partition and cleanup select separate `dk-pr-split` and `dk-pr-commits` skills. Before work starts, resolve every selected installation through native tools, including optional reviewers, and supply the identity list to `preflight --available FILE.json`. Missing or ambiguous installations block work until installed or explicitly deselected. Retain resolved resource paths and options; load detailed guidance when invoking each skill. Installation preflight establishes availability only. Each skill owns its options, internal passes and completion report.

`dk-config` is the single configuration skill for workflow settings and bundled [PR policies](pr/configuration.md). The two helpers retain their existing private stores, schemas and precedence. External domain-setting references remain with their selected owners. Renaming a skill does not rewrite saved custom selections, clear retained work or move state; update explicitly selected old identities through `dk-config`.

`identities` is a list of additional personal accounts stored as `{"host":"github.com","id":123}`. Resolve each account on its own host before saving its immutable numeric id. This list replaces at each scope; the authenticated account is always recognized without a saved entry. Host-local account ids must never be compared across hosts.

`incoming_review` merges individual settings across user/upstream/repository scopes. Defaults are `{"post":true,"approve":false,"merge":false,"merge_method":"squash"}`. Posting verified findings is part of an invoked `dk-pr-review` workflow. Approval and merging require separate enabled settings or exact user instructions. A narrower invocation, including analysis-only review, overrides saved permission. Supported merge methods are `squash` and `merge`; automatic merges require current successful checks and satisfied review/merge requirements. Configure only requested actions; enabling merges does not implicitly enable posting an approval. Fixed iteration limits also bound incoming review refreshes/retries.

Architecture settings belong to the independent dk-architecture package. Workflows do not invoke architecture automatically or require unrelated documentation conflicts to be resolved. Applicable repository guidance and explicit architecture rules still govern tasks and review reuse. Relevant findings require disposition; incomplete selected-skill results remain incomplete.
