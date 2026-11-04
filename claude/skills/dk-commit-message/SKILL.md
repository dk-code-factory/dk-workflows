---
name: dk-commit-message
description: Draft or revise Git commit messages using the changes and repository conventions.
---

Set PACKAGE_ROOT to this plugin's root directory. Follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read the diff, repository guidance, commit template and recent human commits. Match their style. Preserve authorship and source records. Limit each message line to 80 characters. Return the reviewed message and sources. Commit only when requested.

Use [the field conventions](../../../runtime/dk-commit-message/references/field-schema.md) when repository formatting rules leave a choice open.
