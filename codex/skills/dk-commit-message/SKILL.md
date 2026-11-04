---
name: dk-commit-message
description: Draft or refine repository-native Git commit messages from actual changes and maintainer conventions.
---

Resolve PACKAGE_ROOT from this skill’s installation and follow [the shared PR task contract](../../../docs/pr/contracts.md).

Read the diff, repository guidance, commit template and recent human commits. Match established style and preserve authorship/provenance. Keep every message line at most 80 characters. Return the reviewed message and sources; commit only when requested.

Use [the field conventions](../../../runtime/dk-commit-message/references/field-schema.md) when repository evidence leaves a formatting choice open.
