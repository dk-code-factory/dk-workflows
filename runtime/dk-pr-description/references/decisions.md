# PR decision record

Use a bounded `## Decisions` section in the PR body. One bullet per settled material choice:

- **Choice:** outcome. Reason: short rationale. Source: issue, review thread, or user-provided link when available.

Keep at most the decisions needed to understand current behavior, compatibility, architecture, or scope. Consolidate duplicates without changing their meaning. Preserve the section when redrafting or updating. If an answer is needed to choose among materially different options, ask once; after the answer, update a local Decisions draft before implementing dependent changes. Edit the remote PR body only when the user requests that update. Do not manufacture a source URL for an in-chat answer.

Before finalizing new or explicitly revised entries, follow [the decision language review](../../dk-pr-decisions/references/language.md), including its scripted three-pass limit. Preserve existing published entries unless the user authorizes rewriting them.

Treat collapsed `dk-pr-decisions` history blocks as superseded evidence, not active choices. Preserve them verbatim when editing the surrounding description. For an individual choice that supersedes an existing decision, use [dk-pr-decisions](../../dk-pr-decisions/procedure.md) `replace --id` followed by `publish` when the description update is authorized; refresh the body before further edits. Whole-section edits must retain history and the decisions it references.
