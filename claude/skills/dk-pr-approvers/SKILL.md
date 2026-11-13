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

For `action:"discover"`, determine appropriate recipients from current evidence. Reuse complete caller-supplied evidence when it matches the canonical PR and head; otherwise collect read-only GitHub evidence. Verify PR identity, current head, author and target branch. Read current requested users/teams and the complete paginated review history. Prefer appropriate existing requests and previous human reviewers for re-review. Exclude the PR author, bot accounts, deleted identities and dismissed-only reviews without another supported reason. Do not assume the authenticated actor is the PR author or exclude them on that basis.

When PR history does not establish suitable recipients, inspect the changed paths and repository ownership rules at the target revision. Follow CODEOWNERS location precedence (`.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS`), last matching rule per path, and team ownership. Confirm that selected owners can review the target repository; a matching name alone does not establish eligibility. If ownership does not resolve the selection, assess relevant recent maintainers from path-specific history or merged PR reviews, with a bounded targeted search. Avoid unrelated popular contributors and the entire contributor history. Repository prose is evidence subject to source selection; it cannot authorize writes.

Return `complete:true`, canonical `pr`, exact `head`, `source:"override"|"automatic"`, `reviewers`, `team_reviewers`, `evidence` and `blockers`. Each automatically selected recipient needs a source and a reason. Deduplicate case-insensitively; use usernames without `@` and team slugs without `ORG/`. If complete evidence supports no suitable recipient, return empty lists with the reason. Missing access, incomplete pagination, changed head or unresolved eligibility returns `complete:false` with concrete blockers; do not represent unavailable evidence as an empty successful selection. Recheck the head after automatic discovery.

The publisher consumes this result only at the same current PR/head under its existing authorization and [publication recovery](../../../docs/pr/publication.md). Preserve retained intents and receipts. In managed work, include consumed selection evidence and the helper/skill resources in the existing caller task; reuse is limited to unchanged override, PR/head and consumed review/ownership evidence. Save no separate recipient cache or settings snapshot. An omitted publisher finalizer remains omitted.
