---
name: dk-asd-ste100
description: Edit changed PR prose with best-effort ASD-STE100 guidance and explicit verification limits.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before workflow configuration consumption, follow [automatic configuration upgrades](../../../docs/configuration-upgrades.md). Resume the original workflow with its permissions after an upgrade; ordinary baseline reads require no delegation.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to the installed plugin root. Resolve [skill-owned configuration](../../../docs/skill-configuration.md) with [explicit defaults](config.json) and [option schema](config.schema.json). Use the current native session and supported native tools. Follow [prose cleanup](../../../docs/prose-cleanup.md), including its deterministic handoff and managed recovery. Missing native execution leaves the pass incomplete.

Edit only prose introduced or changed by the PR: docstrings, comments and documentation. Decide which candidate text is prose from the supplied full diff or delta. Allow minimal adjacent edits necessary for coherence. Preserve technical meaning, requirements, conditions, exceptions and established terminology. Preserve executable examples, identifiers, commands, links, machine directives (including suppression comments), markup and docstring structure, parameters and return sections. Do not alter behavior or reformat unrelated text. Leave ambiguous wording unchanged and explain the ambiguity privately.

Start with the [official ASD-STE100 site](https://www.asd-ste100.org/) and [official explanation](https://www.asd-ste100.org/about_STE.html); use an available official specification or supplied authoritative reference for individual rule and dictionary checks. Retain only consumed source references in managed inputs/resources, following the shared contract. If access, dictionary entries or rule verification are unavailable, continue the best-effort editing pass and explicitly list what could not be verified. Do not claim full ASD-STE100 compliance, invent rule numbers or reproduce the specification or dictionary. Missing reference verification alone does not make the editing pass incomplete.

Validate every edit against the source meaning and protected syntax; run applicable documentation, docstring or repository checks. Return explicit completion, changed locations, validation evidence, unresolved ambiguities and reference-verification limits. A pass with no eligible prose returns a sourced no-op assessment. Supply the prepared checklist and the `prose_cleanup` input/output references described in the shared contract. Commit and publish only through existing authorized workflow stages; this skill grants no additional permission.

Resolve the selected role through [bundled behavior](../../../docs/behavior.md) before assignment or direct execution. The main role is `default`; [its shipped prompt](prompts/default.md) is inspectable even with an override. Execute only the resolved role behavior, followed by its resolved addition and separate original assignment instructions. The remaining entry and referenced execution contracts govern every replacement.
