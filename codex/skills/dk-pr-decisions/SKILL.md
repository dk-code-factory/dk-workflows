---
name: dk-pr-decisions
description: Collect, reconcile and explicitly publish settled PR decisions with source references.
---

Resolve PACKAGE_ROOT from this skill’s installation and follow [the shared PR task contract](../../../docs/pr/contracts.md).

For analysis inputs and live verification, follow [shared PR evidence](../../../docs/pr/supplied-evidence.md).

Read complete PR discussion, user instructions and current Decisions. Separate settled choices from suggestions, open questions and generated progress. Retain exact sources; unresolved material scope choices require user input. Propose evidence-backed choices without pretending they are accepted. Review language using runtime/dk-pr-decisions/references/language.md for at most three passes; apply its language criteria directly. Update only the Decisions section when authorized using scripts/publish.py; preserve unrelated title/body text. Return structured decisions, open questions, sources, and original instruction snapshots.
