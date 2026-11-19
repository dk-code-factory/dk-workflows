---
name: dk-commit-message
description: Draft or revise Git commit messages using the changes and repository conventions.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Follow all discovered repository instructions by default, or the saved explicit source override; preserve host instruction priority. Do not ask for a source choice or require onboarding to use the default.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Resolve configuration for `dk-workflows:dk-commit-message` through [skill-owned configuration](../../../docs/skill-configuration.md) before work, including scoped and invocation-local options. This skill owns its [explicit defaults](config.json) and [option schema](config.schema.json). Preserve original additional instructions alongside the governing assignment.

Follow [local commit preparation](../../../docs/pr/commit-preparation.md) before selecting the diff, including existing staged and unstaged hunks. Message-only requests return the proposed scope and message without changing Git.

Resolve the selected role through [bundled behavior](../../../docs/behavior.md) before assignment or direct execution. The main role is `default`; [its shipped prompt](prompts/default.md) is inspectable even with an override. Execute only the resolved role behavior, followed by its resolved addition and separate original assignment instructions. The remaining entry and referenced execution contracts govern every replacement.

Explicit verified commit-policy message instructions and required repository conventions govern stylistic defaults. Preserve authorship and source records. Return the reviewed message, policy and sources. Commit only when requested.
