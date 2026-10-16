---
name: dk-issue-description
description: Draft, create, or update repository-native GitHub issues from issue templates and maintainer conventions. Use when asked to write, file, post, create, update, or rewrite an issue, bug report, or feature request. Skip only when final text must be used verbatim.
---

Use this Codex session and native subagents when available. Use the loaded skill location to resolve the plugin root; do not depend on the working directory.

Before work, read [the lifecycle procedure](../../../common/lifecycle.md), then run its entry command with `--host codex`. Retain its returned token and follow its structured action. Always run its completion command with that token in a finalization step, including on failure or interruption.

Read [the shared procedure](../../../runtime/dk-issue-description/procedure.md) and perform the requested workflow. Resolve paths beginning with `PLUGIN_ROOT/` against this plugin's root. Preserve the shared procedure's validation and publication guards.
