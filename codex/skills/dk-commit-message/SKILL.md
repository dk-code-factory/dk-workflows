---
name: dk-commit-message
description: Draft or revise Git commit messages using the changes and repository conventions.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read the diff, repository guidance, commit template and recent human commits. When supplied by cleanup, apply the verified [commit policy](../../../docs/commit-policy.md) message instructions. Explicit policy instructions replace built-in stylistic defaults and historical style preferences; required repository conventions and current user constraints still apply. Preserve authorship and source records. Return the reviewed message, policy and sources. Commit only when requested.

Without explicit message instructions, match repository style, limit each message line to 80 characters and use [the field conventions](../../../runtime/dk-commit-message/references/field-schema.md) when repository formatting rules leave a choice open.
