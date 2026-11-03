---
name: dk-pr-reply-review
description: Reply to affected PR feedback and resolve verified answered inline threads when authorized.
---

Resolve PACKAGE_ROOT from this installed skill location. This bundled PR skill can be invoked directly and needs no workflow lifecycle, preparation or invocation tokens. Read [shared inputs, results and publication](../../../docs/pr/contracts.md). Read the original thread or source comment/review and current code/validation. Reply only when the answer is supported. Use scripts/publish.py for inline replies and source-linked top-level responses; include the original source URL in top-level response text. Capture actor, source id, head and exact body. Reconcile pending writes before another attempt. Use the resolve-thread publisher with the thread node id and your verified posted reply database id to resolve fully answered inline threads; top-level comments are not resolvable. Return original source, posted reply URL and resolution evidence.
