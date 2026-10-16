---
name: dk-pr-address-reviews
description: "Deterministically fetch and address GitHub pull-request feedback in inline threads, review bodies, and general comments. Use when asked to handle, fix, resolve, or reply to PR review comments; implement and verify fixes locally, and push, reply, or resolve on GitHub only when explicitly requested."
---

Use this Codex session and native subagents when available. Use the loaded skill location to resolve the plugin root; do not depend on the working directory.

Before work, read [the lifecycle procedure](../../../common/lifecycle.md), then run its entry command with `--host codex`. Retain its returned token and follow its structured action. Always run its completion command with that token in a finalization step, including on failure or interruption.

Read [the shared procedure](../../../runtime/dk-pr-address-reviews/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
