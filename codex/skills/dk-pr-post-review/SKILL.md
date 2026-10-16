---
name: dk-pr-post-review
description: Post a completed GitHub pull-request review as inline comments without a summary unless explicitly requested. Use a separate skill for replies to existing review threads.
---

Use this Codex session and native subagents when available. Use the loaded skill location to resolve the plugin root; do not depend on the working directory.

Before work, read [the lifecycle procedure](../../../common/lifecycle.md), then run its entry command with `--host codex`. Retain its returned token and follow its structured action. Always run its completion command with that token in a finalization step, including on failure or interruption.

Read [the shared procedure](../../../runtime/dk-pr-post-review/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
