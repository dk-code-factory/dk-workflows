---
name: dk-plain-english
description: Edit changed PR prose in clear, direct technical English.
---

Read and obey [private workflow reporting](../../../docs/private-reporting.md), the paramount package-wide rule for public text and private user reports.

Before repository guidance reads or handoffs, follow [repository instruction sources](../../../docs/instruction-sources.md). Preserve host instruction priority.

Persist workflow state only when it reduces total API dollar cost, including the next run in a new session; follow [state admission](../../../DESIGN.md#objective-and-state-admission).

Set PLUGIN_ROOT to the installed plugin root. Resolve [skill-owned configuration](../../../docs/skill-configuration.md) with [explicit defaults](config.json) and [option schema](config.schema.json). Use the current native session and supported native tools. Follow [prose cleanup](../../../docs/prose-cleanup.md), including its deterministic handoff and managed recovery. Missing native execution leaves the pass incomplete.

Edit only prose introduced or changed by the PR: docstrings, comments and documentation. Decide which candidate text is prose from the supplied full diff or delta. Allow minimal adjacent edits necessary for coherence. Preserve technical meaning, requirements, conditions, exceptions and established terminology. Preserve executable examples, identifiers, commands, links, machine directives (including suppression comments), markup and docstring structure, parameters and return sections. Do not alter behavior or reformat unrelated text. Leave ambiguous wording unchanged and explain the ambiguity privately.

Favor short sentences, familiar words, active voice and explicit references. Name the actor when the source establishes it. Split long sentences without losing logical connections. Keep necessary technical terms and precise modal verbs. Do not invent an actor, strengthen a recommendation into a requirement, remove a qualifier or simplify a technical distinction away.

Validate every edit against the source meaning and protected syntax; run applicable documentation, docstring or repository checks. Return explicit completion, changed locations, validation evidence, unresolved ambiguities and reference-verification limits. A pass with no eligible prose returns a sourced no-op assessment. Supply the prepared checklist and the `prose_cleanup` input/output references described in the shared contract. Commit and publish only through existing authorized workflow stages; this skill grants no additional permission.
