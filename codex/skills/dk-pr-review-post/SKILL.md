---
name: dk-pr-review-post
description: Publish verified inline PR findings when authorized; report workflow blockers privately.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Resolve configuration for `dk-workflows:dk-pr-review-post` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Read original review results, verification dispositions, and the current head and diff. Publish only completed, verified findings. Each new inline finding must map to a changed line and preserve its evidence. Historical follow-ups use the original source route and current evidence under the linked follow-up procedure. An incomplete review may post independently confirmed findings as `COMMENT`; report remaining coverage and verification blockers privately to the invoking user. Withhold findings whose evidence is insufficient.

Post only when explicitly requested or authorized by the invoked incoming review workflow. Local analysis alone does not authorize publication. Use `scripts/publish.py` with kind `post-review` for new findings and the exact reviewed payload. Eligible historical follow-ups use its existing `reply-inline` or source-linked `reply-comment` kinds through the linked procedure; do not add them again as new inline findings. Approval requires separate authorization and completed clean coverage. Never approve the authenticated account's own PR.

Save the operation path. After uncertainty, reconcile actor, head, body and comment identities. Use `blocking_event` and `nonblocking_event` for completed verified findings; partial reviews always use `COMMENT`. Add a substantive repository summary only when explicitly requested or `include_summary` is enabled. These options never authorize `APPROVE`. Keep the general body empty otherwise; never publish operational scope, coverage blockers or execution limitations. Do not claim a partial review is clean.

Apply [finding follow-ups](../../../docs/pr/finding-followups.md) to historical reports from bots, people and earlier runs. Independent current-head verification precedes any reminder; after at least two new PR commits since the latest substantive request, use a targeted original-thread or source-linked reply when authorized. Recent equivalent requests, completed fixes, resolved/rejected/uncertain and explicitly accepted/deferred findings receive no reminder. Formatting-only bot-summary edits retain each unchanged finding's age. Use deterministic inspection/planning and original publication journals; age grants neither posting permission nor clean-review readiness.
