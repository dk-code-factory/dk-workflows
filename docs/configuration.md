# Workflow configuration

Use `python3 PLUGIN_ROOT/scripts/config.py --repository HOST/OWNER/REPO show|set|reset|preflight`. `set --file SETTINGS.json --scope repo|user` replaces one scope. `show` merges user, optional `--upstream HOST/OWNER/REPO`, then repository settings. Skill roles inherit individually; ordered reviewer lists replace. Files are private under `${XDG_CONFIG_HOME:-~/.config}/dk-workflows/v2/`.

```json
{
  "reviewers": [{"skill":"code-review-ad:code-review-ad", "required":true, "options":{}}],
  "skills": {"feedback":"dk-workflows:dk-pr-address-reviews"},
  "domain_settings": {"dk-workflows":"private domain settings"}
}
```

There is no default reviewer. Default PR roles select bundled skills; users may select other installed skills. Before work starts, resolve every selected installation through native tools, including optional reviewers, and supply the identity list to `preflight --available FILE.json`. Missing or ambiguous installations block work until installed or explicitly deselected. Retain resolved resource paths and options; load detailed guidance when invoking each skill. Installation preflight establishes availability only. Each skill owns its options, internal passes and completion report.

Workflow settings own skill selection, required reviewers, fixed iteration limits and domain-setting references. Route private rules, drafting, partition settings, CI exceptions, flakes and completion prompts to the configured domain owner, normally `dk-workflows:dk-pr-config`.

Architecture settings belong to the independent dk-architecture package. Workflows do not invoke architecture automatically or require unrelated documentation conflicts to be resolved. Applicable repository guidance and explicit architecture rules still govern tasks and review reuse. Relevant findings require disposition; incomplete selected-skill results remain incomplete.
