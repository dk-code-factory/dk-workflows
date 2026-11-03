# Workflow configuration and migration

Use `python3 PLUGIN_ROOT/scripts/config.py --repository HOST/OWNER/REPO show|set|reset|preflight|migrate`. `set --file SETTINGS.json --scope repo|user` replaces one scope. `show` merges user, optional `--upstream HOST/OWNER/REPO`, then repository settings. Skill roles inherit individually; ordered reviewer lists replace. Files are private under `${XDG_CONFIG_HOME:-~/.config}/dk-workflows/v2/`.

```json
{
  "reviewers": [{"skill":"code-review-ad:code-review-ad", "required":true, "options":{}}],
  "skills": {"feedback":"dk-pr-skills:dk-pr-address-reviews"},
  "domain_settings": {"dk-pr-skills":"existing private domain settings"}
}
```

There is no default reviewer. Default domain roles select dk-pr-skills skills; users may select other installed skills. Resolve all selected skills through native tools before work starts; supply the resolved identity list to `preflight --available FILE.json`. Missing selected skills are blockers, including optional selected reviewers; they must be explicitly deselected or installed. The helper never discovers hosts, profiles or registries. A selected skill owns how its options are interpreted.

Only skills, required reviewers, fixed iteration limits and domain-setting references belong here. For rules, drafting, partition thresholds/preferences, CI exceptions, flakes or completion prompts, invoke `dk-pr-skills:dk-pr-config` (or the configured owner). For architecture catalogs/rules/autoapproval, invoke the independent dk-architecture skills. Configuration delegation does not integrate architecture into workflows.

`migrate --reviewers V1_REVIEWERS.json --drafting V1_DRAFTING.json` produces a reviewable mapping and proposal; it does not change private settings. Preserve selected external skill identities. Removed host/profile/command execution bindings need explicit native-skill selection; the migration reports them unresolved. It maps old DK PR skills to `dk-pr-skills:` and architecture skills to `dk-architecture:`. Drafting/rules/completion belong to domain configuration. Existing PR-size, per-PR partition and CI-exception stores retain their paths under their new owner; do not reset opt-outs or explicit exceptions. The architecture package preserves its existing private catalogs.

Review the mapping, resolve unresolved bindings and use `set` to apply workflow settings. Domain configuration owns import of its values and retains original user instructions and sources. Never copy v1 review caches. Active v1 runs must finish through their already retained v1 runtime; keep that installation and invocation receipt available until they stop. v2 uses its own state namespace and cannot resume or silently convert v1 runs.
