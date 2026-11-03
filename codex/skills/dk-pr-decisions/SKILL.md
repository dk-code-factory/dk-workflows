---
name: dk-pr-decisions
description: Collect, reconcile and explicitly publish settled PR decisions with source references.
---

Resolve PACKAGE_ROOT from this installed skill location. This bundled PR skill can be invoked directly and needs no workflow lifecycle, preparation or invocation tokens. Read [shared inputs, results and publication](../../../docs/pr/contracts.md). Read complete PR discussion, user instructions and current Decisions. Separate settled choices from suggestions, open questions and generated progress. Retain exact sources; unresolved material scope choices require user input. Propose evidence-backed choices without pretending they are accepted. Review language using runtime/dk-pr-decisions/references/language.md for at most three passes; apply its language criteria directly without invoking an old helper. Update only the Decisions section when authorized using scripts/publish.py; preserve unrelated title/body text. Return structured decisions, open questions, sources, and original instruction snapshots.

For optional supplied snapshots, read [shared PR evidence](../../../docs/pr/supplied-evidence.md). Validate the handoff against the assigned canonical PR/head, use its complete analysis view and preserve exact source-backed instructions and dispositions. Standalone calls without supplied evidence still collect complete stable snapshots. Freshly verify discussion and current instructions before accepting or recording results; unchanged head or artifact integrity is insufficient. Keep all publisher and final live completion checks.
