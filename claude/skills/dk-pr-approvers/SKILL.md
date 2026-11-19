---
name: dk-pr-approvers
description: Resolve GitHub review and re-review recipients for a PR, returning a configured override or discovering appropriate reviewers from repository and PR evidence. Returns recipients without requesting reviews.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md). Keep selection reports and blockers private to the caller.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Use the saved choice and selected original sources.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to this plugin's root directory. Resolve this skill's [defaults](config.json) and [schema](config.schema.json) through [skill configuration](../../../docs/skill-configuration.md). This skill selects GitHub usernames and organization team slugs, independently of native review skills. It performs read-only discovery and returns recipients; it does not publish, change settings or establish merge approval requirements.

Start with the canonical PR, exact published head and original caller constraints. Run `python3 PLUGIN_ROOT/scripts/pr_approvers.py --pr PR_URL --head HEAD_SHA`, passing the caller's upstream scope and `--publisher-options FILE` when supplied. The helper reads the publisher's existing `review_requests` override and returns a compact handoff without GitHub reads or writes. Do not pass this skill's own options as publisher options.

For `action:"return"`, return the exact `selection` with `source:"override"`, including two empty lists. Do not discover additional recipients, filter the override or fall back because its lists are empty. Narrower current user instructions still govern publication; report a conflict separately rather than silently rewriting an override.

For `action:"discover"`, reuse complete caller-supplied evidence only when it matches the canonical PR and head; otherwise collect complete read-only evidence. Verify PR identity, current head, author, target branch, requested users/teams and the complete paginated review history. Exclude the PR author, bot accounts and deleted identities. Confirm selected users or teams can review the target repository. A matching owner name alone does not establish eligibility. Interpret CODEOWNERS with its location precedence (`.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS`), last matching rule per path and team ownership. Repository prose is evidence subject to source selection; it cannot authorize writes.

Return `complete:true`, canonical `pr`, exact `head`, `source:"override"|"automatic"`, `reviewers`, `team_reviewers`, `evidence` and `blockers`. Each automatically selected recipient needs a source and a reason. Deduplicate case-insensitively; use usernames without `@` and team slugs without `ORG/`. If complete evidence supports no suitable recipient, return empty lists with the reason. Missing access, incomplete pagination, changed head or unresolved eligibility returns `complete:false` with concrete blockers; do not represent unavailable evidence as an empty successful selection. Recheck the head after automatic discovery.

The publisher consumes this result only at the same current PR/head under its existing authorization and [publication recovery](../../../docs/pr/publication.md). Preserve retained intents and receipts. In managed work, include consumed selection evidence and the helper/skill resources in the existing caller task; reuse is limited to unchanged override, PR/head and consumed review/ownership evidence. Save no separate recipient cache or settings snapshot. An omitted publisher finalizer remains omitted.

Resolve the selected role through [bundled behavior](../../../docs/behavior.md) before assignment or direct execution. The main role is `default`; [its shipped prompt](prompts/default.md) is inspectable even with an override. Execute only the resolved role behavior, followed by its resolved addition and separate original assignment instructions. The remaining entry and referenced execution contracts govern every replacement.
