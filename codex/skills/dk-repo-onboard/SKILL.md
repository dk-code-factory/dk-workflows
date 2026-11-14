---
name: dk-repo-onboard
description: Onboard a repository by discovering instruction sources and reporting all discovered rules, settings and gaps in a structured inventory. Use for requested repository onboarding or an explicit discovery refresh.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Follow [repository instruction sources](../../../docs/instruction-sources.md) before adopting repository guidance; preserve host instruction priority. Follow all discovered repository instructions by default, or the saved explicit source override. Do not ask which instructions to follow, save a default choice or require configuration-agent delegation for discovery.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to this plugin's root directory. Resolve this skill's scoped and invocation-local options through [skill configuration](../../../docs/skill-configuration.md), preserving original additional instructions. This skill owns its [defaults](config.json) and [schema](config.schema.json).

## Onboard

Use the source helper's live `show` inventory with the caller's checkout and repository identity. The default is `all` with empty include/exclude lists, including when no policy is saved or a prior override was reset. Reuse an explicit saved override. Ordinary workflows read selected sources and settings directly without running onboarding or asking for a source choice.

For requested onboarding or an explicit refresh, inspect the current source text and produce the report below directly in the running session. Discovery is read-only and needs no configuration request or delegated configuration agent. Change an override only when requested, through [dk-config](../dk-config/SKILL.md) and its existing admission/recovery contract. Preserve unrelated settings, caches, results and budgets.

Inspect current [workflow settings](../../../docs/configuration.md), [skill settings](../../../docs/skill-configuration.md), [PR settings](../../../docs/pr/configuration.md) and [preferences](../../../docs/preferences.md) with their read-only helpers for the same repository and applicable scopes. Report existing values without changing them. Onboarding does not configure reviewers, permissions or preferences, execute a PR workflow, edit repository files or publish a report.

## Structured discovery report

Return the report privately to the user after onboarding, including when a caller resumes a pending workflow. If called by another skill, return the complete report to that caller for delivery. Use the same structure for an explicit refresh or incomplete attempt. Present named sections and tables/records with the fields below; keep every discovered item, not just highlights. Use empty lists or explicit `none`/`unknown` values instead of silently omitting a category.

| Section | Required contents |
| --- | --- |
| Repository | Repository identity, checkout/Git root, applicable upstream if supplied, and completion status (`complete` or `incomplete`). |
| Source choice | Effective default or saved override; exact mode, includes and excludes from the live helper. Keep any explicit-change request/recovery references private. |
| Source inventory | One row per discovered source: original path, automation/human/custom kind, documented scope, selected/excluded/unselected status, inspected/unreadable/unsafe status and reason. Include explicitly supplied sources and known host/ancestor guidance separately, without claiming exhaustive host discovery. |
| Discovered rules | One record per distinct actionable rule: rule text or faithful paraphrase, original path and line/section, scope and conditions, and authority (`selected repository rule`, `evidence only`, `direct user instruction`, `host instruction` or `saved preference`). Cover every inspected source, including excluded sources as evidence only. Preserve differing scopes and exceptions when grouping duplicates; retain all provenance. Descriptive text without a rule belongs in repository facts. |
| Repository facts | Other discovered facts, such as documented build/test commands, layout, contribution/review conventions and ownership: fact, source reference, scope and whether documented or inferred. Commands discovered in excluded files remain evidence only; discovery does not authorize executing them. |
| Existing configuration | Effective settings and scoped preferences inspected: setting key or preference ID, value/instructions, scope, origin/provenance and active/overridden status. Cover the inspected workflow, skill and PR settings, selected skills, permissions, commit policies and applicable preferences. Cite helper provenance; do not infer unknown original intent or expose credentials. |
| Conflicts and gaps | Each conflicting rule with both sources and affected scope, settled resolution or unresolved question; every unreadable/unsafe source, missing capability, unavailable setting or uninspected area and its impact. Explain concrete host limitations and any blocked behavior. |
| Outcome | What was saved or reused, remaining choices/blockers and whether the invoking workflow can resume. Source selection is not permission for PR or publication actions. |

Read source text before describing its rules. Do not claim an unreadable or uninspected source has no rules; inventory it and mark discovery incomplete. Keep excluded findings out of governing summaries and downstream instructions. Pass selected original references and the current choice to resumed work; the user-facing report does not replace live source checks or authorize adoption.

Construct the report from current helper output and inspected evidence in memory. Do not persist a report, source snapshot or effective-settings copy. Its freshness ends when the repository identity, source choice/content, inspected settings/preferences or host context changes; refresh affected reads then. Save no default-choice record; explicit override changes retain only their existing configuration-request decisions/receipts and policy.
