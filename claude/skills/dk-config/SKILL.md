---
name: dk-config
description: Manage workflow and PR settings, including reviewers, rules, drafting, splitting, CI exceptions, identities and review completion policies.
---

Resolve PLUGIN_ROOT from this installed skill location. Read [workflow configuration](../../../docs/configuration.md) for reviewer selection, skill roles, personal identities and incoming-review policies. Use `scripts/config.py` for these settings.

Read [PR configuration](../../../docs/pr/configuration.md) and use `scripts/pr_config.py` for rules, drafting, completion prompts, size/splitting policies and CI exceptions. Use `dk-ci-flakes` for saved flake evidence. Preserve existing private stores, scope precedence, original instructions and per-PR choices; changing skill names does not authorize resetting settings or moving retained state. External domain settings remain owned by the selected external skill. Architecture configuration belongs to the independent dk-architecture package.

Resolve additional personal accounts against their own GitHub host and save immutable numeric account ids. Explain the exact repository/user scope before changing automatic posting, approval or merging settings. Enable only the actions the user requested; do not infer approval or merge permission from permission to post findings. Retain existing values for unrelated settings.
