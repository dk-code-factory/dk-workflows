---
name: dk-pr-reply-review
description: Reply to GitHub PR feedback in inline threads or with source-linked responses to top-level comments and review bodies. Do not submit standalone reviews or general summaries.
---

Use Claude Code native agents and tools. `${CLAUDE_PLUGIN_ROOT}` is this plugin root. Do not require Codex for native execution. When shared instructions name a skill with a dollar prefix, use the corresponding Claude native skill in this plugin namespace.

Before work, read [the lifecycle procedure](../../../common/lifecycle.md), then run its entry command with `--host claude`. Retain its returned token and follow its structured action. Always run its completion command with that token in a finalization step, including on failure or interruption.

Read [the shared procedure](../../../runtime/dk-pr-reply-review/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
