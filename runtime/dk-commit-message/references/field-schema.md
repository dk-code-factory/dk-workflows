# Commit message field schema

Use repository-specific names and layout. Preserve these meanings.

## Required meanings

| Field | Question answered |
|---|---|
| Summary | What changed, in one distinguishing line? |
| Motivation | Why was the change needed, or what constraint shaped it? |
| References | Which issue, ticket, or PR belongs to it? Omit when none exists. |

Motivation may collapse into the subject only when repository history permits one-line messages for a change this small. Otherwise the body must add information unavailable from the diff.

## Repository-conditional meanings

Include only with evidence:

- Testing: repository commits consistently record verification.
- Sign-off: contributor rules, DCO, or hooks require `Signed-off-by`.
- Breaking change: the repository uses Conventional Commits and this change is breaking.
- Human co-authors: real pair authorship exists. Never attribute a tool.

## Conservative fallback

When no style evidence exists:

- Use an imperative, sentence-case subject without a period, preferably at most 72 characters.
- Add a short body only when it supplies motivation or an important consequence.
- Wrap prose near 72 characters.
- Put a real issue reference at the end; omit it when unknown.
- Use plain text, not Markdown headings.

## Reject

- Generic subjects such as `fix bug`, `update code`, or `misc changes`.
- Diff narration, unsupported claims, invented issue numbers, or invented test results.
- Generated-by banners, robot emoji, or tool co-author trailers.
- Web permalinks when a terminal-friendly `path:line` reference is enough.
