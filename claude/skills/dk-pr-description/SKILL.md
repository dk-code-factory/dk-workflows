---
name: dk-pr-description
description: Draft or update PR titles and descriptions using the diff, templates and agreed scope.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read the complete diff, PR template, linked issue, maintainer conventions and current Decisions. Describe the resulting behavior and validation. Preserve Decisions and unrelated body sections. Make the description match the agreed scope before review.

Draft locally and review the text. Publish authorized edits with `scripts/publish.py` after checking the current head, title and body. Follow [the decision record](../../../runtime/dk-pr-description/references/decisions.md) when handling Decisions.

Use [the field conventions](../../../runtime/dk-pr-description/references/field-schema.md) when repository formatting rules leave a choice open.
