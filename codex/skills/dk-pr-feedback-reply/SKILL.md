---
name: dk-pr-feedback-reply
description: Reply to affected PR feedback and resolve fully answered inline threads when authorized.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read the original thread, comment or review, plus current code and validation. Reply only with a supported answer. Use `scripts/publish.py` for inline replies and top-level responses. Include the original source URL in each top-level response. Save the actor, source id, head and exact body. Reconcile pending writes before retrying.

Resolve only fully answered inline threads. Use the `resolve-thread` publisher with the thread node id and the database id of your verified posted reply. Top-level comments cannot be resolved. Return the original source, reply URL and resolution evidence.
